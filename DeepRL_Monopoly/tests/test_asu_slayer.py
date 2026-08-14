from __future__ import annotations

import copy
import random
import unittest

from ASU_FROZEN_TEACHER.evaluate import (
    AgentFactory,
    _new_seeded_game,
    parse_agent_spec,
)
from ASU_SLAYER.board import (
    acquisition_income,
    exposure,
    exposure_if_free,
    improvement_income,
    income,
    income_by_square,
    landings,
    rent_at,
    rent_quantile,
)
from ASU_SLAYER.policy import DEFAULT_CONFIG, SlayerV1
from ASU_SLAYER.scoring import (
    acquisition_gain,
    deed_worth,
    development_outlook,
    disposal_loss,
    improvement_gain,
    liquidation_options,
    mortgage_loss,
)
from ASU_SLAYER.search import SearchConfig, SlayerRolloutV1
from monopoly_game_engine.actions import (
    AUCTION_ACTION_TO_INCREMENT,
    OFFSETS,
    PROPERTY_IDS,
    ActionType,
    AuctionAction,
)
from monopoly_game_engine.constants import (
    COLOR_GROUPS,
    JAIL_SQUARE,
    NUM_PLAYERS,
    PROPERTIES,
    REAL_ESTATE_IDS,
)
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


class JailExposureTest(unittest.TestCase):
    """``exposure`` measures staying in jail; the decision is about leaving."""

    def _hostile(self):
        env = fresh_env()
        for colour in ("orange", "red", "yellow"):
            for square in COLOR_GROUPS[colour]:
                prop = env.properties[square]
                prop.owner = 1
                env.players[1].properties.append(prop)
        env._update_monopolies()
        for colour in ("orange", "red", "yellow"):
            for square in COLOR_GROUPS[colour]:
                env.properties[square].houses = 5
        env.players[0].position = JAIL_SQUARE
        return env

    def test_the_two_agree_for_a_player_who_is_not_in_jail(self):
        env = self._hostile()
        env.players[0].in_jail = False
        self.assertAlmostEqual(
            exposure(env, 0, 1)[0], exposure_if_free(env, 0, 1)[0], places=9
        )

    def test_the_in_jail_figure_is_deflated_by_the_cell(self):
        env = self._hostile()
        env.players[0].in_jail = True
        jailed = exposure(env, 0, 1)[0]
        free = exposure_if_free(env, 0, 1)[0]
        self.assertGreater(free, 5 * jailed)

    def test_the_old_measurement_could_never_reach_the_threshold(self):
        """2820/36 = 78.33 is a hard ceiling, and the default threshold is 95.

        A jailed player with fewer than ``MAX_JAIL_TURNS - 1`` turns served
        only moves on a double, so from square 10 the reachable squares are
        12, 14, 16, 18, 20 and 22 at one roll in thirty-six each. Summing the
        largest rent each can carry bounds the expectation outright.
        """

        # Not a behavioural claim about the fixed code — a proof about the old
        # measurement, kept so the branch can never silently go dead again.
        reachable = ((12, 2), (14, 4), (16, 6), (18, 8), (20, 10), (22, 12))
        ceiling = 0.0
        for square, dice in reachable:
            data = PROPERTIES.get(square)
            if data is None:
                continue
            if data["color"] == "utility":
                ceiling += data["rent"][1] * dice
            elif data["color"] == "railroad":
                ceiling += data["rent"][3]
            else:
                ceiling += data["rent"][5]
        ceiling /= 36.0
        self.assertAlmostEqual(ceiling, 2820 / 36, places=6)
        self.assertLess(ceiling, DEFAULT_CONFIG.jail_exposure_threshold)

        # And no board, however hostile, can beat that bound in practice.
        env = fresh_env()
        for square in PROPERTY_IDS:
            prop = env.properties[square]
            prop.owner = 1
            env.players[1].properties.append(prop)
        env._update_monopolies()
        for square in REAL_ESTATE_IDS:
            env.properties[square].houses = 5
        env.players[0].position = JAIL_SQUARE
        env.players[0].in_jail = True
        self.assertLessEqual(exposure(env, 0, 1)[0], ceiling + 1e-9)


