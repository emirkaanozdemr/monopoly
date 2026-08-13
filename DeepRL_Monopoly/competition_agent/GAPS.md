# GAPS.md — statistical findings (strategic gaps)

Ruleset `ppo-plus-v2`. Candidate D vs strong field (fixed-b/d/e), seeds
960000..961999, seat = seed % 4. Split declared before analysis
(`traces/SPLIT.md`): **EXPLORE = 960000..960999, CONFIRM = 961000..961999**.
Hypotheses frozen in `traces/GAPS_prereg.md` before any CONFIRM read.
Bonferroni across the 4 hypotheses tested: per-test α = 0.0125.
All results are associations measured in traces; none is a causal claim.

## Phase 0 — baselines on the identical harness, seeds, and game count

| policy | wins / games | rate | 95% Wilson CI |
|---|---|---|---|
| ASU_FROZEN_TEACHER (value variant) | PENDING | PENDING | PENDING |
| Candidate D (`final`, ranker on) | 768 / 2000 | 38.40% | [36.29, 40.55] |
| Candidate D base arm (ranker off) | 759 / 2000 | 37.95% | [35.85, 40.10] |
| seat parity | — | 25.00% | — |

(Teacher run in progress, n=2000, same `field_ref.py` seat convention;
interim at n=200: 37.50% [31.09, 44.39]. This section is updated when the
run completes; no finding below is interpreted against the interim number.)

Context from the runs' own proposal accounting: the strong field accepts
0.020% of Candidate D's proposals and 0.035% of the teacher's — near-zero
acceptance is a property of the field, not an agent defect.

## Won-vs-lost control (mandatory) — EXPLORE only, n=1000, win rate 39.4%

Anomalies DROPPED because they appear at ~the same rate in won and lost games:

| candidate anomaly | won | lost | verdict |
|---|---|---|---|
| proposal volume (share of decisions) | 9.6% | 9.0% | dropped |
| jail turns per game | 5.9 | 6.7 | dropped |
| auction wins / spend per game | 3.8 / $844 | 3.3 / $762 | dropped |
| game hit 3000-step cap | 61.4% | 61.6% | dropped |
| built when a build was available | 100% | 94.6% | dropped |
| declined a group-COMPLETING deed (games) | 10.4% | 11.2% | dropped |

The last two matter: **build conversion is not the gap** (the agent builds
essentially whenever it legally can), and declining specifically completing
deeds is rare and outcome-independent. The gap is upstream: reaching a
complete group at all.

## Hypotheses and CONFIRM results (n=1000, win rate 37.4%)

### H1 — wins run through completing a color group — **CONFIRMED**
Pre-registered claim: P(win | agent ever holds a complete real-estate group)
− P(win | never) ≥ +0.40.

| | EXPLORE | CONFIRM |
|---|---|---|
| P(win \| ≥1 complete group) | 0.761 (n=447) | **0.727** [0.683, 0.766] (n=439) |
| P(win \| none) | 0.098 (n=553) | **0.098** [0.076, 0.125] (n=561) |
| Δ | +0.663 | **+0.629** |

z = 20.39, one-sided p = 1.1e-92 « 0.0125. Association only — winning also
causes acquisition — but the size and the stability across halves make group
acquisition the variable that separates outcomes. The agent reaches a
complete group in only ~44% of games.

### H2 — the trade system contributes ~nothing to group formation — **CONFIRMED**
Pre-registered claim: fraction of CONFIRM games with ≥1 accepted trade that
net-completes an agent group < 0.02, and agent-proposed accepted completions = 0.

CONFIRM: **7/1000 games (0.70%, Wilson [0.34%, 1.44%])**; agent-proposed
accepted trades completing a group: **0** (EXPLORE: 13/1000, 0).

Combined with BUGS.md coverage (cash-trade offers dead by spec; the field
accepts 0.02% of exchange proposals; accepted incoming trades are median-zero
value swaps), every practical channel by which trading could complete a group
is closed. Group acquisition happens through buying and auctions or not at
all. This bounds what any trade-proposal improvement (including the Candidate
D ranker, +0.45pp non-significant on this field) can deliver here.

### H4 — high buy-decline rate is associated with losing — **CONFIRMED**
Pre-registered claim: P(win | per-game decline rate ≥ 0.25) − P(win | < 0.25)
≤ −0.08. (Decline = not buying at a buy_decision; threshold = EXPLORE median.)

| | EXPLORE | CONFIRM |
|---|---|---|
| P(win \| decline ≥ 0.25) | 0.320 (n=581) | **0.312** [0.275, 0.351] (n=567) |
| P(win \| decline < 0.25) | 0.498 (n=418) | **0.455** [0.409, 0.502] (n=433) |
| Δ | −0.177 | **−0.143** |

z = 4.62, one-sided p = 1.9e-06 < 0.0125. Recorded confounder (declared at
pre-registration): cash poverty both triggers the purchase gate and results
from losing; this is an association bound, not a causal estimate. Supporting
EXPLORE-only observation: after an agent decline, an opponent completed a
group on that very deed at auction 0.120×/game in losses vs 0.046×/game in
wins (~2.6×).

### H3 — accept-loop churn is associated with losing — **NOT CONFIRMED**
Pre-registered claim: P(win | churn ≥ 10) − P(win | churn < 10) ≤ −0.05.

| | EXPLORE | CONFIRM |
|---|---|---|
| P(win \| churn ≥ 10) | 0.339 (n=221) | 0.323 [0.266, 0.386] (n=229) |
| P(win \| churn < 10) | 0.409 (n=779) | 0.389 [0.355, 0.424] (n=771) |
| Δ | −0.070 | −0.066 |

z = 1.81, one-sided p = 0.035 > 0.0125 (Bonferroni). The point estimate met
the pre-registered −0.05 and replicated across halves, but it does not
survive correction at this sample size. The churn loop itself is documented
deterministically in BUGS.md (h); its win-rate cost, if any, is bounded small.
Reported as unconfirmed; not carried as a result.

## Ranking (as pre-registered, updated by results)
1. **H1** — group acquisition separates outcomes (+62.9pp).
2. **H2** — the entire trade apparatus is a no-op for group acquisition;
   purchase and auctions are the only live channel.
3. **H4** — the purchase gate's decline rate is where games diverge
   (−14.3pp), subject to the recorded cash confounder.
4. H3 — direction consistent, effect ≤ ~7pp, unconfirmed.

## Unresolved
- Phase 0 teacher number pending (section above updates on completion);
  whether ~38% is at, above, or below teacher level on this field decides
  whether these gaps represent recoverable headroom.
- H4's causal direction cannot be settled from traces: it needs an
  intervention (e.g. a gate-threshold A/B on fresh seeds), which is agent
  modification and out of scope for this task.
- Whether H1's 44% group-acquisition rate is low *for this field* is not
  answerable without the same feature mined from teacher-vs-strong-field
  traces (not instrumented in this task).
- The churn loop's cost may be indirect (occupying the field's single
  pending-trade slot); the traces cannot separate that from zero effect.
