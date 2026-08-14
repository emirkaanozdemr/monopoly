"""Tournament entrypoint: ``choose_action(state, player_id, allowed_actions)``.

The worker runs this file in its own process and speaks JSON lines to it, so
the mutable ``MonopolyEnv`` never crosses the boundary — ``state["board"]`` is
a plain snapshot of it.  ``ASU_SLAYER``'s policy is written against the env
object, so the snapshot is rebuilt into one here (``_SnapshotEnv``) and the
policy runs unchanged on top of it.

Two fields of the real env are absent from the snapshot and are handled rather
than faked:

``debt_player``
    Inferred.  ``get_allowed_actions`` returns the liquidation menu without
    ``END_TURN`` exactly when the active player owes rent it has not paid, so
    a post-roll seat that has rolled and cannot end its turn is in debt.

``pending_trades``
    Absent from the snapshot, but the 300-float ``state["vector"]`` encodes
    the offer addressed to this seat — sender, both deeds, both cash legs —
    so it is decoded from there (``_decode_incoming_trade``) and the policy
    prices offers as it does with the real engine.

Any failure below the entrypoint falls back to a legal action instead of
raising: three failed decisions replace the seat with a bot for the rest of
the game, and a degraded move is worth more than a forfeited seat.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent / "DeepRL_Monopoly"
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

DO_NOTHING, END_TURN, ROLL_DICE, BUY_PROPERTY = 0, 1, 2, 3
USE_GOOJ_CARD, PAY_BAIL, DECLARE_BANKRUPT, DECLINE_TRADE = 4, 5, 6, 8

# Imported eagerly and deliberately unguarded: a submission that cannot load
# its own policy should fail validation loudly instead of quietly playing the
# fallback for a whole tournament.
from monopoly_game_engine.actions import PROPERTY_IDS
from monopoly_game_engine.constants import NUM_PLAYERS
from monopoly_game_engine.env import PHASE_POST_ROLL
from monopoly_game_engine.state import BASE_STATE_DIM, PHASES, STATE_DIM, Player, Property

from ASU_SLAYER.policy import SlayerV1


# ``build_state_vector`` writes these fields, in this order, after the 240
# base dimensions.  Naming the widths instead of hard-coding indices means the
# assert below fails loudly if the engine's layout ever moves, rather than
# silently decoding the wrong slot.
_TAIL_LAYOUT = (
    ("phase", len(PHASES)),
    ("whose_turn", NUM_PLAYERS),
    ("active_player", NUM_PLAYERS),
    ("has_rolled", 1),
    ("consecutive_doubles", 1),
    ("last_dice", 2),
    ("building_supply", 2),
    ("bankrupt", NUM_PLAYERS),
    ("jail_turns", NUM_PLAYERS),
    ("turn_order", NUM_PLAYERS),
    ("debt_amount", 1),
    ("debt_creditor", NUM_PLAYERS + 1),
    ("auction_property", 1),
    ("auction_high_bid", 1),
    ("round", 1),
    ("auction_high_bidder", NUM_PLAYERS + 1),
    ("auction_bidders", NUM_PLAYERS),
    ("extra_roll_pending", 1),
    ("incoming_sender", NUM_PLAYERS + 1),
    ("incoming_offered", 1),
    ("incoming_requested", 1),
    ("incoming_cash_offered", 1),
    ("incoming_cash_requested", 1),
    ("outgoing_to", 1),
    ("pending_trade_count", 1),
)

_OFFSETS = {}
_cursor = BASE_STATE_DIM
for _name, _width in _TAIL_LAYOUT:
    _OFFSETS[_name] = _cursor
    _cursor += _width
assert _cursor == STATE_DIM, f"state layout drifted: {_cursor} != {STATE_DIM}"

_CASH_SCALE = 2000.0
_SQUARE_SCALE = len(PROPERTY_IDS) + 1


class _IncomingOffer:
    """The fields ``SlayerV1`` prices an offer from, recovered from the vector."""

    __slots__ = (
        "from_player",
        "to_player",
        "offered_prop",
        "requested_prop",
        "cash_offered",
        "cash_requested",
    )

    def __init__(
        self,
        from_player,
        to_player,
        offered_prop,
        requested_prop,
        cash_offered,
        cash_requested,
    ):
        self.from_player = from_player
        self.to_player = to_player
        self.offered_prop = offered_prop
        self.requested_prop = requested_prop
        self.cash_offered = cash_offered
        self.cash_requested = cash_requested


def _decode_square(value: float):
    if value <= 0.0:
        return None
    index = int(round(value * _SQUARE_SCALE)) - 1
    return PROPERTY_IDS[index] if 0 <= index < len(PROPERTY_IDS) else None


def _decode_incoming_trade(vector, player_id: int, properties: dict):
    """Rebuild the pending offer the snapshot omits but the vector encodes.

    ``build_state_vector`` writes the sender, both deeds and both cash legs of
    the offer currently addressed to this seat, which is everything the policy
    needs to price it.  Without this the seat can only decline.
    """

    base = _OFFSETS["incoming_sender"]
    order = [player_id] + [pid for pid in range(NUM_PLAYERS) if pid != player_id]
    sender = None
    for relative in range(NUM_PLAYERS):
        if vector[base + 1 + relative] >= 0.5:
            sender = order[relative]
            break
    if sender is None or sender == player_id:
        return None

    offered = _decode_square(vector[_OFFSETS["incoming_offered"]])
    requested = _decode_square(vector[_OFFSETS["incoming_requested"]])
    return _IncomingOffer(
        from_player=sender,
        to_player=player_id,
        offered_prop=properties.get(offered),
        requested_prop=properties.get(requested),
        # Both legs are a multiple of a deed price, far below the 2000 the
        # encoding saturates at, so this round-trips exactly.
        cash_offered=round(vector[_OFFSETS["incoming_cash_offered"]] * _CASH_SCALE),
        cash_requested=round(vector[_OFFSETS["incoming_cash_requested"]] * _CASH_SCALE),
    )


class _SnapshotEnv:
    """The subset of ``MonopolyEnv`` that ``ASU_SLAYER`` reads, rebuilt.

    Everything is derived from one ``state["board"]``; nothing here advances
    the game, so the policy's reads are the only supported operations.
    """

    def __init__(self, board: dict, vector, player_id: int, allowed: list[int]):
        self.round = board["round"]
        self.phase = board["phase"]
        self.has_rolled = board["has_rolled"]
        self.done = board["done"]
        self.houses_available = board["houses_available"]
        self.hotels_available = board["hotels_available"]

        self.properties = {}
        for entry in board["properties"]:
            prop = Property(entry["square_id"])
            prop.owner = entry["owner"]
            prop.mortgaged = entry["mortgaged"]
            prop.houses = entry["houses"]
            prop.is_monopoly = entry["is_monopoly"]
            self.properties[prop.square_id] = prop

        self.players = []
        for entry in board["players"]:
            player = Player(entry["player_id"])
            player.cash = entry["cash"]
            player.position = entry["position"]
            player.in_jail = entry["in_jail"]
            player.jail_turns = entry["jail_turns"]
            player.gooj_card = entry["gooj_card"]
            player.bankrupt = entry["bankrupt"]
            player.properties = [
                self.properties[square]
                for square in entry["properties"]
                if square in self.properties
            ]
            self.players.append(player)

        auction = board.get("auction") or {}
        self.auction_property_id = auction.get("property_id")
        self.auction_bidders = auction.get("bidders") or []
        self.auction_current_pid = auction.get("current_bidder")
        self.auction_high_bid = auction.get("high_bid") or 0
        self.auction_high_bidder = auction.get("high_bidder")

        self._player_id = player_id
        self._allowed = list(allowed)
        self.debt_player = (
            player_id
            if (
                self.phase == PHASE_POST_ROLL
                and self.has_rolled
                and board["active_player"] == player_id
                and END_TURN not in self._allowed
            )
            else None
        )
        self._incoming = _decode_incoming_trade(vector, player_id, self.properties)

    def whose_turn(self) -> int:
        return self._player_id

    def active_player_id(self) -> int:
        return self._player_id

    def get_allowed_actions(self, pid: int | None = None) -> list[int]:
        # The harness is the authority on legality, and it only ever asks
        # about the seat being decided; any other seat gets the inert action
        # rather than a guess the policy would then act on.
        if pid is None or pid == self._player_id:
            return list(self._allowed)
        return [DO_NOTHING]

    def _incoming_trade_entry(self, pid: int):
        if pid != self._player_id or self._incoming is None:
            return None
        return self._incoming.from_player, self._incoming


def _fallback(allowed: list[int]) -> int:
    legal = set(allowed)
    for preferred in (
        ROLL_DICE,
        USE_GOOJ_CARD,
        BUY_PROPERTY,
        PAY_BAIL,
        DECLINE_TRADE,
        END_TURN,
    ):
        if preferred in legal:
            return preferred
    # Debt menus carry only liquidation actions, and DECLARE_BANKRUPT is the
    # highest index among them — take anything else first.
    remaining = sorted(legal - {DECLARE_BANKRUPT, DO_NOTHING})
    return remaining[0] if remaining else allowed[0]


class Agent:
    """One seat's policy, constructed once and reused for every decision."""

    def __init__(self, player_id):
        self.player_id = player_id
        self.policy = SlayerV1(player_id)

    def choose_action(self, state, allowed_actions):
        allowed = [int(action) for action in allowed_actions]
        if len(allowed) == 1:
            return allowed[0]

        try:
            env = _SnapshotEnv(
                state["board"], state["vector"], self.player_id, allowed
            )
            action = int(self.policy.choose_action(env))
            if action in allowed:
                return action
        except Exception:
            pass
        return _fallback(allowed)


_AGENTS = {}


def choose_action(state, player_id, allowed_actions):
    agent = _AGENTS.get(player_id)
    if agent is None:
        agent = Agent(player_id)
        _AGENTS[player_id] = agent
    return agent.choose_action(state, allowed_actions)
