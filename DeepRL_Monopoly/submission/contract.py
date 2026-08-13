"""The entrypoint contract every submission must satisfy.

A submission repository exposes exactly one entrypoint: ``agent.py`` at its
root, defining ``choose_action(state, allowed_actions) -> int``.

``state`` is the ``STATE_DIM``-length float32 vector built by
``monopoly_game_engine.state.build_state_vector`` for the seat being asked,
and ``allowed_actions`` is the list of legal action indices for that seat
right now.  The return value must be one of ``allowed_actions``.

Two shapes are accepted, because the repository's own agents use both:

* a module-level function ``choose_action``
* a class ``Agent`` with a ``choose_action`` method, instantiated once per
  seat (with ``player_id`` if its constructor accepts one)

Beyond the two required parameters, an entrypoint may declare ``env`` and/or
``player_id``; the harness supplies them by keyword.  This is what lets an
env-reading policy such as ``ASU_SLAYER`` be submitted at all — ``state`` is a
flat vector, and the environment cannot be reconstructed from it.  Entrants who
only need the vector simply omit them and the two-argument signature stands.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import threading
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, List, Sequence

from ASU_FROZEN_TEACHER.core import preserve_global_rng

ENTRYPOINT_FILENAME = "agent.py"
ENTRYPOINT_ATTRIBUTE = "choose_action"
ENTRYPOINT_CLASS = "Agent"

REQUIRED_PARAMETERS = ("state", "allowed_actions")
INJECTABLE_PARAMETERS = ("env", "player_id")

_IMPORT_LOCK = threading.Lock()


class SubmissionError(Exception):
    """The submission does not satisfy the entrypoint contract."""


class IllegalActionError(SubmissionError):
    """The submission returned an action outside the legal set."""


def _parameters(target: Callable[..., Any]) -> list[inspect.Parameter]:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError) as exc:  # builtins, C callables
        raise SubmissionError(
            f"{ENTRYPOINT_ATTRIBUTE} must be an inspectable Python callable: {exc}"
        ) from exc
    kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    )
    return [p for p in signature.parameters.values() if p.kind in kinds]


def plan_injections(target: Callable[..., Any]) -> tuple[str, ...]:
    """Return the optional parameter names the harness must supply by keyword.

    Raises ``SubmissionError`` when the callable cannot accept
    ``(state, allowed_actions)`` positionally, or requires anything else the
    harness has no value for.
    """

    parameters = _parameters(target)
    variadic_positional = any(
        p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters
    )
    variadic_keyword = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters
    )
    positional = [
        p
        for p in parameters
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]

    if len(positional) < 2 and not variadic_positional:
        rendered = ", ".join(p.name for p in parameters) or "no parameters"
        raise SubmissionError(
            f"{ENTRYPOINT_ATTRIBUTE} must accept "
            f"({', '.join(REQUIRED_PARAMETERS)}) positionally; found: {rendered}"
        )

    injections: list[str] = []
    for parameter in parameters[2:] if len(positional) >= 2 else []:
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.name in INJECTABLE_PARAMETERS:
            injections.append(parameter.name)
        elif parameter.default is inspect.Parameter.empty:
            raise SubmissionError(
                f"{ENTRYPOINT_ATTRIBUTE} requires unknown parameter "
                f"{parameter.name!r}; the harness supplies only "
                f"{', '.join((*REQUIRED_PARAMETERS, *INJECTABLE_PARAMETERS))}"
            )
    if variadic_keyword and not injections:
        # **kwargs alone is ambiguous; supply nothing and let the entrypoint
        # work from the two required arguments.
        return ()
    return tuple(injections)


def _import_agent_module(repo_dir: Path) -> ModuleType:
    entrypoint = repo_dir / ENTRYPOINT_FILENAME
    if not entrypoint.is_file():
        raise SubmissionError(
            f"missing {ENTRYPOINT_FILENAME} at the repository root ({repo_dir})"
        )
    module_name = f"_submission_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, entrypoint)
    if spec is None or spec.loader is None:
        raise SubmissionError(f"cannot load {entrypoint}")
    module = importlib.util.module_from_spec(spec)

    # Import under the repository root so the submission's own modules resolve,
    # and serialise the whole operation because sys.path/sys.modules are global.
    with _IMPORT_LOCK:
        root = str(repo_dir)
        sys.path.insert(0, root)
        sys.modules[module_name] = module
        try:
            with preserve_global_rng():
                spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            raise SubmissionError(
                f"{ENTRYPOINT_FILENAME} raised on import: {exc!r}"
            ) from exc
        finally:
            try:
                sys.path.remove(root)
            except ValueError:
                pass
    return module


def _construct(factory: Callable[..., Any], player_id: int) -> Any:
    """Instantiate an ``Agent`` class, passing ``player_id`` when accepted."""

    try:
        parameters = _parameters(factory)
    except SubmissionError:
        parameters = []
    accepts = any(
        p.name == "player_id"
        or p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for p in parameters
    )
    try:
        return factory(player_id=player_id) if accepts else factory()
    except Exception as exc:
        raise SubmissionError(
            f"{ENTRYPOINT_CLASS}() raised during construction: {exc!r}"
        ) from exc


def load_module(repo_dir: Path) -> ModuleType:
    """Import a submission's ``agent.py`` once; bind seats from the result.

    Importing is the expensive half (an entrant may load model weights), and a
    seat-balanced evaluation binds four seats per game across many games, so
    callers should import once and call :func:`bind_seat` per seat.
    """

    return _import_agent_module(Path(repo_dir))


def bind_seat(module: ModuleType, player_id: int) -> "SubmissionAgent":
    """Bind an imported submission module to one seat."""

    holder = getattr(module, ENTRYPOINT_CLASS, None)
    if inspect.isclass(holder):
        instance = _construct(holder, player_id)
        target = getattr(instance, ENTRYPOINT_ATTRIBUTE, None)
        if not callable(target):
            raise SubmissionError(
                f"class {ENTRYPOINT_CLASS} has no callable "
                f"{ENTRYPOINT_ATTRIBUTE} method"
            )
    else:
        target = getattr(module, ENTRYPOINT_ATTRIBUTE, None)
        if not callable(target):
            raise SubmissionError(
                f"{ENTRYPOINT_FILENAME} defines neither a module-level "
                f"{ENTRYPOINT_ATTRIBUTE}() nor a class {ENTRYPOINT_CLASS}"
            )
    return SubmissionAgent(target, player_id, plan_injections(target))


def load_entrypoint(repo_dir: Path, player_id: int) -> "SubmissionAgent":
    """Import ``agent.py`` from ``repo_dir`` and bind it to a single seat."""

    return bind_seat(load_module(repo_dir), player_id)


def _as_action_index(value: Any) -> int:
    if isinstance(value, bool) or not hasattr(value, "__index__"):
        raise SubmissionError(
            f"{ENTRYPOINT_ATTRIBUTE} must return an integer action index, "
            f"got {value!r} ({type(value).__name__})"
        )
    return int(value.__index__())


def _env_fingerprint(env) -> tuple:
    """A cheap summary of every piece of game state a submission could
    profitably mutate.  ``env`` is handed to entrants raw (it cannot be
    reconstructed from the state vector), so the harness must be able to
    detect a submission that edits cash, ownership, buildings, debt, or the
    trade queue instead of playing (SLAYER_REVIEW.md 6)."""

    players = tuple(
        (
            int(p.cash),
            bool(p.bankrupt),
            int(p.position),
            bool(p.in_jail),
            int(p.jail_turns),
            bool(p.gooj_card),
        )
        for p in env.players
    )
    deeds = tuple(
        (
            -1 if prop.owner is None else int(prop.owner),
            int(prop.houses),
            bool(prop.mortgaged),
        )
        for _, prop in sorted(env.properties.items())
    )
    trades = tuple(
        (
            int(sender),
            int(offer.to_player),
            None if offer.offered_prop is None else int(offer.offered_prop.square_id),
            None if offer.requested_prop is None else int(offer.requested_prop.square_id),
            int(offer.cash_offered),
            int(offer.cash_requested),
        )
        for sender, offer in sorted(env.pending_trades.items())
    )
    return (
        players,
        deeds,
        trades,
        env.phase,
        bool(env.has_rolled),
        int(env.round),
        env.debt_player,
        env.debt_creditor,
        int(env.debt_amount),
        env.auction_property_id,
        env.auction_high_bid,
        env.auction_high_bidder,
        int(env.houses_available),
        int(env.hotels_available),
        tuple(env.turn_order),
        int(env.current_turn_idx),
    )


class EnvironmentMutationError(SubmissionError):
    """The submission mutated the environment instead of just reading it."""


class SubmissionAgent:
    """Adapt a submission entrypoint to the engine's ``choose_action(env)`` seat.

    The engine drives agents with the environment; the contract hands entrants a
    state vector and a legal-action list.  This class bridges the two, and
    enforces the three properties the paired-seed evaluator depends on: the
    submission may not perturb the global RNG streams, may not return an
    action outside the legal set, and — because ``env`` is injected raw for
    policies that need the board — may not mutate the environment.
    """

    def __init__(
        self,
        target: Callable[..., Any],
        player_id: int,
        injections: Sequence[str] = (),
    ):
        self.target = target
        self.player_id = player_id
        self.injections = tuple(injections)
        self.decisions = 0
        self.rng_perturbations = 0

    def choose_action(self, env) -> int:
        allowed: List[int] = list(env.get_allowed_actions(self.player_id))
        if not allowed:
            raise SubmissionError(
                f"seat {self.player_id} was asked to act with no legal action"
            )
        state = env._get_state(self.player_id)
        extra = {"env": env, "player_id": self.player_id}
        kwargs = {name: extra[name] for name in self.injections}

        import random as _random

        before = _random.getstate()
        env_before = _env_fingerprint(env) if "env" in self.injections else None
        with preserve_global_rng():
            try:
                raw = self.target(state, allowed, **kwargs)
            except Exception as exc:
                raise SubmissionError(
                    f"{ENTRYPOINT_ATTRIBUTE} raised for seat {self.player_id}: {exc!r}"
                ) from exc
            if _random.getstate() != before:
                self.rng_perturbations += 1
        if env_before is not None and _env_fingerprint(env) != env_before:
            raise EnvironmentMutationError(
                f"submission for seat {self.player_id} mutated the environment "
                "(cash, ownership, buildings, trades, or phase state changed "
                "during its decision)"
            )

        action = _as_action_index(raw)
        if action not in allowed:
            raise IllegalActionError(
                f"{ENTRYPOINT_ATTRIBUTE} returned illegal action {action} for "
                f"seat {self.player_id}; legal actions are {allowed}"
            )
        self.decisions += 1
        return action
