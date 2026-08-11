from __future__ import annotations

import random
import unittest

from ASU_FROZEN_TEACHER.evaluate import (
    AgentFactory,
    _new_seeded_game,
    parse_agent_spec,
)
from ASU_SLAYER.board import exposure, landings, rent_at, rent_quantile
from ASU_SLAYER.policy import DEFAULT_CONFIG, SlayerV1
from ASU_SLAYER.scoring import (
    acquisition_gain,
    deed_worth,
    disposal_loss,
    improvement_gain,
    liquidation_options,
    mortgage_loss,
)
from ASU_SLAYER.search import SearchConfig, SlayerRolloutV1
from monopoly_game_engine.actions import OFFSETS, PROPERTY_IDS, ActionType
from monopoly_game_engine.constants import COLOR_GROUPS, PROPERTIES, REAL_ESTATE_IDS
from monopoly_game_engine.env import MonopolyEnv
from monopoly_game_engine.state import Property


def fresh_env(seed: int = 0) -> MonopolyEnv:
    random.seed(seed)
    return MonopolyEnv(agent_ids=[0], max_rounds=200)


class DeedWorthTest(unittest.TestCase):
    """``deed_worth`` must reproduce the engine's own scoring exactly."""

    def test_matches_engine_for_every_configuration(self):
        for square in PROPERTY_IDS:
            prop = Property(square)
            house_levels = (0, 1, 2, 3, 4, 5) if prop.is_real_estate else (0,)
            for houses in house_levels:
                for mortgaged in (False, True):
                    for monopoly in (False, True):
                        prop.houses = houses
                        prop.mortgaged = mortgaged
                        prop.is_monopoly = monopoly
                        self.assertAlmostEqual(
                            deed_worth(square, houses, mortgaged, monopoly),
                            prop.calculate_net_worth(),
                            places=6,
                            msg=f"square={square} houses={houses} "
                            f"mortgaged={mortgaged} monopoly={monopoly}",
                        )


class BoardMathTest(unittest.TestCase):
    def test_landing_count_exceeds_turn_count_because_doubles_roll_again(self):
        env = fresh_env()
        previous = 0.0
        for turns in (1, 2, 3):
            total = sum(
                probability for _key, probability in landings(env.players[0], turns)
            )
            # A doubles roll lands twice, so expected landings per turn exceed 1
            # but stay under the 1.2 ceiling the triple-doubles jail rule imposes.
            self.assertGreater(total, float(turns))
            self.assertLess(total, 1.2 * turns)
            self.assertGreater(total, previous)
            previous = total

    def test_a_jailed_player_reaches_fewer_squares(self):
        env = fresh_env()
        env.players[0].in_jail = True
        env.players[0].jail_turns = 0
        jailed = sum(probability for _key, probability in landings(env.players[0], 1))
        env.players[0].in_jail = False
        free = sum(probability for _key, probability in landings(env.players[0], 1))
        self.assertLess(jailed, free)

    def test_rent_doubles_on_a_complete_group(self):
        env = fresh_env()
        group = COLOR_GROUPS["brown"]
        single = group[0]
        env.properties[single].owner = 0
        self.assertEqual(rent_at(env, single), env.properties[single].data["rent"][0])
        for square in group:
            env.properties[square].owner = 0
        self.assertEqual(
            rent_at(env, single), 2 * env.properties[single].data["rent"][0]
        )

    def test_mortgaged_property_earns_no_rent(self):
        env = fresh_env()
        env.properties[1].owner = 0
        env.properties[1].mortgaged = True
        self.assertEqual(rent_at(env, 1), 0)


