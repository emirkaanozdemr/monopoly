# INTERVENTIONS.md — causal testing of the BUGS.md / GAPS.md gaps

Status: **COMPLETE.** Blocker 0 resolved (cap-20000 regime chosen after the
STOP finding); all arms measured at n=2000 on seeds 962000–963999, paired
McNemar + bootstrap, Bonferroni /4. Verdicts: A REJECT, B MECHANISM-ONLY,
C REJECT (harmful, significant), **D ADOPT (+2.95pp, p=1.7e-08)**,
E REJECT at precheck.

## Blocker 0a — step-cap semantics (RESOLVED, STOP condition met)

**Winner at truncation.** `monopoly_game_engine/env.py`:
- The engine's own terminal rule (`_check_game_over`, env.py:1070): a game
  ends when ≤1 player is solvent **or `round >= 200`** (`max_rounds=200`
  everywhere in this repo).
- `winner()` (env.py:1078): sole survivor if decisive; otherwise
  `max(self.players, key=net_worth)` — **simulator net worth** (deed face
  values + building values + cash, `state.py calculate_net_worth`), not cash.
  **Tie rule: lowest player id wins** (Python `max` keeps the first maximal
  element in player order). Ties are exact-float equality and therefore rare,
  but the tie-break is seat-biased.
- The harness-level `--max-steps 3000` (bench.py / field_ab.py / field_ref.py)
  stops stepping mid-game and reads the same `winner()` — i.e. a net-worth
  snapshot taken **before** the ruleset's own endpoint.

**The actual competition-proxy harness.** The repository's own evaluator —
`ASU_FROZEN_TEACHER/evaluate.py`, the harness behind every ASU record — uses
`DEFAULT_MAX_DECISIONS = 20_000` (evaluate.py:40) with the same
`max_rounds=200` engine. Measured on 300 fresh games (seeds 964000–964299):
every game ends naturally at ≤8,612 steps, so **20,000 never truncates; the
engine's round-200 rule is the real cap.** The 3000-step cap appears nowhere
in the repo's own evaluation path. **The competition cap therefore differs
from 3000 → STOP, per the task rule.**

**How much prior signal is affected.** From the existing 2000-game trace
corpus: 62% of games hit the 3000-step cap at **median round 80**
(p10 72, p90 90) — adjudicated ~120 rounds before the ruleset's endpoint.

**Quantified impact (validation, seeds 964000–964299, n=300 paired, Candidate
D vs strong field, both regimes):**

| | cap 3000 | cap 20000 (= natural end) |
|---|---|---|
| win rate | 36.00% [30.78, 41.58] | 36.67% [31.41, 42.26] |
| decisive games | 38.0% | 47.0% |
| paired delta | — | **+0.67pp, 95% CI [−1.00, +2.67]** |
| outcome flips | — | **8/300 = 2.7%** |
| McNemar exact p | — | 0.73 |

Reading: the 3000-step snapshot picks the same winner as the full round-200
adjudication in 97.3% of games; no measurable systematic bias at n=300. But
the 2.7% per-game flip noise is the same order as the 1–3pp effects the arms
are hunting, and it is pure regime error, not game noise.

**Recommendation (decision needed before arms run):** run all arms at
`--max-steps 20000` (games end naturally; regime question disappears).
Cost: ~2× wall-clock per game (median game 5,357 steps vs 3,000). The Phase 0
teacher baseline currently running at cap 3000 would also need a cap-20000
re-run to stay comparable (teacher games are ~10× agent cost).

## Blocker 0b — Phase 0 arithmetic (RESOLVED)

The earlier chat table printed "40.77% … 466/2000", inviting the reading
466 wins/2000 games. The correct statement at that moment was **190 wins /
466 completed games = 40.77%** — the CI [36.40, 45.29] matches n=466, as
noted. Honest current figure (run still in progress, completed games only):

> **FINAL (n=2000): teacher vs strong field: 790/2000 = 39.50%,
> Wilson [37.38, 41.66]** (cap-3000 regime, same seeds/seats as Candidate D)

