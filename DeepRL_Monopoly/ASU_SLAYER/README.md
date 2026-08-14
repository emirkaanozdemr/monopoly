# ASU_SLAYER — a net-worth-exact challenger

`ASU_SLAYER` is a search policy for this repository's `ppo-plus-v2` simulator,
built to beat `ASU_FROZEN_TEACHER`. It contains no learned weights and needs no
training: the entire edge comes from optimizing the quantity the simulator
actually ranks players by.

## Why it beats the frozen teacher

The engine decides a game two ways. Either one player is left standing, or the
200-round cap is reached and `MonopolyEnv.winner()` picks the greatest
`Player.net_worth()`. That function does **not** price a deed at its list
price (`monopoly_game_engine/state.py`):

| Holding | Contribution to `net_worth()` | Cash cost |
| --- | --- | --- |
| Unmortgaged deed | `2.5 x price` | `price` |
| Deed in a complete color group | `5.0 x price` | `price` |
| `h` houses | `h x house_price x (1 + 0.5h)` | `h x house_price` |
| Hotel | `5 x house_price x 3.5` | `5 x house_price` |
| Cash | face value | — |

Two consequences drive the whole policy:

- **Every purchase is accretive.** A deed bought at list price converts $1 of
  cash into $2.50 of score, and completing a color group re-prices every deed
  already held in it from `2.5x` to `5.0x`.
- **Every liquidation is expensive, and unequally so.** Mortgaging an ordinary
  deed destroys 2.5 net worth per dollar raised; selling it to the bank
  destroys 5.0; breaking a hotel destroys 11.0. Liquidating in the wrong order
  is a large, silent loss.

`ASU_FROZEN_TEACHER` maximizes `M_assets + R_short + R_long + M_monopoly`,
where `M_assets` is list price plus development *cost* and cash is excluded by
design (`ASU_FROZEN_TEACHER/spec.py`). That objective is a defensible reading
of the ASU papers, but it is not the simulator's scoring rule: it undervalues a
completed group, undervalues development, and prices cash at zero right up to
the moment cash decides the game. The challenger scores every decision in the
engine's own units instead.

## Policies

- `slayer-v1` (`policy.py`) — deterministic, analytic, roughly 60x faster per
  decision than `asu_value_v1`. It ranks every way to spend a dollar by the net
  worth it buys, and spends down to the bank's own limit: the solvency reserve
  that used to gate it was measured and removed (see *Two defaults that were
  costing games* below).
- `slayer-rollout-v1` (`search.py`) — truncated common-random-number rollouts
  driven by `slayer-v1` at every seat, scored at the leaf by `equity()`
  (advantage over the strongest live opponent). It only searches contested
  shortlists, so the greedy policy handles the many decisions that have one
  sensible answer.

Both are pure functions of the environment. Search clones the environment and
restores Python, NumPy, and Torch RNG state, so it never perturbs the caller.

## What the policy does differently

| Situation | `slayer-v1` | `asu_value_v1` |
| --- | --- | --- |
| Unowned deed it can afford | Buys whenever the group-aware gain exceeds the price | Buys under its own value gates |
| Monopoly in hand | Builds to hotels; gain per dollar rises from 0.5x to 4.5x | Builds to maximize projected rent |
| Raising cash under debt | Cheapest net worth per dollar: mortgage, then houses, then bank sale | Orders by its own value function |
| Auction | Bids to a fraction of the true group-aware gain, in the smallest legal increment | Bids the largest increment under its marginal value |
| In jail | Stays while the board is expensive, leaves while it is cheap | Decides from its value function |
| Incoming trade | Accepts only when our net-worth gain beats the proposer's | Requires both parties to gain in ASU units |
| Making offers | Deed-for-deed swaps only, and only when both gain and we gain more | Offers cash or swaps under its own value gates |

### What the instrumented games changed

Two games were logged decision by decision against three `asu-value-v1` seats.
Three defects surfaced, and each is now pinned by a regression test:

- **90 cash-for-deed offers, 0 accepted.** An opponent holding a deed prices it
  above list, and no legal cash level (0.75x, 1.0x, 1.25x) reaches that. In the
  won game these offers consumed a fifth of the agent's decisions. Only swaps
  are proposed now.
- **A flat reserve was added while deeds were unowned.** It peaked on an empty
  board, which is exactly when rent is near zero and buying is safest and
  cheapest. Removed in favor of the rent quantile.
- **Mortgage capacity counted toward the reserve**, which created a trap: a
  strict reserve blocked purchases, owning nothing removed the credit, and the
  gate tightened until the agent never invested and died with zero net worth.
  Every conservative point in an earlier sweep lost all of its games this way.
  The reserve now looks only at real cash.

Both reserve fixes above were steps in the right direction and neither went far
enough: the next section shows the reserve was worth removing outright.