class DeltaTest(unittest.TestCase):
    """Every analytic delta must equal the real change in ``net_worth()``."""

    def test_acquisition_gain_matches_realised_swing(self):
        env = fresh_env()
        group = COLOR_GROUPS["orange"]
        for square in group[:-1]:
            env.properties[square].owner = 0
            env.players[0].properties.append(env.properties[square])
        env._update_monopolies()

        target = group[-1]
        env.properties[target].owner = 1
        env.players[1].properties.append(env.properties[target])
        env._update_monopolies()

        predicted = acquisition_gain(env, 0, target)
        before = env.players[0].net_worth() - env.players[1].net_worth()

        prop = env.properties[target]
        env.players[1].properties.remove(prop)
        prop.owner = 0
        env.players[0].properties.append(prop)
        env._update_monopolies()
        after = env.players[0].net_worth() - env.players[1].net_worth()

        self.assertAlmostEqual(predicted, after - before, places=6)
        self.assertGreater(predicted, env.properties[target].price)

    def test_improvement_gain_matches_realised_change(self):
        env = fresh_env()
        group = COLOR_GROUPS["orange"]
        for square in group:
            env.properties[square].owner = 0
            env.players[0].properties.append(env.properties[square])
        env._update_monopolies()

        target = group[0]
        for level in range(5):
            predicted = improvement_gain(env, target, to_hotel=level == 4)
            before = env.players[0].net_worth()
            env.properties[target].houses = 5 if level == 4 else level + 1
            after = env.players[0].net_worth()
            self.assertAlmostEqual(predicted, after - before, places=6)
            self.assertGreater(predicted, 0.0)

    def test_mortgage_loss_matches_realised_change(self):
        env = fresh_env()
        env.properties[1].owner = 0
        env.players[0].properties.append(env.properties[1])
        predicted = mortgage_loss(env, 1)
        before = env.players[0].net_worth()
        env.properties[1].mortgaged = True
        self.assertAlmostEqual(predicted, before - env.players[0].net_worth(), places=6)

    def test_disposal_loss_matches_realised_change(self):
        env = fresh_env()
        group = COLOR_GROUPS["brown"]
        for square in group:
            env.properties[square].owner = 0
            env.players[0].properties.append(env.properties[square])
        env._update_monopolies()
        predicted = disposal_loss(env, 0, group[0])
        before = env.players[0].net_worth()
        prop = env.properties[group[0]]
        env.players[0].properties.remove(prop)
        prop.owner = None
        env._update_monopolies()
        self.assertAlmostEqual(predicted, before - env.players[0].net_worth(), places=6)


class LiquidationTest(unittest.TestCase):
    def test_mortgaging_is_preferred_over_selling_to_the_bank(self):
        env = fresh_env()
        square = PROPERTY_IDS[5]
        env.properties[square].owner = 0
        env.players[0].properties.append(env.properties[square])
        index = PROPERTY_IDS.index(square)
        legal = {OFFSETS["mortgage"] + index, OFFSETS["sell_prop"] + index}

        options = liquidation_options(env, 0, legal)
        self.assertEqual(options[0][3], OFFSETS["mortgage"] + index)
        # Both raise the same cash; only the net worth destroyed differs.
        self.assertEqual(options[0][1], options[1][1])
        self.assertLess(options[0][2], options[1][2])

    def test_options_are_restricted_to_legal_actions(self):
        env = fresh_env()
        square = PROPERTY_IDS[5]
        env.properties[square].owner = 0
        env.players[0].properties.append(env.properties[square])
        self.assertEqual(liquidation_options(env, 0, set()), [])


