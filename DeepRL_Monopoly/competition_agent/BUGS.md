# BUGS.md — deterministic findings (invariants + coverage)

Ruleset `ppo-plus-v2`. Candidate D (`final`, TRADE_RANKER=rank_gate_1000.json)
vs the strong field (fixed-b/d/e), seeds 960000..961999, seat = seed % 4 —
the exact games of `probes/field_strong_rank` (verified: all 2000 traced
outcomes and step counts match the un-instrumented run byte-for-byte at the
outcome level; 0 mismatches). 2,000 games, 5,134,822 decisions, all four
players checked. Wins and losses alike.

Instrumentation facts (Phase 1):
- Trace: one gzip'd JSONL per game (`traces/final_strong/`, 111 MB), one row
  per decision: seed, step, round, phase, actor, family, full legal set,
  chosen action, cash/net-worth/position/jail of all players, deed ownership,
  mortgage flags, house counts, bank supply, debt triple, pending trades,
  incoming offer, and the engine's per-step info dict.
- Replay: 20/20 games replayed **byte-identical** (gzip container included;
  written with mtime=0). Seed + PYTHONHASHSEED=0 fully determines a game.
- Overhead: **+37.0%** measured over 20 interleaved games — above the 20%
  gate, so instrumentation lives in a separate pass (`trace_run.py`); the
  measurement harnesses (`bench.py`, `field_ab.py`, `field_ref.py`) are
  untouched.

## Invariant results (Phase 2)

| check | violations / 2,000 games | verdict |
|---|---|---|
| a. chosen ∈ legal set | **0** | clean |
| b. negative cash (any point, debt or not) | **0** | clean |
| c. ownership changes outside purchase/trade/auction/bankruptcy/sell-to-bank | **0** | clean |
| d. supply: houses ≤ 32, hotels ≤ 12, board+bank consistent | **0** | clean |
| d′. even-BUILD rule on build actions | **0** | clean |
| e. per-player cash conservation, every step exactly attributed | **0** | clean |
| g. unmortgage charged exactly ⌊1.1 × mortgage⌋, once | **0** | clean |
| i. declined/unaffordable rolled-upon deed always auctioned | **0** | clean |
| d″. even-SELL across a group | 956 | **documented ruleset deviation** |
| f. build on group containing a mortgaged deed | 2,042 | **documented ruleset deviation** |
| h. cyclic re-trade of same deed within 10 rounds | 38,120 | **behavioral, not an engine defect** — see below |

The cash-conservation check is exact: every one of the 5.13M inter-decision
cash deltas is reproduced from the recorded pre-state plus action semantics
(rent transfer, GO salary, clamped taxes, clamped forced bail, purchase,
mortgage/unmortgage, build/sell, auction settlement, trade cash, bankruptcy
liquidation-and-transfer). One checker model error was found and fixed during
development (three-doubles → jail grants no GO salary); after that, zero
residuals.

### d″ / f — deviations already documented in PPO_PLUS_RULES.md
`PPO_PLUS_RULES.md` states: "Even selling across that group is not enforced"
and "Mortgage checks are per deed rather than enforcing every color-group
restriction from the official rules." Both fire for **all four players** (the
example repro below is a field agent), so neither advantages nor disadvantages
Candidate D systematically. They are listed because the check was mandated;
they are not defects against this ruleset's own spec.

- f repro: seed **960002**, step **1993** (round 64), actor 2 builds on
  deed 27 while the group's mortgage flags are [1, 0, 1].
  Full 5 preceding rows: `traces/check_results.json`, check
  `f_build_on_mortgaged_group`, first entry.
- d″ repro: seed **960009**, step **1525** (round 41), actor 0 sells a house
  leaving the red group at [2, 4, 4].
  Full rows: `traces/check_results.json`, check `d_uneven_sell`.

### h — cyclic re-trade: real, frequent, agent is a party in 86%
38,120 accepted trades put a deed back with a previous owner within 10 rounds;
**32,617 (86%) involve the agent**. The mechanism (verified by reading raw
trace rows, repro below): a field agent (typically fixed-b) proposes an
equal-price, zero-cash exchange inside one color group; the agent's
`_trade_reply` accepts; next round the field agent proposes the reverse swap;
the agent accepts again — indefinitely. 84% of accepted trades in a 30-game
sample are equal-price zero-cash exchanges. The swaps never net-complete a
group for either side (the deed received is offset by the sibling given away
in the same trade), so the loop is materially near-neutral; its association
with losing is a Phase 4/5 question, answered in GAPS.md (H3: direction
negative, does not survive Bonferroni).

