# Phase 4 pre-registration — frozen before any CONFIRM seed is read

Date: 2026-08-13. EXPLORE = seeds 960000..960999, CONFIRM = 961000..961999
(traces/SPLIT.md, declared before mining). All evidence below is EXPLORE-only.
Bonferroni correction: 4 hypotheses, α = 0.05 → per-test α = 0.0125.
All tests are two-proportion one-sided z-tests on CONFIRM games, effect sizes
reported as proportion differences with a 95% Wilson interval on each arm.

Dropped by the won-vs-lost control (same rate in both, cannot be a cause of
losing): proposal volume (9.6% vs 9.0% of decisions), jail turns (5.9 vs 6.7),
auction wins (3.8 vs 3.3) and spend (844 vs 762), step-cap rate (61.4% vs
61.6%), build-conversion-given-opportunity (100% vs 94.6%), declining a
group-COMPLETING deed (10.4% vs 11.2% of games).

## H1 (rank 1) — wins run through completing a color group; the agent mostly doesn't
Claim: on CONFIRM, P(win | agent ever holds a complete real-estate group)
− P(win | never) ≥ +0.40.
EXPLORE: 0.761 (n=447) vs 0.098 (n=553), Δ = +0.663.
Association only; no causal claim (winning also causes acquisition).

## H2 (rank 2) — the trade system contributes ~nothing to group formation
Claim: on CONFIRM, the fraction of games containing at least one accepted
trade that NET-completes a color group for the agent (receiving deed completes
the group counting the deed simultaneously given away) is < 0.02, and the
number of AGENT-proposed accepted trades that complete an agent group is 0.
EXPLORE: 13/1000 games (1.3%); agent-proposed accepted completions: 0.
Test: Wilson interval on the CONFIRM proportion; falsified if ≥ 0.02.

## H3 (rank 4) — accept-loop churn is associated with losing
Claim: on CONFIRM, P(win | churn ≥ 10) − P(win | churn < 10) ≤ −0.05,
where churn = accepted trades returning a deed to a previous owner within 10
rounds with the agent as a party.
EXPLORE: 0.339 (n=221) vs 0.409 (n=779), Δ = −0.070.

## H4 (rank 3) — high buy-decline rate is associated with losing
Claim: on CONFIRM, P(win | per-game decline rate ≥ 0.25) − P(win | < 0.25)
≤ −0.08, where decline rate = fraction of the agent's buy_decision family
decisions where it does not buy. Threshold 0.25 = EXPLORE median, frozen here.
EXPLORE: 0.320 (n=581) vs 0.498 (n=418), Δ = −0.177.
Confounding note (recorded now): cash poverty both causes declines (the
safety gate) and results from losing; CONFIRM can only bound the association.

No further hypotheses. Ranking (most to least important if confirmed):
H1, H2, H4, H3.
