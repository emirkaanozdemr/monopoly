from __future__ import annotations

import random
import tempfile
import textwrap
import unittest
from pathlib import Path

from ASU_FROZEN_TEACHER.evaluate import AgentFactory, parse_agent_spec, tree_sha256
from submission.contract import (
    IllegalActionError,
    SubmissionError,
    load_entrypoint,
    load_module,
    plan_injections,
)
from submission.fetch import (
    MAX_REPO_BYTES,
    FetchError,
    directory_size,
    parse_commit_sha,
    parse_github_https,
)
from submission.validate import ValidationFailure, smoke_test, validate


def write_agent(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "agent.py").write_text(textwrap.dedent(body))
    return directory


TEMPLATE = Path(__file__).resolve().parents[1] / "submission" / "template" / "agent.py"


class GitHubUrlTests(unittest.TestCase):
    def test_accepts_canonical_https_url(self):
        reference = parse_github_https("https://github.com/owner/repo")
        self.assertEqual((reference.owner, reference.repo), ("owner", "repo"))
        self.assertEqual(reference.url, "https://github.com/owner/repo.git")

    def test_strips_dot_git_suffix(self):
        self.assertEqual(parse_github_https("https://github.com/o/r.git").repo, "r")

    def test_rejects_non_https_and_foreign_hosts(self):
        for url in (
            "git@github.com:owner/repo.git",
            "ssh://git@github.com/owner/repo",
            "http://github.com/owner/repo",
            "git://github.com/owner/repo",
            "https://gitlab.com/owner/repo",
            "https://raw.githubusercontent.com/owner/repo",
        ):
            with self.subTest(url=url), self.assertRaises(FetchError):
                parse_github_https(url)

    def test_rejects_credentials_ports_and_deep_paths(self):
        for url in (
            "https://user:token@github.com/owner/repo",
            "https://github.com:8443/owner/repo",
            "https://github.com/owner",
            "https://github.com/owner/repo/tree/main",
            "https://github.com/owner/repo?ref=main",
            "https://github.com/owner/repo#frag",
            "https://github.com/../repo",
        ):
            with self.subTest(url=url), self.assertRaises(FetchError):
                parse_github_https(url)


class CommitPinTests(unittest.TestCase):
    def test_accepts_full_lowercase_sha(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(parse_commit_sha(f"  {sha}\n"), sha)

    def test_rejects_branches_tags_and_short_shas(self):
        for value in ("main", "v1.0", "0123456", "0" * 39, "0" * 41, "A" * 40, "g" * 40):
            with self.subTest(value=value), self.assertRaises(FetchError):
                parse_commit_sha(value)


class SignaturePlanTests(unittest.TestCase):
    def test_two_argument_contract_needs_no_injection(self):
        self.assertEqual(plan_injections(lambda state, allowed_actions: 0), ())

    def test_optional_parameters_are_injected_by_name(self):
        self.assertEqual(
            plan_injections(lambda state, allowed_actions, env: 0), ("env",)
        )
        self.assertEqual(
            plan_injections(lambda state, allowed_actions, env, player_id: 0),
            ("env", "player_id"),
        )

    def test_rejects_too_few_parameters(self):
        with self.assertRaises(SubmissionError):
            plan_injections(lambda state: 0)

    def test_rejects_unknown_required_parameter(self):
        with self.assertRaises(SubmissionError):
            plan_injections(lambda state, allowed_actions, oracle: 0)

    def test_allows_unknown_parameter_with_a_default(self):
        self.assertEqual(plan_injections(lambda state, allowed_actions, k=1: 0), ())


class EntrypointLoadingTests(unittest.TestCase):
    def test_module_level_function_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                def choose_action(state, allowed_actions):
                    return allowed_actions[0]
                """,
            )
            agent = load_entrypoint(repo, 0)
            self.assertEqual(agent.player_id, 0)
            self.assertEqual(agent.injections, ())

    def test_agent_class_receives_player_id(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                class Agent:
                    def __init__(self, player_id):
                        self.player_id = player_id

                    def choose_action(self, state, allowed_actions):
                        return allowed_actions[0]
                """,
            )
            agent = load_entrypoint(repo, 2)
            self.assertEqual(agent.target.__self__.player_id, 2)

    def test_agent_class_without_constructor_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                class Agent:
                    def choose_action(self, state, allowed_actions):
                        return allowed_actions[0]
                """,
            )
            self.assertEqual(load_entrypoint(repo, 1).player_id, 1)

    def test_missing_entrypoint_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SubmissionError):
                load_entrypoint(Path(directory), 0)

    def test_missing_choose_action(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(Path(directory), "VALUE = 1\n")
            with self.assertRaises(SubmissionError):
                load_entrypoint(repo, 0)

    def test_import_time_exception_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(Path(directory), "raise RuntimeError('boom')\n")
            with self.assertRaises(SubmissionError):
                load_entrypoint(repo, 0)

    def test_submission_may_import_its_own_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                from helper import pick

                def choose_action(state, allowed_actions):
                    return pick(allowed_actions)
                """,
            )
            (repo / "helper.py").write_text("def pick(actions):\n    return actions[0]\n")
            self.assertIsNotNone(load_entrypoint(repo, 0))