class PolicyTest(unittest.TestCase):
    def test_buys_an_affordable_deed_it_landed_on(self):
        env = fresh_env()
        seat = env.active_player_id()
        env.phase = "post_roll"
        env.has_rolled = True
        env.players[seat].position = 1
        from monopoly_game_engine.actions import ActionType

        action = SlayerV1(seat).choose_action(env)
        self.assertEqual(action, int(ActionType.BUY_PROPERTY))

    def test_never_mortgages_while_solvent(self):
        env = fresh_env()
        seat = env.active_player_id()
        for square in COLOR_GROUPS["brown"]:
            env.properties[square].owner = seat
            env.players[seat].properties.append(env.properties[square])
        env._update_monopolies()
        action = SlayerV1(seat).choose_action(env)
        self.assertFalse(
            OFFSETS["mortgage"] <= action < OFFSETS["unmortgage"],
            "a solvent player must never volunteer a mortgage",
        )

    def test_plays_a_full_game_legally_and_deterministically(self):
        results = []
        for _repeat in range(2):
            game = _new_seeded_game(7)
            factory = AgentFactory()
            agents = [SlayerV1(0)] + [
                factory.build(parse_agent_spec(name), seat + 1)
                for seat, name in enumerate(("fixed-a", "fixed-b", "fixed-c"))
            ]
            decisions = 0
            while not game.env.done and decisions < 20_000:
                actor = game.env.whose_turn()
                allowed = game.env.get_allowed_actions(actor)
                action = agents[actor].choose_action(game.env)
                self.assertIn(action, allowed)
                game.step(action)
                decisions += 1
            results.append((game.env.winner(), game.env.round, decisions))
        self.assertEqual(results[0], results[1])

    def test_rollout_variant_plays_legally(self):
        game = _new_seeded_game(3)
        factory = AgentFactory()
        search = SearchConfig(shortlist=2, rollouts=2, depth=6)
        agents = [SlayerRolloutV1(0, DEFAULT_CONFIG, search)] + [
            factory.build(parse_agent_spec("fixed-a"), seat) for seat in (1, 2, 3)
        ]
        for _step in range(400):
            if game.env.done:
                break
            actor = game.env.whose_turn()
            allowed = game.env.get_allowed_actions(actor)
            action = agents[actor].choose_action(game.env)
            self.assertIn(action, allowed)
            game.step(action)

    def test_search_does_not_disturb_the_caller_environment(self):
        game = _new_seeded_game(5)
        agent = SlayerRolloutV1(0, DEFAULT_CONFIG, SearchConfig(rollouts=2, depth=6))
        while game.env.whose_turn() != 0:
            game.step(game.env.get_allowed_actions(game.env.whose_turn())[0])
        before = (
            game.env.round,
            game.env.phase,
            [player.cash for player in game.env.players],
            [prop.owner for prop in game.env.properties.values()],
        )
        agent.choose_action(game.env)
        after = (
            game.env.round,
            game.env.phase,
            [player.cash for player in game.env.players],
            [prop.owner for prop in game.env.properties.values()],
        )
        self.assertEqual(before, after)


class ReserveTest(unittest.TestCase):
    """Regressions for the two reserve defects the diagnostics exposed."""

    def test_quantile_is_zero_on_an_empty_board(self):
        env = fresh_env()
        self.assertEqual(rent_quantile(env, 0, 0.90), 0.0)

    def test_quantile_rises_once_an_opponent_develops(self):
        """The reserve must grow on its own as the board becomes expensive."""

        env = fresh_env()
        self.assertEqual(rent_quantile(env, 0, 0.90), 0.0)

        for square in PROPERTY_IDS:
            prop = env.properties[square]
            prop.owner = 1
            env.players[1].properties.append(prop)
        env._update_monopolies()
        owned = rent_quantile(env, 0, 0.90)
        self.assertGreater(owned, 0.0)

        for square in REAL_ESTATE_IDS:
            env.properties[square].houses = 5
        self.assertGreater(rent_quantile(env, 0, 0.90), owned)

    def test_quantile_is_monotonic_and_never_exceeds_the_worst_case(self):
        env = fresh_env()
        env.players[0].position = 20
        for square in COLOR_GROUPS["red"]:
            prop = env.properties[square]
            prop.owner = 1
            env.players[1].properties.append(prop)
        env._update_monopolies()
        _expected, worst = exposure(env, 0, 1)
        previous = -1.0
        for quantile in (0.5, 0.75, 0.9, 0.99):
            value = rent_quantile(env, 0, quantile)
            self.assertGreaterEqual(value, previous)
            self.assertLessEqual(value, worst)
            previous = value

    def test_empty_board_reserve_does_not_block_the_first_purchase(self):
        """The old design added a flat reserve while deeds were unowned."""

        env = fresh_env()
        agent = SlayerV1(0)
        reserve = agent._reserve(env)
        self.assertEqual(reserve, DEFAULT_CONFIG.reserve_floor)
        # Boardwalk is the most expensive deed; starting cash must still clear it.
        self.assertTrue(agent._affordable(env, PROPERTIES[39]["price"], reserve))

    def test_reserve_ignores_mortgage_capacity(self):
        """Counting liquidation as cash created a no-assets-no-credit trap."""

        env = fresh_env()
        agent = SlayerV1(0)
        env.players[0].cash = 100
        reserve = 200.0
        self.assertFalse(agent._affordable(env, 0.0, reserve))
        for square in COLOR_GROUPS["darkblue"]:
            prop = env.properties[square]
            prop.owner = 0
            env.players[0].properties.append(prop)
        # Owning mortgageable deeds must not unlock spending we cannot fund.
        self.assertFalse(agent._affordable(env, 0.0, reserve))