Paired against Candidate D on the same 2000 seeds: **+1.10pp**
[−0.90, +3.10], McNemar p = 0.30 — not significant. Candidate D is
statistically at teacher level on the strong field. Note the adopted arm D
(+2.95pp at natural game end) exceeds this teacher gap, though the two
numbers live in different cap regimes and are not directly comparable.

## Blocker 0c — paired testing (RESOLVED)

`competition_agent/paired_ab.py`: joins two seed-matched runs, reports raw
Wilson rates per arm, paired delta with a 10k-resample paired bootstrap CI,
discordant-pair counts, and McNemar's exact p, with a `--bonferroni` divisor.
Used above for the cap validation. Every arm A/B will report the paired test
as primary and raw rates alongside, per protocol.

## Arm E — mortgaged-group build parity (RESOLVED AT PRECHECK: NEGATIVE)

Per-seat rates computed from the existing 2000-game corpus (no new run):

| | agent (1 seat) | field (3 seats, per-capita) |
|---|---|---|
| builds landed | 19,333 | 2,783 |
| builds on a group with a mortgaged deed | 1,569 (**8.12%**) | 158 (**5.67%**) |

The premise ("the agent exploits this deviation less than the field") is
false — the agent already exploits it more, absolutely and per-build. No
implementation, no A/B. Verdict: **REJECT (precheck)** — to be appended to
DECISIONS.md with these numbers.