class _StubEnv:
    """Minimal stand-in exposing only what SubmissionAgent touches."""

    def __init__(self, allowed):
        self.allowed = list(allowed)

    def get_allowed_actions(self, pid):
        return list(self.allowed)

    def _get_state(self, pid):
        return [0.0]


class AdapterTests(unittest.TestCase):
    def _agent(self, body, player_id=0):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        repo = write_agent(Path(self.directory.name), body)
        return load_entrypoint(repo, player_id)

    def test_illegal_action_is_rejected(self):
        agent = self._agent(
            """
            def choose_action(state, allowed_actions):
                return 999
            """
        )
        with self.assertRaises(IllegalActionError):
            agent.choose_action(_StubEnv([1, 2]))

    def test_non_integer_return_is_rejected(self):
        for expression in ("'1'", "None", "True", "1.5"):
            agent = self._agent(
                f"""
                def choose_action(state, allowed_actions):
                    return {expression}
                """
            )
            with self.subTest(expression=expression), self.assertRaises(SubmissionError):
                agent.choose_action(_StubEnv([1, 2]))

    def test_raising_agent_is_wrapped(self):
        agent = self._agent(
            """
            def choose_action(state, allowed_actions):
                raise ValueError('nope')
            """
        )
        with self.assertRaises(SubmissionError):
            agent.choose_action(_StubEnv([1]))

    def test_global_rng_is_restored_and_the_violation_counted(self):
        agent = self._agent(
            """
            import random

            def choose_action(state, allowed_actions):
                random.random()
                return allowed_actions[0]
            """
        )
        random.seed(1234)
        before = random.getstate()
        self.assertEqual(agent.choose_action(_StubEnv([7])), 7)
        self.assertEqual(random.getstate(), before)
        self.assertEqual(agent.rng_perturbations, 1)

    def test_env_injection_reaches_the_agent(self):
        agent = self._agent(
            """
            def choose_action(state, allowed_actions, env, player_id):
                return env.get_allowed_actions(player_id)[-1]
            """,
            player_id=3,
        )
        self.assertEqual(agent.choose_action(_StubEnv([4, 5, 6])), 6)


class SmokeTests(unittest.TestCase):
    def test_template_agent_plays_full_games_from_every_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "agent.py").write_text(TEMPLATE.read_text())
            report = smoke_test(repo, seeds=(0,))
            self.assertEqual(report["games_played"], 4)
            self.assertEqual(report["global_rng_perturbations"], 0)
            self.assertGreater(report["submission_decisions"], 0)

    def test_validate_rejects_an_illegal_action_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                def choose_action(state, allowed_actions):
                    return 10_000
                """,
            )
            with self.assertRaises(IllegalActionError):
                validate(None, None, repo, seeds=(0,))

    def test_validate_enforces_the_size_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                def choose_action(state, allowed_actions):
                    return allowed_actions[0]
                """,
            )
            (repo / "weights.bin").write_bytes(b"\0" * 4096)
            with self.assertRaises(ValidationFailure):
                validate(None, None, repo, max_bytes=1024)

    def test_directory_size_ignores_git_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            (repo / ".git" / "blob").write_bytes(b"\0" * 5000)
            (repo / "agent.py").write_bytes(b"\0" * 100)
            self.assertEqual(directory_size(repo), 100)
            self.assertLess(directory_size(repo), MAX_REPO_BYTES)


class EvaluatorIntegrationTests(unittest.TestCase):
    def test_submission_spec_is_parsed_and_built(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(
                Path(directory),
                """
                def choose_action(state, allowed_actions):
                    return allowed_actions[0]
                """,
            )
            spec = parse_agent_spec(f"submission:{repo}")
            self.assertEqual(spec.kind, "submission")
            self.assertEqual(spec.checkpoint, repo.resolve())
            agent = AgentFactory().build(spec, 2)
            self.assertEqual(agent.player_id, 2)

    def test_missing_checkout_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_agent_spec("submission:/nonexistent/checkout")

    def test_tree_digest_is_stable_and_content_sensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = write_agent(Path(directory), "X = 1\n")
            first = tree_sha256(repo)
            self.assertEqual(first, tree_sha256(repo))
            (repo / "agent.py").write_text("X = 2\n")
            self.assertNotEqual(first, tree_sha256(repo))


if __name__ == "__main__":
    unittest.main()