## Two defaults that were costing games

A later audit measured every `SlayerConfig` weight one at a time. Two of them
were not merely mistuned, they were pointed the wrong way.

**The solvency reserve.** Running out of cash never bankrupts a player in this
engine. `_handle_landing` records unpaid rent as a debt, `get_allowed_actions`
opens the rescue menu, and `DECLARE_BANKRUPT` only becomes legal once there is
nothing left to liquidate at all. So the event the reserve insured against is
not ruin, it is a forced mortgage — and a mortgage raises $1 while destroying
2.5 net worth, a cost of 1.5x per dollar. That is exactly what the same dollar
*earns*, with certainty and immediately, when it buys a deed at list price. The
reserve was paying a certain 1.5x to avoid a probabilistic one. Both
`reserve_floor` and `threat_multiple` are now `0.0`.

**The auction step.** `auction_step_fraction` made the policy raise by the
first increment exceeding 18% of its ceiling — often $100 where $10 would have
done. But a bidder leaves the engine's auction only by passing, so climbing in
the smallest legal increment reaches the same terminal price as climbing in the
largest. The coarse step was a pure overpay on auctions that were already won.
It is now `0.0`, which floors the target at 1 and always takes the cheapest
legal raise.

Measured together, seat-balanced, over three seed blocks — `0-24` selected the
change; `1000-1049` and `2000-2049` did not:

| lineup | old | new | delta | 95% CI | sign test |
| --- | --- | --- | --- | --- | --- |
| `fixed-a/b/c` | 70.8% | 79.8% | +9.00pp | +4.50 … +13.50 | p = 0.0016 |
| `fixed-b/d/e` | 54.0% | 68.2% | +14.20pp | +9.77 … +18.63 | p < 0.0001 |
| **pooled** | — | — | **+11.60pp** | **+8.43 … +14.77** | **p < 0.0001** |

250 seed-clusters, 2,000 games: 107 seeds improved, 32 regressed, 111 unchanged.

The cluster is the seed rather than the game deliberately. The four seats
inside one seed share a board and a dice stream, so a per-game McNemar treats
correlated pairs as independent and overstates the result — on the `1000-1049`
block alone it read +15.5pp at p < 0.0001, while a third block read +4.0pp at
p = 0.28. Pooling all three at the seed level gives +11.6pp and a confidence
interval that does not include zero.

The reserve machinery is kept rather than deleted. `target_survival`,
`expected_game_length` and `min_horizon` still shape `_survival_quantile`, so
raising `threat_multiple` above zero restores the entire behaviour for anyone
who wants to re-measure it — and the regression tests that cover the recovery
rule do exactly that.

## Two fixes that were right and did not pay

Not every defect is worth win rate. Both of these were real, both are fixed,
and both measured flat — which is recorded here so nobody re-derives them.

**The jail branch was unreachable.** `_jail_action` compared
`exposure(env, pid, 1)` against a threshold in units of rent. But `exposure`
measures the state the player is *in*, and a jailed player mostly does not
move — only a double leaves the cell. From square 10 that reaches squares 12,
14, 16, 18, 20 and 22 at one roll in thirty-six each, so the expectation is
bounded by `2820/36 = 78.33` however hostile the board becomes, against a
threshold of `95.0`. The branch could never fire. `exposure_if_free` asks the
question the decision actually poses, and the branch now fires on about 4% of
jail decisions. Effect on win rate: none — thresholds of 95, 150, 250 and 400
all scored identically to the old never-stay behaviour over 200 games.

**Rent flow in the objective did not move the needle.** `net_worth` prices
every unmortgaged deed at 2.5x list whatever it earns, so the greedy objective
cannot separate Boardwalk from a railroad — and four games in five are decided
by elimination, which is a rent outcome. `acquisition_income` and
`improvement_income` supply the missing half exactly: both reproduce the
realised change in `income()` to within 1e-9 across randomised boards, and
`rent_horizon` capitalises them into `_acquire_value` and the build ranking.

| horizon | `fixed-a/b/c` | `fixed-b/d/e` | pooled | 95% CI |
| --- | --- | --- | --- | --- |
| 0 (shipped) | 79.8% | 68.2% | — | — |
| 10 | 81.4% | 66.4% | −0.10pp | −1.71 … +1.51 |
| 20 | 82.4% | 66.2% | +0.30pp | −1.57 … +2.17 |

250 seed-clusters, 2,000 games per arm. The interval is tight enough to bound
the effect near zero, not merely to miss one. It helps against one lineup and
hurts against the other.