class RentFlowTest(unittest.TestCase):
    """The rent terms must equal the real change in ``income()``."""

    def _mixed_board(self, seed: int):
        env = fresh_env(seed)
        rnd = random.Random(seed)
        for square in PROPERTY_IDS:
            owner = rnd.choice([None, 0, 1, 2, 3])
            if owner is not None:
                prop = env.properties[square]
                prop.owner = owner
                env.players[owner].properties.append(prop)
        env._update_monopolies()
        for square in REAL_ESTATE_IDS:
            if env.properties[square].is_monopoly and rnd.random() < 0.5:
                env.properties[square].houses = rnd.choice([1, 2, 3, 4, 5])
        for square in PROPERTY_IDS:
            if env.properties[square].houses == 0 and rnd.random() < 0.15:
                env.properties[square].mortgaged = True
        for player in env.players:
            player.position = rnd.randrange(40)
        return env

    def test_acquisition_income_matches_a_realised_income_change(self):
        for seed in (0, 1, 2):
            env = self._mixed_board(seed)
            for square in PROPERTY_IDS:
                for pid in range(NUM_PLAYERS):
                    if env.properties[square].owner == pid:
                        continue
                    with self.subTest(seed=seed, square=square, pid=pid):
                        avoided = 0.0
                        owner = env.properties[square].owner
                        if owner is not None:
                            for (item, dice), p in landings(env.players[pid], 1):
                                if item == square:
                                    avoided += p * rent_at(env, item, dice)
                        before = income(env, pid, 1)
                        clone = copy.deepcopy(env)
                        prop = clone.properties[square]
                        if prop.owner is not None:
                            clone.players[prop.owner].properties.remove(prop)
                        prop.owner = pid
                        clone.players[pid].properties.append(prop)
                        clone._update_monopolies()
                        self.assertAlmostEqual(
                            acquisition_income(env, pid, square, 1),
                            (income(clone, pid, 1) - before) + avoided,
                            places=9,
                        )

    def test_acquiring_a_deed_you_already_hold_changes_nothing(self):
        env = self._mixed_board(0)
        for square in PROPERTY_IDS:
            owner = env.properties[square].owner
            if owner is not None:
                self.assertEqual(acquisition_income(env, owner, square, 1), 0.0)

    def test_closing_a_group_is_worth_more_than_the_deed_alone(self):
        """Completing a colour group doubles base rent on the deeds held."""

        env = fresh_env()
        first, second = COLOR_GROUPS["brown"]
        for player in env.players:
            player.position = 0
        alone = acquisition_income(env, 0, first, 1)

        prop = env.properties[second]
        prop.owner = 0
        env.players[0].properties.append(prop)
        env._update_monopolies()
        closing = acquisition_income(env, 0, first, 1)
        self.assertGreater(closing, alone)

    def test_improvement_income_matches_a_realised_income_change(self):
        env = fresh_env()
        for square in COLOR_GROUPS["orange"]:
            prop = env.properties[square]
            prop.owner = 0
            env.players[0].properties.append(prop)
        env._update_monopolies()
        for player in env.players:
            player.position = 11
        for square in COLOR_GROUPS["orange"]:
            for level in (1, 2, 3, 4, 5):
                with self.subTest(square=square, level=level):
                    clone = copy.deepcopy(env)
                    clone.properties[square].houses = level
                    self.assertAlmostEqual(
                        improvement_income(env, square, level, 1),
                        income(clone, 0, 1) - income(env, 0, 1),
                        places=9,
                    )

    def test_the_measured_default_horizon_is_zero(self):
        """Exact machinery, measured neutral — so it ships off.

        Capitalising rent flow into the greedy objective is the obvious
        remedy for the capped-game weakness, and it does not work: over 250
        seed-clusters and 2,000 games per arm a horizon of 10 scored -0.10pp
        (95% CI -1.71..+1.51) and a horizon of 20 scored +0.30pp
        (CI -1.57..+2.17). The interval is tight enough to bound the effect
        near zero rather than merely fail to detect one.
        """

        self.assertEqual(DEFAULT_CONFIG.rent_horizon, 0.0)

    def test_a_zero_horizon_reproduces_the_pure_net_worth_objective(self):
        env = fresh_env()
        env.players[0].position = 0
        priced = SlayerV1(0, DEFAULT_CONFIG.evolve(rent_horizon=0.0))
        for square in PROPERTY_IDS:
            self.assertEqual(
                priced._acquire_value(env, square),
                acquisition_gain(env, 0, square, include_denial=False)
                * development_outlook(env, 0, square)
                + DEFAULT_CONFIG.denial_fraction
                * max(
                    acquisition_gain(env, rival, square, include_denial=False)
                    for rival in (1, 2, 3)
                ),
            )


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

    def test_quantile_compounds_to_the_survival_target(self):
        """The whole point: q ** horizon must equal the survival target.

        A flat 0.90 per turn reads as safe and is not — over the ~45 turns a
        game actually lasts it compounds to under 1% survival, which is what
        bankrupted the policy in 113 of 128 measured games.
        """

        env = fresh_env()
        agent = SlayerV1(0)
        config = agent.config
        env.round = 0
        quantile = agent._survival_quantile(env)
        horizon = config.expected_game_length
        self.assertAlmostEqual(quantile**horizon, config.target_survival, places=6)
        self.assertGreater(quantile, 0.99)

    def test_quantile_relaxes_as_the_game_runs_out(self):
        """Fewer turns left means fewer chances to be ruined, so risk more."""

        env = fresh_env()
        agent = SlayerV1(0)
        previous = 1.0
        for round_number in (0, 10, 20, 30, 40, 44):
            env.round = round_number
            quantile = agent._survival_quantile(env)
            self.assertLess(quantile, previous)
            self.assertTrue(0.0 < quantile < 1.0)
            previous = quantile

    def test_quantile_stays_valid_past_the_expected_game_length(self):
        """A long game must not drive the horizon to zero or the quantile out of range."""

        env = fresh_env()
        agent = SlayerV1(0)
        for round_number in (44, 45, 60, 199, 200):
            env.round = round_number
            quantile = agent._survival_quantile(env)
            self.assertTrue(0.0 < quantile < 1.0, f"round={round_number}")
            # The floor on the horizon pins the late-game quantile.
            self.assertAlmostEqual(
                quantile,
                agent.config.target_survival ** (1.0 / agent.config.min_horizon),
                places=9,
            )

    def test_a_longer_expected_game_demands_a_stricter_quantile(self):
        env = fresh_env()
        short = SlayerV1(0, DEFAULT_CONFIG.evolve(expected_game_length=30.0))
        long = SlayerV1(0, DEFAULT_CONFIG.evolve(expected_game_length=60.0))
        self.assertLess(short._survival_quantile(env), long._survival_quantile(env))

    def test_configuration_rejects_impossible_settings(self):
        for changes in (
            {"target_survival": 0.0},
            {"target_survival": 1.0},
            {"target_survival": -0.5},
            {"min_horizon": 0.0},
            {"expected_game_length": 0.0},
            {"threat_multiple": -1.0},
        ):
            with self.assertRaises(ValueError, msg=str(changes)):
                DEFAULT_CONFIG.evolve(**changes)

    def test_empty_board_reserve_does_not_block_the_first_purchase(self):
        """The old design added a flat reserve while deeds were unowned."""

        env = fresh_env()
        agent = SlayerV1(0)
        reserve = agent._reserve(env)
        self.assertEqual(reserve, DEFAULT_CONFIG.reserve_floor)
        # Boardwalk is the most expensive deed; starting cash must still clear it.
        self.assertTrue(agent._affordable(env, PROPERTIES[39]["price"], reserve))

    def test_the_measured_default_reserve_is_zero(self):
        """A solvency reserve is a losing trade in this engine.

        Running out of cash never bankrupts a player directly: unpaid rent
        becomes a debt, the rescue menu opens, and ``DECLARE_BANKRUPT`` is
        only legal once nothing is left to liquidate. Being short therefore
        costs 1.5 net worth per dollar mortgaged — exactly what the unspent
        dollar would have earned buying a deed, with certainty.

        Removing it together with the auction step fix is worth +11.60pp
        pooled (95% CI +8.43..+14.77) over 250 seed-clusters and 2,000 games,
        across three seed blocks and two lineups. See ``SlayerConfig``.
        """

        self.assertEqual(DEFAULT_CONFIG.reserve_floor, 0.0)
        self.assertEqual(DEFAULT_CONFIG.threat_multiple, 0.0)

    def test_a_zero_threat_multiple_skips_the_quantile_entirely(self):
        """The short circuit must not change the number it returns."""

        env = fresh_env()
        for square in COLOR_GROUPS["orange"]:
            prop = env.properties[square]
            prop.owner = 1
            env.players[1].properties.append(prop)
        env._update_monopolies()
        for square in COLOR_GROUPS["orange"]:
            env.properties[square].houses = 4
        env.players[0].position = 13

        self.assertEqual(SlayerV1(0)._reserve(env), 0.0)
        # The same config with the multiple restored must still price the threat.
        threatened = SlayerV1(0, DEFAULT_CONFIG.evolve(threat_multiple=1.0))
        self.assertGreater(threatened._reserve(env), 0.0)

    def test_a_zero_reserve_can_never_refuse_a_legal_purchase(self):
        """``BUY_PROPERTY`` is only legal when the price is affordable."""

        env = fresh_env()
        agent = SlayerV1(0)
        for price in (60, 200, 400):
            env.players[0].cash = price
            self.assertTrue(agent._affordable(env, float(price), 0.0))

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


