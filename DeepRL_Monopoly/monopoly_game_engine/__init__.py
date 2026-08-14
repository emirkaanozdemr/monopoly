"""
monopoly_game_engine – Shared ppo-plus-v2 Monopoly simulator
=============================================================

Based on:
  "Decision Making in Monopoly Using a Hybrid Deep Reinforcement
   Learning Approach"
  Bonjour et al., IEEE TETCI, Vol. 6, No. 6, December 2022.

Quick start
-----------
>>> from monopoly_game_engine import train_ppo, train_ddqn, evaluate_agent
>>> agent, history = train_ppo(hybrid=True, n_games=2000)
>>> results = evaluate_agent(agent, is_ppo=True, n_games=2000)

The simulator itself needs only numpy; torch is a training dependency. The
names that pull it in — the agents, ``train``/``evaluate`` and the three
helpers below — are therefore resolved on first attribute access rather than
at import, so that playing a game (the tournament submission does exactly
this, in a container that has no torch) never imports torch at all.
"""

import importlib

from .env import MonopolyEnv
from .agents_fixed import FPAgentA, FPAgentB, FPAgentC
from .state import build_state_vector
from .actions import ACTION_SPACE_SIZE, action_to_description


_LAZY = {
    "PPOAgent": ("agent_ppo", "PPOAgent"),
    "DDQNAgent": ("agent_ddqn", "DDQNAgent"),
    "train": ("train", "train"),
    "evaluate": ("train", "evaluate"),
}


def __getattr__(name):
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(f".{module_name}", __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


def _seed_everything(seed: int) -> None:
    import random

    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def train_ppo(
    hybrid: bool = True,
    player_id: int = 0,
    n_games: int = 2000,
    log_every: int = 100,
    checkpoint_every: int = 0,
    checkpoint_path: str | None = None,
    watchdog=None,
    seed: int = 42,
    resume_path: str | None = None,
    **kwargs,
):
    """Train a PPO agent. Set hybrid=True for the hybrid approach."""
    from .agent_ppo import PPOAgent
    from .train import train

    _seed_everything(seed)
    agent = PPOAgent(player_id=player_id, hybrid=hybrid, **kwargs)
    if resume_path is not None:
        agent.load(resume_path)
        n_games = max(0, n_games - agent.games_trained)
    history = train(
        agent,
        is_ppo=True,
        hybrid=hybrid,
        n_games=n_games,
        log_every=log_every,
        checkpoint_every=checkpoint_every,
        checkpoint_path=checkpoint_path,
        watchdog=watchdog,
        seed=seed,
    )
    return agent, history


def train_ddqn(
    hybrid: bool = True,
    player_id: int = 0,
    n_games: int = 10_000,
    log_every: int = 100,
    checkpoint_every: int = 0,
    checkpoint_path: str | None = None,
    watchdog=None,
    seed: int = 42,
    resume_path: str | None = None,
    **kwargs,
):
    """Train a DDQN agent. Set hybrid=True for the hybrid approach."""
    from .agent_ddqn import DDQNAgent
    from .train import train

    _seed_everything(seed)
    agent = DDQNAgent(player_id=player_id, hybrid=hybrid, **kwargs)
    if resume_path is not None:
        agent.load(resume_path)
        n_games = max(0, n_games - agent.games_trained)
    history = train(
        agent,
        is_ppo=False,
        hybrid=hybrid,
        n_games=n_games,
        log_every=log_every,
        checkpoint_every=checkpoint_every,
        checkpoint_path=checkpoint_path,
        watchdog=watchdog,
        seed=seed,
    )
    return agent, history


def evaluate_agent(agent, is_ppo: bool, n_games: int = 2000, n_runs: int = 5):
    """Evaluate a trained agent against fixed-policy opponents."""
    from .train import evaluate

    return evaluate(agent, is_ppo=is_ppo, n_games=n_games, n_runs=n_runs)


__all__ = [
    "MonopolyEnv",
    "PPOAgent", "DDQNAgent",
    "FPAgentA", "FPAgentB", "FPAgentC",
    "train_ppo", "train_ddqn", "evaluate_agent",
    "build_state_vector", "ACTION_SPACE_SIZE", "action_to_description",
]
