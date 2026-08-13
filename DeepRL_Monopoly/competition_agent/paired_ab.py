"""Paired A/B analysis for seed-matched field runs (Blocker 0c).

Both arms play the same seeds from the same seats, so per-seed outcomes pair
exactly. Primary statistic: McNemar's exact test on discordant pairs, plus a
paired bootstrap CI on the win-rate delta. Raw per-arm rates with Wilson CIs
are reported alongside, never as the headline.

Usage:
  python3 competition_agent/paired_ab.py \
      --a probes/field_strong_base.partial.jsonl \
      --b probes/field_strong_rank.partial.jsonl \
      [--key leader_win] [--boot 10000] [--bonferroni 1]
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path


def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - m), min(1.0, c + m)


def mcnemar_exact(b01: int, b10: int) -> float:
    """Two-sided exact binomial test on discordant pairs."""
    n = b01 + b10
    if n == 0:
        return 1.0
    k = min(b01, b10)
    # two-sided: 2 * P(X <= k | Bin(n, 0.5)), capped at 1
    acc = 0.0
    for i in range(k + 1):
        acc += math.comb(n, i)
    p = 2.0 * acc / (2.0 ** n)
    return min(1.0, p)


def load(path: str, key: str) -> dict:
    out = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["seed"]] = bool(r[key])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="baseline arm JSONL")
    ap.add_argument("--b", required=True, help="intervention arm JSONL")
    ap.add_argument("--key", default="leader_win")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--bonferroni", type=int, default=1,
                    help="number of arms in the family; reported alpha is "
                         "0.05/this")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap RNG seed")
    args = ap.parse_args()

    a = load(args.a, args.key)
    b = load(args.b, args.key)
    seeds = sorted(set(a) & set(b))
    only_a, only_b = len(a) - len(seeds), len(b) - len(seeds)
    if only_a or only_b:
        print(f"WARNING: unpaired seeds dropped (a-only {only_a}, "
              f"b-only {only_b}); paired n = {len(seeds)}")

    pairs = [(a[s], b[s]) for s in seeds]
    n = len(pairs)
    ka = sum(x for x, _ in pairs)
    kb = sum(y for _, y in pairs)
    b01 = sum(1 for x, y in pairs if not x and y)   # B wins where A lost
    b10 = sum(1 for x, y in pairs if x and not y)   # A wins where B lost

    pa = wilson(ka, n)
    pb = wilson(kb, n)
    delta = (kb - ka) / n
    p_mcnemar = mcnemar_exact(b01, b10)

    rng = random.Random(args.seed)
    boots = []
    for _ in range(args.boot):
        d = 0
        for _ in range(n):
            x, y = pairs[rng.randrange(n)]
            d += y - x
        boots.append(d / n)
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[int(0.975 * len(boots))]

    alpha = 0.05 / args.bonferroni
    print(f"paired n = {n}")
    print(f"A: {ka}/{n} = {100*pa[0]:.2f}%  Wilson [{100*pa[1]:.2f}, {100*pa[2]:.2f}]")
    print(f"B: {kb}/{n} = {100*pb[0]:.2f}%  Wilson [{100*pb[1]:.2f}, {100*pb[2]:.2f}]")
    print(f"paired delta (B-A): {100*delta:+.2f}pp  "
          f"bootstrap 95% CI [{100*lo:+.2f}, {100*hi:+.2f}]")
    print(f"discordant pairs: B-wins-where-A-lost {b01}, "
          f"A-wins-where-B-lost {b10}")
    print(f"McNemar exact p = {p_mcnemar:.4g}   "
          f"alpha = {alpha:.4g} (Bonferroni /{args.bonferroni})   "
          f"-> {'SIGNIFICANT' if p_mcnemar < alpha else 'not significant'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