The reason is worth keeping. The policy already buys almost everything it
lands on — 164 of 186 offers taken, none refused for lack of value — so
re-pricing deeds barely changes which deeds it ends up holding. The capped-game
weakness is a deed **allocation** problem, not a deed **valuation** one: in
those games it finishes with 7 of 28 deeds, 0.8 of them inside a monopoly, and
no legal way to spend $4,908 because nothing is left to buy. Fixing that means
acquiring groups — which is trading — and the naive trade fix makes things
worse (see below). `rent_horizon` ships at `0.0`; the machinery is tested and
available for the search variant and for whoever attacks allocation properly.

### `group_completion_override` — off by default, and now subsumed

**This override no longer does anything under the shipped defaults, and that is
the point.** It only ever removed the reserve's veto over a purchase. With the
reserve at zero, `_affordable(price, 0)` reduces to `price <= cash` — which
`BUY_PROPERTY` legality already guarantees — so the scenario below cannot
arise. The flag, its logic and its tests are kept because they are the record
of how the defect was first found: the override was a hand-cut, group-shaped
patch over a gate that turned out to be wrong everywhere, and its +2.92pp is a
lower bound on the +11.60pp that removing the gate outright is worth. The tests
restore a non-zero reserve so the mechanism itself stays covered.

The original write-up follows.

The reserve still holds a veto the group arithmetic says it should not have.
`acquisition_gain` already prices a group-closing deed at the 2.5x-to-5.0x
re-pricing it triggers, so `gain` clears easily — and then `_affordable`
refuses the purchase because the cash left afterwards would sit under the
solvency reserve. The deed is affordable to the bank and unaffordable to the
gate.

`SlayerConfig.group_completion_override` removes the veto for exactly two
cases: a deed that closes a colour group we hold the rest of, and a deed that
is the last piece of a group a *single* opponent holds the rest of. Railroads
and utilities are excluded — neither can be built on, so closing them buys
none of the development the reserve is risked for. `BUY_PROPERTY` is only
legal when the price itself is affordable, so the override cannot overdraw.

Measured as a paired A/B, both arms replaying the identical seeded game from
the identical seat (every policy involved is deterministic, so a discordant
pair is caused by the override and nothing else):

| field | baseline | override | delta | discordant | McNemar exact |
| --- | --- | --- | --- | --- | --- |
| `fixed-a/b/c` | 72.08% | 74.58% | +2.50pp | 6 won, 0 lost | p = 0.031 |
| `fixed-b/d/e` | 51.67% | 55.00% | +3.33pp | 8 won, 0 lost | p = 0.0078 |
| combined | 61.88% | 64.79% | **+2.92pp** | **14 won, 0 lost** | **p = 1.2e-04** |

240 paired games per field, 60 seeds x 4 seats. The override fires 0.63-0.67
times per game. Not one of the 480 paired games flipped from a win to a loss.

The intervention is a port of the competition agent's arm-D result (+2.95pp,
McNemar p = 1.7e-08, n = 2000, `fixed-b/d/e`), which was measured on an
unrelated policy under a different game-end regime. That it reproduces here at
nearly the same effect size, on a policy with completely different buy logic,
is the evidence that the finding is about the game rather than about either
agent.

It ships off, and with the zero reserve it is now inert either way. To exercise
it you have to restore the reserve it was written to bypass:

```python
from ASU_SLAYER.policy import DEFAULT_CONFIG, SlayerV1

policy = SlayerV1(
    seat,
    DEFAULT_CONFIG.evolve(
        reserve_floor=50.0,             # the gate the override exists to bypass
        threat_multiple=1.0,
        group_completion_override=True,
    ),
)
```

## Usage

The repository's seat-balanced evaluator accepts both identifiers:

```bash
python -m ASU_FROZEN_TEACHER.evaluate \
  --focus slayer-v1 \
  --opponents asu-value-v1 asu-value-v1 asu-value-v1 \
  --seeds 0 1 2 3 4 5 --pretty
```

`benchmark.py` runs a lineup suite and, for each lineup, a paired control in
which `asu-value-v1` occupies the focus seat on the same seeds:

```bash
python -m ASU_SLAYER.benchmark --seeds 0 1 2 3 --output artifacts/slayer.json
```

```python
from ASU_SLAYER import SlayerV1

agent = SlayerV1(player_id=0)
action = agent.choose_action(env)
```

`SlayerConfig` and `SearchConfig` are frozen dataclasses; use `evolve()` to
sweep a weight without mutating the shared default.

## Scope and honesty

These are `ppo-plus-v2` results. The `2.5x` / `5.0x` net-worth multipliers and
the 200-round cap are research controls in this repository, not traditional
Monopoly rules, so the margin reported in `TRAINING_RESULTS.md` measures play
against this simulator's scoring rule and does not transfer to official
Monopoly or to either ASU paper's reported figures. The frozen teacher is not
weakened or modified in any way: `ASU_FROZEN_TEACHER/spec.py` and its hash are
untouched, and the only edit to that package registers two new policy IDs in
its evaluation CLI.