## Regime decision
Cap 20000 chosen (games end naturally by the engine's round-200 rule).
Baseline arm: Candidate D unchanged, seeds 962000–963999, seat = seed % 4,
strong field, n=2000: **777/2000 = 38.85% [36.74, 41.01]**; mechanism
counters: full_group 34.7%, net-completing trades 1, accepted trades
(agent party) 33,655. All paired tests below: McNemar exact + 10k paired
bootstrap, Bonferroni /4 → α = 0.0125.

## Arm A — cash-for-deed channel — **REJECT**
Implementation: `_propose_trade` first offers cash for a deed completing a
group the agent holds the rest of, capped at 50% of cash (fixed pre-run),
highest multiplier preferred. Exchange scoring untouched. GAP_ARM=A.

| | baseline | arm A |
|---|---|---|
| win rate | 38.85% [36.74, 41.01] | 38.95% [36.84, 41.11] |
| paired Δ | — | **+0.10pp** [+0.00, +0.25], McNemar p = 0.5 |
| gap_fires | 0 | 528,545 |
| proposals → accepted | 207,098 → 301 | 575,544 → 224 |
| full_group / net_complete | 34.7% / 1 | 34.7% / 0 |

The channel opened and fired 264×/game; the strong field declines cash
offers exactly as it declines exchanges (~0.04%). Only 2/2000 games changed
outcome. No mechanism counter moved. The gap (buy_trade 0/34.8M) is real
but closing it buys nothing against this field: acquisition-by-trade is
dead at the counterparty, not at the proposer. REJECT.

## Arm B — reject net≤0 offers — **MECHANISM-ONLY**
Implementation: `_trade_reply` declines any offer with net list-value ≤ 0
before the deed_value evaluation; positive handling untouched. GAP_ARM=B.

| | baseline | arm B |
|---|---|---|
| win rate | 38.85% [36.74, 41.01] | 37.85% [35.75, 40.00] |
| paired Δ | — | **−1.00pp** [−2.70, +0.70], McNemar p = 0.28 |
| gap_fires | 0 | 368,269 |
| accepted trades (agent party) | 33,655 | **884 (−97%)** |
| full_group / net_complete | 34.7% / 1 | 32.0% / 3 |
| discordant pairs | — | 310 |

The churn loop (BUGS.md h; 38,120 cyclic re-trades) is eliminated, with **no
measurable win-rate effect** — the point estimate is mildly negative. This is
the causal test H3 could not deliver as an association: churn does not cause
losses at any detectable size. Logged as MECHANISM-ONLY per protocol; not
retuned.

## Arm C — accept net≥+50 offers — **REJECT (harmful, significant)**
Implementation: `_trade_reply` accepts any offer with net list-value ≥ +50
after the solvency guards; threshold fixed pre-run. GAP_ARM=C.

| | baseline | arm C |
|---|---|---|
| win rate | 38.85% [36.74, 41.01] | 35.50% [33.43, 37.62] |
| paired Δ | — | **−3.35pp** [−4.20, −2.55], McNemar p = 1.4e-17 |
| gap_fires | 0 | 335 |
| discordant pairs | — | 3 wins gained, 70 lost |
| bankrupt | 28.7% | 35.2% |

335 firings cost 3.35pp — roughly one lost game per five acceptances. The
~17k "rejected positive offers" flagged in BUGS.md were CORRECT rejections:
the field offers list-value surplus precisely when the trade is
strategically advantageous to itself. This also closes BUGS.md's Unresolved
item on those rejections, causally. REJECT.

## Arm D — unconditional buy on completing/blocking deeds — **ADOPT**
Implementation: `_buy` overrides the A3 cash gate and buys any affordable
unowned real-estate deed that completes an agent group or is the last
missing piece of a single opponent's group. Not conditioned on decline rate
(the H4 confounder). GAP_ARM=D.

| | baseline | arm D |
|---|---|---|
| win rate | 38.85% [36.74, 41.01] | **41.80% [39.66, 43.98]** |
| paired Δ | — | **+2.95pp** [+1.90, +4.00], McNemar p = 1.7e-08 |
| gap_fires | 0 | 1,255 (0.63/game) |
| full_group | 34.7% | **38.1%** |
| net_complete / accepted | 1 / 33,655 | 3 / 31,058 |
| bankrupt | 28.7% | 26.2% |

Significant after Bonferroni (p « 0.0125), mechanism moves coherently with
the win rate (group formation +3.4pp, bankruptcy −2.5pp), and the
intervention is surgical (0.63 firings/game). 85 games flipped to wins
against 26 to losses. ADOPT.

## Arms A–D — protocol (as pre-registered)

Ready to implement per spec once the regime is chosen:
- **A** `gap/arm-a-cash-for-deed` — extend `_propose_trade` with buy_trade
  offers targeting group-completing deeds; affordability cap fixed at 50% of
  current cash (stated here, before any run; not tunable mid-run).
  Legality confirmed: `buy_trade` actions are generated for the agent by
  `env._trade_offer_actions` and appear ~14.2M times in its legal sets
  (BUGS.md); the ruleset does not forbid them — only SPEC I1 (a probe-derived
  teacher observation, not a rule) excluded them.
- **B** `gap/arm-b-reject-zero-value-trades` — `_trade_reply`: reject net
  list-value ≤ 0 offers; positive handling untouched.
- **C** `gap/arm-c-accept-positive-offers` — `_trade_reply`: accept offers
  with net list-value ≥ +50 (single threshold, fixed here before any run).
- **D** `gap/arm-d-group-completing-buy` — buy_decision: on an unowned deed
  that completes an agent group or blocks an opponent's 2/3 group, buy
  whenever cash allows, overriding the gate; override-fire count reported.

Protocol per arm (unchanged from task): n=2000, seeds 962000–963999,
seat = seed % 4, strong field, one intervention per arm, paired McNemar +
bootstrap vs the shared baseline arm, Bonferroni across arms actually run,
mechanism counters (group-completion rate, net-completing trades,
accepted-trade count) reported regardless of the win-rate outcome.

## Unresolved
- The regime decision (cap 20000 vs 3000) — user call, blocking arms A–D.
- Teacher Phase 0 at n=2000 still in flight; a cap-20000 teacher baseline
  does not exist yet.
- The 2.7% flip rate was measured at n=300; if arms run at cap 3000 anyway,
  a larger flip-rate bound may be needed to interpret sub-3pp deltas.