class DevelopmentOutlookTest(unittest.TestCase):
    """Concentration: a deed we can never build on is not worth list price."""

    def test_outlook_is_full_while_the_group_is_still_winnable(self):
        env = fresh_env()
        group = COLOR_GROUPS["orange"]
        self.assertEqual(development_outlook(env, 0, group[0]), 1.0)
        env.properties[group[1]].owner = 0
        self.assertEqual(development_outlook(env, 0, group[0]), 1.0)

    def test_outlook_collapses_once_an_opponent_enters_the_group(self):
        env = fresh_env()
        group = COLOR_GROUPS["orange"]
        env.properties[group[1]].owner = 1
        self.assertLess(development_outlook(env, 0, group[0]), 1.0)

    def test_railroads_and_utilities_are_exempt(self):
        """Their rent scales with the count held, so partial ownership earns."""

        env = fresh_env()
        for colour in ("railroad", "utility"):
            group = COLOR_GROUPS[colour]
            env.properties[group[1]].owner = 1
            self.assertEqual(development_outlook(env, 0, group[0]), 1.0)

    def test_a_blocked_deed_is_valued_below_a_winnable_one(self):
        env = fresh_env()
        seat = 0
        winnable = COLOR_GROUPS["orange"][0]
        blocked = COLOR_GROUPS["red"][0]
        env.properties[COLOR_GROUPS["red"][1]].owner = 1
        env.players[1].properties.append(env.properties[COLOR_GROUPS["red"][1]])
        agent = SlayerV1(seat)
        self.assertGreater(
            agent._acquire_value(env, winnable), agent._acquire_value(env, blocked)
        )

    def test_still_buys_into_a_group_nobody_else_has_entered(self):
        env = fresh_env()
        seat = env.active_player_id()
        env.phase = "post_roll"
        env.has_rolled = True
        env.players[seat].position = COLOR_GROUPS["orange"][0]
        self.assertEqual(
            SlayerV1(seat).choose_action(env), int(ActionType.BUY_PROPERTY)
        )


