"""``slayer-v1``: a net-worth-exact policy for the ``ppo-plus-v2`` simulator.

The policy scores every decision in the units the engine ranks players by, then
spends cash wherever a dollar buys the most net worth, subject to a solvency
reserve set by a high quantile of the rent it may owe on the next turn.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from monopoly_game_engine.actions import (
    AUCTION_ACTION_TO_INCREMENT,
    OFFSETS,
    PROPERTY_IDS,
    ActionType,
    AuctionAction,
)
from monopoly_game_engine.constants import (
    JAIL_BAIL,
    NUM_PLAYERS,
    PROPERTIES,
    REAL_ESTATE_IDS,
)
from monopoly_game_engine.env import (
    PHASE_AUCTION,
    PHASE_OUT_OF_TURN,
    PHASE_POST_ROLL,
    PHASE_PRE_ROLL,
)

from .board import exposure, rent_quantile
from .scoring import (
    acquisition_gain,
    disposal_loss,
    improvement_gain,
    liquidation_options,
    mortgage_loss,
)


SLAYER_V1 = "slayer-v1"
_EXCHANGE_STRIDE = len(PROPERTY_IDS) * (len(PROPERTY_IDS) - 1)


@dataclass(frozen=True, slots=True)
class SlayerConfig:
    """Tunable weights.

    The reserve is deliberately threat-proportional and holds no credit for
    liquidation. An earlier design added a flat reserve while deeds were still
    unowned and counted mortgage capacity as if it were cash; both were wrong
    in the same direction. The flat term peaked on an empty board, which is
    when buying is cheapest and rent is nearly zero, and the liquidation credit
    created a trap: a strict reserve blocked purchases, owning nothing removed
    the credit, and the gate tightened until the agent never invested at all.
    """

    reserve_floor: float = 50.0
    risk_quantile: float = 0.90
    threat_multiple: float = 1.0
    auction_value_fraction: float = 0.62
    auction_denial_fraction: float = 0.22
    auction_step_fraction: float = 0.18
    jail_exposure_threshold: float = 95.0
    trade_margin: float = 1.0

    def evolve(self, **changes) -> "SlayerConfig":
        return replace(self, **changes)


DEFAULT_CONFIG = SlayerConfig()


class SlayerV1:
    """Deterministic net-worth-maximizing policy."""

    policy_id = SLAYER_V1

    def __init__(self, player_id: int, config: SlayerConfig = DEFAULT_CONFIG):
        if not 0 <= player_id < NUM_PLAYERS:
            raise ValueError(f"player_id must be in [0, {NUM_PLAYERS - 1}]")
        self.player_id = player_id
        self.config = config

    # ── Solvency ──────────────────────────────────────────────────────────

    def _reserve(self, env) -> float:
        """Cash to keep back: the rent this player survives 90% of the time.

        On an empty board almost every landing owes nothing, so the reserve is
        the floor and the agent invests freely. It rises on its own as
        opponents develop, without any term that depends on what we own.
        """

        config = self.config
        threat = rent_quantile(env, self.player_id, config.risk_quantile, 1)
        return config.reserve_floor + config.threat_multiple * threat

    def _affordable(self, env, cost: float, reserve: float) -> bool:
        cash = float(env.players[self.player_id].cash)
        return cost <= cash and (cash - cost) >= reserve

    # ── Investment candidates ─────────────────────────────────────────────

    def _investments(self, env, legal: set[int]) -> list[tuple[float, int]]:
        """``(net worth gained per action, action)`` for every way to spend cash."""

        candidates: list[tuple[float, int]] = []
        for index, square in enumerate(PROPERTY_IDS):
            action = OFFSETS["unmortgage"] + index
            if action not in legal:
                continue
            prop = env.properties[square]
            cost = int(prop.mortgage_v * 1.1)
            # Unmortgaging restores exactly what mortgaging destroyed.
            gain = mortgage_loss(env, square) - cost
            candidates.append((gain, cost, action))

        for index, square in enumerate(REAL_ESTATE_IDS):
            cost = float(PROPERTIES[square]["house_price"])
            action = OFFSETS["improve_hotel"] + index
            if action in legal:
                candidates.append(
                    (improvement_gain(env, square, True) - cost, cost, action)
                )
            action = OFFSETS["improve_house"] + index
            if action in legal:
                candidates.append(
                    (improvement_gain(env, square, False) - cost, cost, action)
                )

        candidates.extend(self._trade_proposals(env, legal))
        reserve = self._reserve(env)
        return sorted(
            (
                (gain, action)
                for gain, cost, action in candidates
                if gain > 0 and self._affordable(env, cost, reserve)
            ),
            key=lambda item: (-item[0], item[1]),
        )

    def _trade_proposals(self, env, legal: set[int]) -> list[tuple[float, float, int]]:
        """Deed-for-deed swaps a rational opponent has a reason to accept.

        Cash-for-deed offers are omitted on purpose. Instrumented games showed
        90 such offers made and none accepted: an opponent holding a deed
        values it above its list price, and no legal cash level reaches that.
        Proposing them burned a fifth of the agent's decisions for nothing.
        A swap is only proposed when both sides gain and we gain more.
        """

        proposals: list[tuple[float, float, int]] = []
        others = [pid for pid in range(NUM_PLAYERS) if pid != self.player_id]
        for action in legal:
            if not OFFSETS["exch_trade"] <= action < OFFSETS["auction"]:
                continue
            local = action - OFFSETS["exch_trade"]
            target = others[local // _EXCHANGE_STRIDE]
            remainder = local % _EXCHANGE_STRIDE
            offer_index = remainder // (len(PROPERTY_IDS) - 1)
            raw = remainder % (len(PROPERTY_IDS) - 1)
            request_index = raw if raw < offer_index else raw + 1
            offered = PROPERTY_IDS[offer_index]
            requested = PROPERTY_IDS[request_index]

            gain = acquisition_gain(
                env, self.player_id, requested, include_denial=False
            ) - disposal_loss(env, self.player_id, offered)
            counter = acquisition_gain(
                env, target, offered, include_denial=False
            ) - disposal_loss(env, target, requested)
            if counter <= 0 or gain <= counter:
                continue
            proposals.append((gain * self.config.trade_margin, 0.0, action))
        return proposals

    # ── Phase handlers ────────────────────────────────────────────────────

    def _jail_action(self, env, legal: set[int]) -> int | None:
        player = env.players[self.player_id]
        if not player.in_jail:
            return None
        expected_out, _worst = exposure(env, self.player_id, 1)
        # Jail is shelter once the board is expensive; leave while it is cheap.
        if expected_out >= self.config.jail_exposure_threshold:
            return None
        if int(ActionType.USE_GOOJ_CARD) in legal:
            return int(ActionType.USE_GOOJ_CARD)
        reserve = self._reserve(env)
        if int(ActionType.PAY_BAIL) in legal and self._affordable(
            env, JAIL_BAIL, reserve
        ):
            return int(ActionType.PAY_BAIL)
        return None

    def _incoming_trade_action(self, env, legal: set[int]) -> int | None:
        """Judge an offer against what it gains its proposer, not just us.

        Every offer on the table was constructed by an opponent to serve that
        opponent, so "my net worth goes up" is too weak a test. Both sides are
        priced on their own holdings only and the offer is declined unless our
        gain is the larger one.
        """

        if int(ActionType.ACCEPT_TRADE) not in legal:
            return None
        entry = env._incoming_trade_entry(self.player_id)
        if entry is None:
            return int(ActionType.DECLINE_TRADE)
        proposer, offer = entry

        mine = float(offer.cash_offered) - float(offer.cash_requested)
        theirs = float(offer.cash_requested) - float(offer.cash_offered)
        if offer.offered_prop is not None:
            square = offer.offered_prop.square_id
            mine += acquisition_gain(env, self.player_id, square, include_denial=False)
            theirs -= disposal_loss(env, proposer, square)
        if offer.requested_prop is not None:
            square = offer.requested_prop.square_id
            mine -= disposal_loss(env, self.player_id, square)
            theirs += acquisition_gain(env, proposer, square, include_denial=False)

        reserve = self._reserve(env)
        if (
            mine > 0
            and mine > theirs
            and self._affordable(env, float(offer.cash_requested), reserve)
        ):
            return int(ActionType.ACCEPT_TRADE)
        return int(ActionType.DECLINE_TRADE)

    def _auction_action(self, env, legal: set[int]) -> int:
        config = self.config
        square = env.auction_property_id
        if square is None:
            return int(AuctionAction.PASS)
        ceiling = config.auction_value_fraction * acquisition_gain(
            env, self.player_id, square
        )
        rivals = [
            other.player_id
            for other in env.players
            if other.player_id != self.player_id and not other.bankrupt
        ]
        if rivals:
            ceiling += config.auction_denial_fraction * max(
                acquisition_gain(env, rival, square) for rival in rivals
            )

        reserve = self._reserve(env)
        increments = [
            (AUCTION_ACTION_TO_INCREMENT[AuctionAction(action)], action)
            for action in legal
            if action != int(AuctionAction.PASS)
        ]
        step_target = max(1.0, config.auction_step_fraction * ceiling)
        affordable = [
            (increment, action)
            for increment, action in sorted(increments)
            if env.auction_high_bid + increment <= ceiling
            and self._affordable(env, env.auction_high_bid + increment, reserve)
        ]
        if not affordable:
            return int(AuctionAction.PASS)
        for increment, action in affordable:
            if increment >= step_target:
                return action
        return affordable[-1][1]

    def _debt_action(self, env, legal: set[int]) -> int:
        if int(ActionType.DECLARE_BANKRUPT) in legal and len(legal) == 1:
            return int(ActionType.DECLARE_BANKRUPT)
        options = liquidation_options(env, self.player_id, legal)
        if options:
            return options[0][3]
        return min(legal)

    # ── Entry point ───────────────────────────────────────────────────────

    def choose_action(self, env) -> int:
        legal_list = env.get_allowed_actions(self.player_id)
        if not legal_list:
            raise RuntimeError(f"player {self.player_id} has no legal action")
        if len(legal_list) == 1:
            return legal_list[0]
        legal = set(legal_list)

        if env.phase == PHASE_AUCTION:
            return self._auction_action(env, legal)

        if env.debt_player == self.player_id and env.phase == PHASE_POST_ROLL:
            return self._debt_action(env, legal)

        if env.phase == PHASE_POST_ROLL and not env.has_rolled:
            jail = self._jail_action(env, legal)
            if jail is not None:
                return jail
            return int(ActionType.ROLL_DICE)

        if env.phase == PHASE_POST_ROLL:
            if int(ActionType.BUY_PROPERTY) in legal:
                square = env.players[self.player_id].position
                price = float(PROPERTIES[square]["price"])
                gain = acquisition_gain(env, self.player_id, square) - price
                if gain > 0 and self._affordable(env, price, self._reserve(env)):
                    return int(ActionType.BUY_PROPERTY)
            return int(ActionType.END_TURN)

        if env.phase in (PHASE_PRE_ROLL, PHASE_OUT_OF_TURN):
            response = self._incoming_trade_action(env, legal)
            if response is not None:
                return response
            if env.phase == PHASE_PRE_ROLL:
                jail = self._jail_action(env, legal)
                if jail is not None:
                    return jail
            investments = self._investments(env, legal)
            if investments:
                return investments[0][1]
            return int(ActionType.END_TURN)

        return legal_list[0]


__all__ = ["DEFAULT_CONFIG", "SLAYER_V1", "SlayerConfig", "SlayerV1"]