- Repro: seed **960000** (agent seat 0), steps **201/235/306/347** — deeds
  31↔32 swapped between players 1 and 0 four times in rounds 6–9; total 66
  accepted trades in this one game, every one proposed by the field and
  accepted by the agent. Preceding rows: `traces/check_results.json`, check
  `h_cyclic_trade`; raw rows in `traces/final_strong/seed_960000.jsonl.gz`.

## Coverage results (Phase 3) — agent seat only, 1,277,448 decisions

### Decision families and default-branch rates

| family | decisions | default action | default rate |
|---|---|---|---|
| oot_offer | 472,395 | END_TURN | 86.6% |
| pre_roll_manage | 228,387 | END_TURN | 63.3% |
| trade_reply | 191,742 | DECLINE | 89.8% |
| roll | 172,482 | ROLL_DICE | **98.5%** ⚑ |
| post_roll_manage | 166,582 | END_TURN | **99.6%** ⚑ |
| auction | 27,755 | PASS | 37.9% |
| buy_decision | 11,878 | END_TURN (decline) | 28.5% |
| debt_rescue | 6,227 | n/a (forced menu) | 0% |

⚑ flagged >90% per the task. Context that keeps these from being defects:
in `roll`, the only alternatives are jail options (GOOJ card cannot occur in
this ruleset; PAY_BAIL is the 1.5% taken); in `post_roll_manage` the menu is
mortgage/sell-to-bank/END_TURN — mortgaging outside debt is almost always
value-negative. Neither family hides an unreachable good action, but both are
where a stronger agent could differ (e.g. bail policy, voluntary liquidity).

### Legal actions never chosen once (entire run)

| section | ids ever legal | ids never chosen | legal exposures wasted |
|---|---|---|---|
| buy_trade (cash-for-deed offers) | 252 | **252 (100%)** | 14,206,893 |
| sell_trade (deed-for-cash offers) | 252 | **252 (100%)** | 20,642,450 |
| sell_prop (sell deed to bank) | 28 | **28 (100%)** | 2,893,760 |
| exch_trade | 2,264 | 789 (35%) | 3,834,354 |
| sell_house / sell_hotel | 22 / 22 | 3 / 9 | 4,389 / 32,578 |
| everything else | — | 0 | 0 |

Classification (code read, `spec_policy._propose_trade`):
- **buy_trade / sell_trade dead by specification, not by mask bug.** SPEC I1
  records that the frozen teacher proposed exchange-only (36/36 in probe p09),
  and `_propose_trade` therefore scores only `exch_trade` actions. 504 action
  ids and 34.8M legal exposures are permanently dead in the shipped agent.
  Consequence worth knowing: cash-for-deed is the only trade shape that could
  buy a group-completing deed from an opponent without surrendering one, and
  it is unreachable (GAPS.md H2 measures the downstream effect).
- **sell_prop never fires** because `_debt` ranks it behind mortgages and
  house sales and the engine's rescue menu always offered a cheaper source of
  cash first; outside debt no rule ever selects it. Defensible (selling to the
  bank at mortgage value is dominated by mortgaging), but it is dead code in
  practice.
- The 789 never-chosen exchanges and the sell_house/sell_hotel tails are
  consistent with a ranker/gate that only ever fires on a subset; exposure
  counts are in `traces/coverage.json`.

### _trade_reply (descriptive only — this path has never been modified)

191,742 replies, **acceptance rate 10.2%** (19,543 accepts).
Net list-price value to the agent (offered − requested, cash included):

| | n | p10 | p25 | median | p75 | p90 | mean |
|---|---|---|---|---|---|---|---|
| at accept | 19,543 | 0 | 0 | **0** | 0 | 0 | −2.7 |
| at reject | 172,199 | −60 | −45 | **−20** | −15 | **+75** | −18.8 |

Reading, stated descriptively: accepts are overwhelmingly value-zero swaps
(the churn loop above); rejects are mostly value-negative as expected, but the
p90 of rejected offers is **+75** — roughly 17k rejected offers carried ≥+75
of list-price value. Whether those rejections were strategically correct
(e.g. the deed completed an opponent group) is not determined here. Per the
task instruction, no proposal is made for this path.

## Unresolved
- Whether the ~17k rejected positive-value offers were correct rejections
  needs opponent-side group accounting per offer; not settled by these traces.
- The 789 never-chosen exch_trade ids: separating "gate never cleared" from
  "feature vector cannot rank them first" requires ranker introspection, not
  traces.
- d″/f are engine-level relaxations shared by all seats; whether enforcing
  them would shift the field ordering cannot be answered from traces of the
  current engine.
