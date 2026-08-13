# INTERVENTIONS.md — causal testing of the BUGS.md / GAPS.md gaps

Status: **STOPPED at Blocker 0a per task rule** — the competition-harness cap
differs from the 3000 used by every prior measurement. Findings and the
quantified impact are below; no arm has been run. Arms A–D are implemented
nowhere yet; Arm E resolved at its precheck (negative). Awaiting a decision on
the measurement regime before any A/B.

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

> **teacher vs strong field: 223/571 = 39.05%, Wilson [35.14, 43.12]**
> (decisive 38.0%; 62% of teacher games step-capped — the teacher baseline
> sits in the same truncated regime as everything else)

The n=2000 figure will replace this when the run finishes. No finding is
interpreted against the interim number. GAPS.md's Phase 0 table remains
"PENDING" until then.

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

## Arms A–D — NOT RUN (gated on the Blocker 0a decision)

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