class ActiveLiquidationTest(unittest.TestCase):
    """Refusing to spend cannot recover cash; only raising it can.

    The recovery rule exists to claw cash back up to the solvency reserve, so
    it is only meaningful when there is a reserve. The measured default is now
    zero (see ``ReserveTest``), which makes ``_raise_cash_action`` a no-op, so
    every test here restores the pre-measurement reserve explicitly rather than
    leaning on the shipped default.
    """

    LIQUIDATING = DEFAULT_CONFIG.evolve(
        active_liquidation=True, reserve_floor=50.0, threat_multiple=1.0
    )

    def _threatened(self):
        """Us cash-poor and holding deeds, an opponent with a developed group."""

        env = fresh_env()
        seat = env.active_player_id()
        rival = (seat + 1) % 4
        for square in COLOR_GROUPS["orange"]:
            prop = env.properties[square]
            prop.owner = rival
            env.players[rival].properties.append(prop)
        env._update_monopolies()
        for square in COLOR_GROUPS["orange"]:
            env.properties[square].houses = 4
        env.players[seat].position = 13  # oranges are a roll away
        env.players[seat].cash = 60
        return env, seat

    def _give(self, env, seat, squares):
        for square in squares:
            prop = env.properties[square]
            prop.owner = seat
            env.players[seat].properties.append(prop)
        env._update_monopolies()

    def test_mortgages_when_cash_is_under_the_reserve(self):
        env, seat = self._threatened()
        self._give(env, seat, [COLOR_GROUPS["brown"][0], COLOR_GROUPS["pink"][0]])
        agent = SlayerV1(seat, self.LIQUIDATING)
        self.assertLess(env.players[seat].cash, agent._reserve(env))
        legal = set(env.get_allowed_actions(seat))
        action = agent._raise_cash_action(env, legal)
        self.assertIsNotNone(action)
        self.assertTrue(OFFSETS["mortgage"] <= action < OFFSETS["unmortgage"])

    def test_does_nothing_when_cash_is_healthy(self):
        env, seat = self._threatened()
        self._give(env, seat, [COLOR_GROUPS["brown"][0]])
        env.players[seat].cash = 5000
        agent = SlayerV1(seat, self.LIQUIDATING)
        legal = set(env.get_allowed_actions(seat))
        self.assertIsNone(agent._raise_cash_action(env, legal))

    def test_never_mortgages_a_deed_inside_a_monopoly(self):
        env, seat = self._threatened()
        self._give(env, seat, list(COLOR_GROUPS["brown"]))
        agent = SlayerV1(seat, self.LIQUIDATING)
        legal = set(env.get_allowed_actions(seat))
        action = agent._raise_cash_action(env, legal)
        # Brown is our only holding and it is a complete group, so nothing may go.
        self.assertIsNone(action)

    def test_gives_up_the_lowest_earning_deed_first(self):
        env, seat = self._threatened()
        cheap = COLOR_GROUPS["brown"][0]
        earner = 5  # Reading Railroad, opponents land on it far more often
        self._give(env, seat, [cheap, earner])
        agent = SlayerV1(seat, self.LIQUIDATING)
        legal = set(env.get_allowed_actions(seat))
        earned = income_by_square(env, seat, 1)
        self.assertGreater(earned.get(earner, 0.0), earned.get(cheap, 0.0))
        action = agent._raise_cash_action(env, legal)
        self.assertEqual(action, OFFSETS["mortgage"] + PROPERTY_IDS.index(cheap))

    def test_recovery_and_investment_never_oscillate(self):
        """Mortgage then unmortgage then mortgage would loop forever."""

        env, seat = self._threatened()
        self._give(
            env, seat, [COLOR_GROUPS["brown"][0], COLOR_GROUPS["pink"][0], 5, 15]
        )
        agent = SlayerV1(seat, self.LIQUIDATING)
        seen = []
        for _step in range(40):
            legal = set(env.get_allowed_actions(seat))
            action = agent.choose_action(env)
            self.assertIn(action, legal)
            if action == int(ActionType.END_TURN):
                break
            seen.append(action)
            env.step(action)
        mortgaged = [a for a in seen if OFFSETS["mortgage"] <= a < OFFSETS["unmortgage"]]
        unmortgaged = [
            a for a in seen if OFFSETS["unmortgage"] <= a < OFFSETS["improve_house"]
        ]
        self.assertEqual(
            len(mortgaged), len(set(mortgaged)), "a deed was mortgaged twice"
        )
        self.assertEqual(unmortgaged, [], "undid its own recovery in the same turn")

    def test_the_flag_disables_it(self):
        """Off by default: 24 paired games showed no survival effect either way."""

        env, seat = self._threatened()
        self._give(env, seat, [COLOR_GROUPS["brown"][0], COLOR_GROUPS["pink"][0]])
        self.assertFalse(DEFAULT_CONFIG.active_liquidation)
        agent = SlayerV1(seat, DEFAULT_CONFIG)
        legal = set(env.get_allowed_actions(seat))
        self.assertIsNone(agent._raise_cash_action(env, legal))


