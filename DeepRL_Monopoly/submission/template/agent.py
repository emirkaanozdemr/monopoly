"""Submission template — copy this file to the root of your repository.

Required contract
-----------------

    choose_action(state, allowed_actions) -> int

``state``            float32 vector of length 300, built for *your* seat
``allowed_actions``  list[int] of the action indices that are legal right now
``return``           one element of ``allowed_actions``

Returning anything outside ``allowed_actions`` fails the match, so filter the
legal list rather than assuming an action is available.

Optional extras
---------------

Declare ``env`` and/or ``player_id`` after the two required parameters and the
harness will pass them by keyword:

    def choose_action(state, allowed_actions, env, player_id):
        ...

Take ``env`` only if you need the board itself — a search policy that clones
the environment, or a rule that reads pending trades.  The state vector cannot
be turned back into an environment, so this is the only way to write one.

Class form
----------

If your agent holds weights or per-seat state, expose a class named ``Agent``
instead.  It is constructed once per seat, with ``player_id`` if the
constructor accepts it:

    class Agent:
        def __init__(self, player_id):
            self.player_id = player_id
            self.model = load_my_model()

        def choose_action(self, state, allowed_actions):
            ...

Rules your code must respect
----------------------------

* Do not consume the global ``random`` / ``numpy`` / ``torch`` RNG streams.
  Seed your own ``random.Random(...)`` instead.  The evaluator scores paired
  seeds across seats, and a submission that moves the global stream breaks the
  pairing.  The harness restores it after every call and counts the violations.
* Keep one decision well under the per-decision time limit; a game runs to a
  few thousand decisions.
* Load weights lazily or at import, but keep the checkout under 100 MB.
"""

from __future__ import annotations

import random
from typing import List

try:  # available when running inside the tournament harness
    from monopoly_game_engine.actions import ActionType

    BUY_PROPERTY = int(ActionType.BUY_PROPERTY)
    END_TURN = int(ActionType.END_TURN)
    ROLL_DICE = int(ActionType.ROLL_DICE)
    DECLINE_TRADE = int(ActionType.DECLINE_TRADE)
except ImportError:  # standalone: the indices are part of the frozen ruleset
    BUY_PROPERTY, END_TURN, ROLL_DICE, DECLINE_TRADE = 3, 1, 2, 8


class Agent:
    """A deliberately simple baseline: roll, buy what it lands on, end turn."""

    def __init__(self, player_id: int):
        self.player_id = player_id
        # Own RNG stream — never the global one.
        self.rng = random.Random(20260813 + player_id)

    def choose_action(self, state, allowed_actions: List[int]) -> int:
        legal = set(allowed_actions)
        for preferred in (ROLL_DICE, BUY_PROPERTY, DECLINE_TRADE, END_TURN):
            if preferred in legal:
                return preferred
        return self.rng.choice(sorted(legal))