class TradeTest(unittest.TestCase):
    def test_never_proposes_a_cash_for_deed_offer(self):
        """90 such offers were made across two instrumented games; 0 accepted."""

        env = fresh_env()
        seat = env.active_player_id()
        other = (seat + 1) % 4
        for square in COLOR_GROUPS["orange"][:2]:
            prop = env.properties[square]
            prop.owner = seat
            env.players[seat].properties.append(prop)
        prop = env.properties[COLOR_GROUPS["orange"][2]]
        prop.owner = other
        env.players[other].properties.append(prop)
        env._update_monopolies()

        legal = set(env.get_allowed_actions(seat))
        proposals = SlayerV1(seat)._trade_proposals(env, legal)
        for _gain, _cost, action in proposals:
            self.assertFalse(
                OFFSETS["buy_trade"] <= action < OFFSETS["exch_trade"],
                "cash offers for deeds are never accepted and waste the decision",
            )

    def test_declines_an_offer_that_favours_its_proposer(self):
        env = fresh_env()
        seat, proposer = 0, 1
        # We hold two of a group; the proposer wants the one that completes it.
        group = COLOR_GROUPS["orange"]
        for square in group[:2]:
            prop = env.properties[square]
            prop.owner = seat
            env.players[seat].properties.append(prop)
        spare = env.properties[COLOR_GROUPS["brown"][0]]
        spare.owner = proposer
        env.players[proposer].properties.append(spare)
        for square in group[2:]:
            prop = env.properties[square]
            prop.owner = proposer
            env.players[proposer].properties.append(prop)
        env._update_monopolies()

        from monopoly_game_engine.env import TradeOffer

        # A cheap brown for one of our oranges: good for them, not for us.
        env.pending_trades[proposer] = TradeOffer(
            proposer, seat, offered_prop=spare, requested_prop=env.properties[group[0]]
        )
        env.phase = "out_of_turn"
        env.out_of_turn_pids = [seat]
        legal = {int(ActionType.ACCEPT_TRADE), int(ActionType.DECLINE_TRADE)}
        decision = SlayerV1(seat)._incoming_trade_action(env, legal)
        self.assertEqual(decision, int(ActionType.DECLINE_TRADE))


class RegistrationTest(unittest.TestCase):
    def test_evaluator_accepts_the_slayer_identifiers(self):
        for name, expected in (
            ("slayer-v1", SlayerV1),
            ("slayer-rollout-v1", SlayerRolloutV1),
        ):
            spec = parse_agent_spec(name)
            self.assertEqual(spec.policy_id, name)
            agent = AgentFactory().build(spec, 2)
            self.assertIsInstance(agent, expected)
            self.assertEqual(agent.player_id, 2)


if __name__ == "__main__":
    unittest.main()