class AuctionTest(unittest.TestCase):
    """Bidders leave only by passing, so the smallest raise wins as often."""

    def _contested(self, high_bid: int = 0):
        env = fresh_env()
        seat = env.active_player_id()
        env.phase = "auction"
        env.auction_property_id = 39  # Boardwalk, so the ceiling is generous
        env.auction_bidders = [pid for pid in env.turn_order]
        env.auction_current_pid = seat
        env.auction_high_bid = high_bid
        env.auction_high_bidder = None if high_bid == 0 else (seat + 1) % 4
        return env, seat

    def test_the_measured_default_step_is_the_smallest_increment(self):
        """A coarse step overpays on auctions that were already won.

        Raising by the largest increment reaches the same terminal price as
        raising by the smallest, because the only way out of the engine's
        auction is ``PASS``. Measured with the zero reserve, the pair is worth
        +11.60pp pooled (95% CI +8.43..+14.77). See ``SlayerConfig``.
        """

        self.assertEqual(DEFAULT_CONFIG.auction_step_fraction, 0.0)

        env, seat = self._contested()
        legal = set(env.get_allowed_actions(seat))
        action = SlayerV1(seat).choose_action(env)
        self.assertIn(action, legal)
        self.assertEqual(
            AUCTION_ACTION_TO_INCREMENT[AuctionAction(action)],
            min(AUCTION_ACTION_TO_INCREMENT.values()),
        )

    def test_the_old_coarse_step_bid_far_more_for_the_same_deed(self):
        env, seat = self._contested()
        legal = set(env.get_allowed_actions(seat))
        coarse = SlayerV1(seat, DEFAULT_CONFIG.evolve(auction_step_fraction=0.18))
        coarse_action = coarse.choose_action(env)
        fine_action = SlayerV1(seat).choose_action(env)
        self.assertIn(coarse_action, legal)
        self.assertGreater(
            AUCTION_ACTION_TO_INCREMENT[AuctionAction(coarse_action)],
            AUCTION_ACTION_TO_INCREMENT[AuctionAction(fine_action)],
        )

    def test_it_still_passes_once_the_ceiling_is_reached(self):
        env, seat = self._contested(high_bid=1200)
        env.players[seat].cash = 5000
        self.assertEqual(
            SlayerV1(seat).choose_action(env), int(AuctionAction.PASS)
        )


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


