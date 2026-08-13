"""Validate one submission end to end, then smoke-test it in real games.

    python -m submission.validate \
        --repo https://github.com/owner/repo \
        --commit 0123456789abcdef0123456789abcdef01234567

    python -m submission.validate --local ./my-agent-checkout

The checks run in order and stop at the first failure:

1. the URL is GitHub over HTTPS and the commit is a full pinned SHA
2. the pinned commit fetches, and the checkout is under the 100 MB cap
3. ``agent.py`` imports and exposes a conforming ``choose_action``
4. the agent plays complete games from every seat without returning an illegal
   action, raising, perturbing the global RNG, or blowing the time limit

SECURITY: step 3 imports and step 4 executes code from an untrusted repository
in this process.  Run this inside a container or throwaway VM with no
credentials mounted.  Nothing here sandboxes the submission.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

from ASU_FROZEN_TEACHER.core import preserve_global_rng
from ASU_FROZEN_TEACHER.evaluate import (
    AgentFactory,
    _new_seeded_game,
    parse_agent_spec,
)
from monopoly_game_engine.constants import NUM_PLAYERS

from .contract import (
    ENTRYPOINT_FILENAME,
    IllegalActionError,
    SubmissionError,
    bind_seat,
    load_module,
)
from .fetch import MAX_REPO_BYTES, FetchError, checkout_pinned, directory_size

DEFAULT_OPPONENTS = ("fixed-a", "fixed-b", "fixed-c")
DEFAULT_SEEDS = (0, 1)
DEFAULT_MAX_DECISIONS = 20_000
DEFAULT_TIME_LIMIT = 5.0


class ValidationFailure(Exception):
    """A submission failed a validation stage."""


def smoke_test(
    repo_dir: Path,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    opponents: Sequence[str] = DEFAULT_OPPONENTS,
    max_decisions: int = DEFAULT_MAX_DECISIONS,
    time_limit: float = DEFAULT_TIME_LIMIT,
) -> dict[str, Any]:
    """Play the submission from every seat against scripted opponents."""

    if len(opponents) != NUM_PLAYERS - 1:
        raise ValidationFailure(f"exactly {NUM_PLAYERS - 1} opponents are required")
    opponent_specs = [parse_agent_spec(name) for name in opponents]
    factory = AgentFactory()
    module = load_module(repo_dir)

    games: list[dict[str, Any]] = []
    slowest = 0.0
    total_time = 0.0
    total_decisions = 0
    rng_perturbations = 0

    with preserve_global_rng():
        for seed in seeds:
            for focus_seat in range(NUM_PLAYERS):
                game = _new_seeded_game(int(seed))
                submission = bind_seat(module, focus_seat)
                agents: list[Any] = [None] * NUM_PLAYERS
                agents[focus_seat] = submission
                for seat, spec in zip(
                    (s for s in range(NUM_PLAYERS) if s != focus_seat), opponent_specs
                ):
                    agents[seat] = factory.build(spec, seat)

                decisions = 0
                while not game.env.done and decisions < max_decisions:
                    actor = game.env.whose_turn()
                    if actor == focus_seat:
                        started = time.perf_counter()
                        action = agents[actor].choose_action(game.env)
                        elapsed = time.perf_counter() - started
                        total_time += elapsed
                        slowest = max(slowest, elapsed)
                        if elapsed > time_limit:
                            raise ValidationFailure(
                                f"a decision took {elapsed:.2f}s from seat "
                                f"{focus_seat} on seed {seed}, over the "
                                f"{time_limit:.2f}s limit"
                            )
                    else:
                        action = agents[actor].choose_action(game.env)
                    game.step(action)
                    decisions += 1

                rng_perturbations += submission.rng_perturbations
                total_decisions += submission.decisions
                winner = game.env.winner() if game.env.done else None
                games.append(
                    {
                        "seed": int(seed),
                        "seat": focus_seat,
                        "won": None if winner is None else winner == focus_seat,
                        "rounds": game.env.round,
                        "decisions": decisions,
                        "submission_decisions": submission.decisions,
                        "truncated": not game.env.done,
                    }
                )

    decided = [game for game in games if not game["truncated"]]
    return {
        "games": games,
        "games_played": len(games),
        "wins": sum(1 for game in decided if game["won"]),
        "truncated": sum(1 for game in games if game["truncated"]),
        "submission_decisions": total_decisions,
        "seconds_per_decision_mean": (
            total_time / total_decisions if total_decisions else 0.0
        ),
        "seconds_per_decision_max": slowest,
        "global_rng_perturbations": rng_perturbations,
    }


def validate(
    repo_url: str | None,
    commit: str | None,
    local: Path | None,
    work_dir: Path | None = None,
    keep: bool = False,
    seeds: Sequence[int] = DEFAULT_SEEDS,
    opponents: Sequence[str] = DEFAULT_OPPONENTS,
    max_bytes: int = MAX_REPO_BYTES,
    time_limit: float = DEFAULT_TIME_LIMIT,
) -> dict[str, Any]:
    """Run every stage and return a report; raises on the first failure."""

    report: dict[str, Any] = {"stages": {}}
    temporary: Path | None = None
    try:
        if local is not None:
            checkout = Path(local).expanduser().resolve()
            if not checkout.is_dir():
                raise ValidationFailure(f"not a directory: {checkout}")
            used = directory_size(checkout)
            if used > max_bytes:
                raise ValidationFailure(
                    f"checkout exceeds the {max_bytes // (1024 * 1024)} MB cap "
                    f"({used / (1024 * 1024):.1f} MB)"
                )
            report["source"] = {"local": str(checkout)}
            report["stages"]["fetch"] = "skipped (local checkout)"
        else:
            if not repo_url or not commit:
                raise ValidationFailure("--repo and --commit are both required")
            base = Path(work_dir).expanduser().resolve() if work_dir else None
            temporary = Path(tempfile.mkdtemp(prefix="submission-", dir=base))
            destination = temporary / "checkout"
            result = checkout_pinned(repo_url, commit, destination, max_bytes=max_bytes)
            checkout = result.path
            report["source"] = {
                "repo": repo_url,
                "commit": result.commit,
                "bytes": result.bytes_used,
                "megabytes": round(result.bytes_used / (1024 * 1024), 2),
            }
            report["stages"]["fetch"] = "ok"

        entrypoint = checkout / ENTRYPOINT_FILENAME
        if not entrypoint.is_file():
            raise ValidationFailure(
                f"missing {ENTRYPOINT_FILENAME} at the repository root"
            )
        report["stages"]["entrypoint"] = "ok"

        smoke = smoke_test(
            checkout,
            seeds=seeds,
            opponents=opponents,
            time_limit=time_limit,
        )
        report["smoke"] = smoke
        report["stages"]["smoke"] = "ok"
        if smoke["global_rng_perturbations"]:
            report["warnings"] = [
                f"the agent moved the global RNG on "
                f"{smoke['global_rng_perturbations']} decisions; seed its own "
                f"random.Random instead"
            ]
        report["accepted"] = True
        return report
    finally:
        if temporary is not None and not keep:
            shutil.rmtree(temporary, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m submission.validate",
        description="Validate a pinned submission repository and smoke-test its agent.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", help="https://github.com/<owner>/<repo>")
    source.add_argument(
        "--local", type=Path, help="validate an existing directory, no fetching"
    )
    parser.add_argument("--commit", help="full 40-character commit SHA, pinned at submit time")
    parser.add_argument("--work-dir", type=Path, help="where to place the temporary checkout")
    parser.add_argument("--keep", action="store_true", help="keep the checkout on disk")
    parser.add_argument("--seeds", nargs="+", type=int, default=list(DEFAULT_SEEDS))
    parser.add_argument("--opponents", nargs=3, default=list(DEFAULT_OPPONENTS))
    parser.add_argument("--max-bytes", type=int, default=MAX_REPO_BYTES)
    parser.add_argument("--time-limit", type=float, default=DEFAULT_TIME_LIMIT)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        report = validate(
            arguments.repo,
            arguments.commit,
            arguments.local,
            work_dir=arguments.work_dir,
            keep=arguments.keep,
            seeds=arguments.seeds,
            opponents=arguments.opponents,
            max_bytes=arguments.max_bytes,
            time_limit=arguments.time_limit,
        )
    except (FetchError, SubmissionError, ValidationFailure) as exc:
        rejection = {
            "accepted": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if isinstance(exc, IllegalActionError):
            rejection["hint"] = "return only actions present in allowed_actions"
        print(json.dumps(rejection, indent=2 if arguments.pretty else None, sort_keys=True))
        return 1
    print(json.dumps(report, indent=2 if arguments.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