class GroupCompletionOverrideTest(unittest.TestCase):
    """The arm-D port: a group-closing deed outranks the solvency reserve.

    The override only ever removed the reserve's veto over a purchase, so it
    can only do work while a reserve exists. Now that the measured default
    reserve is zero, ``_affordable(price, 0)`` reduces to ``price <= cash`` —
    which ``BUY_PROPERTY`` legality already guarantees — and the override is
    subsumed entirely. These tests therefore restore the pre-measurement
    reserve to keep exercising the mechanism, and the last test pins the fact
    that it is now inert under the shipped defaults.
    """

    RESERVED = DEFAULT_CONFIG.evolve(reserve_floor=50.0, threat_multiple=1.0)
    ARM = RESERVED.evolve(group_completion_override=True)

    def _cash_starved_on_the_last_brown(self, owner_of_the_sibling: int):
        """Land the seat on a brown deed it can afford but the reserve refuses.

        Brown costs 60 and the restored reserve floors at 50 on an undeveloped
        board, so 100 in cash is affordable to the bank and unaffordable to the
        gate.
        """

        env = fresh_env()
        seat = env.active_player_id()
        first, second = COLOR_GROUPS["brown"]
        env.properties[first].owner = owner_of_the_sibling
        env.players[owner_of_the_sibling].properties.append(env.properties[first])
        env._update_monopolies()
        env.phase = "post_roll"
        env.has_rolled = True
        env.players[seat].position = second
        env.players[seat].cash = 100
        return env, seat

    def test_the_reserve_alone_would_refuse_the_group_closing_deed(self):
        env, seat = self._cash_starved_on_the_last_brown(owner_of_the_sibling=0)
        self.assertIn(int(ActionType.BUY_PROPERTY), env.get_allowed_actions(seat))
        policy = SlayerV1(seat, self.RESERVED)
        self.assertNotEqual(policy.choose_action(env), int(ActionType.BUY_PROPERTY))
        self.assertEqual(policy.override_fires, 0)

    def test_the_override_buys_the_deed_that_closes_our_group(self):
        env, seat = self._cash_starved_on_the_last_brown(owner_of_the_sibling=0)
        policy = SlayerV1(seat, self.ARM)
        self.assertEqual(policy.choose_action(env), int(ActionType.BUY_PROPERTY))
        self.assertEqual(policy.override_fires, 1)

    def test_the_override_denies_a_group_one_opponent_would_close(self):
        env, seat = self._cash_starved_on_the_last_brown(owner_of_the_sibling=1)
        policy = SlayerV1(seat, self.ARM)
        self.assertEqual(policy.choose_action(env), int(ActionType.BUY_PROPERTY))
        self.assertEqual(policy.override_fires, 1)

    def test_a_group_split_between_two_opponents_is_not_a_denial(self):
        env = fresh_env()
        seat = 0
        squares = COLOR_GROUPS["lightblue"]
        for rival, square in zip((1, 2), squares[:2]):
            env.properties[square].owner = rival
        policy = SlayerV1(seat, self.ARM)
        self.assertFalse(policy._closes_or_denies_group(env, squares[2]))

    def test_railroads_and_utilities_are_excluded(self):
        env = fresh_env()
        seat = 0
        for group in ("railroad", "utility"):
            squares = COLOR_GROUPS[group]
            for square in squares[1:]:
                env.properties[square].owner = seat
            policy = SlayerV1(seat, self.ARM)
            self.assertFalse(
                policy._closes_or_denies_group(env, squares[0]),
                f"{group} cannot be built on, so the reserve keeps its veto",
            )

    def test_the_override_is_off_by_default(self):
        env, seat = self._cash_starved_on_the_last_brown(owner_of_the_sibling=0)
        self.assertFalse(DEFAULT_CONFIG.group_completion_override)
        self.assertFalse(SlayerV1(seat)._closes_or_denies_group(env, 3))

    def test_the_override_never_overdraws_the_bank(self):
        """BUY is illegal below the price, so the override cannot spend cash we
        do not have — it only ignores the reserve held on top of the price."""

        env, seat = self._cash_starved_on_the_last_brown(owner_of_the_sibling=0)
        env.players[seat].cash = 10
        self.assertNotIn(int(ActionType.BUY_PROPERTY), env.get_allowed_actions(seat))
        action = SlayerV1(seat, self.ARM).choose_action(env)
        self.assertIn(action, env.get_allowed_actions(seat))

    def test_the_zero_reserve_subsumes_the_override(self):
        """Under the shipped defaults the override can no longer change a buy.

        The scenario that motivated it — legal at the bank, refused by the gate
        — cannot arise once the reserve is zero, so the default policy already
        makes the purchase the override was invented to force.
        """

        env, seat = self._cash_starved_on_the_last_brown(owner_of_the_sibling=0)
        self.assertEqual(DEFAULT_CONFIG.reserve_floor, 0.0)
        plain = SlayerV1(seat)
        self.assertEqual(plain.choose_action(env), int(ActionType.BUY_PROPERTY))
        self.assertEqual(plain.override_fires, 0)


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
