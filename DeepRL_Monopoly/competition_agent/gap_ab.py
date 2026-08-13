"""A/B runner for the gap-closing intervention arms (INTERVENTIONS.md).

Same seat convention and loop as field_ab.py (seat = seed % 4, strong field),
plus the three mechanism counters the measurement protocol requires, so a
null win-rate result is diagnosable:

  full_group        agent ever holds a complete real-estate color group
  net_complete      accepted trades that NET-complete an agent group
                    (deed received completes it counting the deed given away)
  accepted_party    accepted trades with the agent as a party (either side)
  gap_fires         how often the arm's intervention actually fired
                    (read from the policy's `gap_fires` attribute if present)

Arm selection is via the GAP_ARM environment variable, read by spec_policy at
import time on the arm branches; this runner only records. Baseline = GAP_ARM
unset on an arm-free checkout.

Usage:
  GAP_ARM=A python3 competition_agent/gap_ab.py --tag armA --workers 4
  python3 competition_agent/gap_ab.py --tag base --workers 4        # baseline
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monopoly_game_engine.actions import OFFSETS, ActionType  # noqa: E402
from monopoly_game_engine.constants import COLOR_GROUPS, PROPERTY_IDS  # noqa: E402

from competition_agent.policies import build_policy  # noqa: E402
from competition_agent.proc import ensure_hash_seed, managed_pool  # noqa: E402

FIELD = ("fixed-b", "fixed-d", "fixed-e")
ACCEPT = int(ActionType.ACCEPT_TRADE)
DECLINE = int(ActionType.DECLINE_TRADE)
RE_GROUPS = {c: sqs for c, sqs in COLOR_GROUPS.items()
             if c not in ("railroad", "utility")}


def wilson(k, n, z=1.96):
    if not n:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - m), min(1.0, c + m)


def _agent_completes(env, seat, gains_sq, loses_sq) -> bool:
    """Would ownership {current} + gains − loses complete gains_sq's group?"""
    for c, sqs in RE_GROUPS.items():
        if gains_sq not in sqs:
            continue
        return all(
            (x == gains_sq)
            or (env.properties[x].owner == seat and x != loses_sq)
            for x in sqs)
    return False


def _holds_full_group(env, seat) -> bool:
    return any(all(env.properties[x].owner == seat for x in sqs)
               for sqs in RE_GROUPS.values())


def _game(job):
    seed, max_steps = job
    from ASU_FROZEN_TEACHER.evaluate import _new_seeded_game
    seat = seed % 4
    game = _new_seeded_game(seed)
    env = game.env
    agents, fill = {}, list(FIELD)
    for s in range(4):
        agents[s] = (build_policy("final", s, seed * 4 + s) if s == seat
                     else build_policy(fill.pop(0), s, seed * 4 + s))

    steps = proposed = accepted = declined = 0
    net_complete = accepted_party = 0
    full_group = False
    pending_from_agent = False
    t0 = time.perf_counter()
    while not env.done and steps < max_steps:
        actor = env.whose_turn()
        a = int(agents[actor].choose_action(env))
        if actor == seat and OFFSETS["buy_trade"] <= a < OFFSETS["auction"]:
            proposed += 1
            pending_from_agent = True
        elif pending_from_agent and actor != seat and a in (ACCEPT, DECLINE):
            accepted += a == ACCEPT
            declined += a == DECLINE
            pending_from_agent = False
        if a == ACCEPT:
            entry = env._incoming_trade_entry(actor)
            if entry is not None:
                sender, offer = entry
                if seat in (sender, offer.to_player):
                    accepted_party += 1
                    off = (offer.offered_prop.square_id
                           if offer.offered_prop else None)
                    req = (offer.requested_prop.square_id
                           if offer.requested_prop else None)
                    if offer.to_player == seat and off is not None:
                        if _agent_completes(env, seat, off, req):
                            net_complete += 1
                    elif sender == seat and req is not None:
                        if _agent_completes(env, seat, req, off):
                            net_complete += 1
        game.step(a)
        steps += 1
        if not full_group and _holds_full_group(env, seat):
            full_group = True

    pol = getattr(agents[seat], "policy", None)
    gap_fires = int(getattr(pol, "gap_fires", 0)) if pol is not None else 0
    active = [p.player_id for p in env.players if not p.bankrupt]
    decisive = len(active) == 1
    return {
        "seed": seed, "seat": seat, "steps": steps, "decisive": decisive,
        "leader_win": env.winner() == seat,
        "bankrupt": bool(env.players[seat].bankrupt),
        "proposed": proposed, "accepted": accepted, "declined": declined,
        "full_group": full_group, "net_complete": net_complete,
        "accepted_party": accepted_party, "gap_fires": gap_fires,
        "seconds": time.perf_counter() - t0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--seed-base", type=int, default=962000)
    ap.add_argument("--max-steps", type=int, default=20000,
                    help="20000 = effectively uncapped; the engine's "
                         "round-200 rule ends every game first (Blocker 0a)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tag", type=str, required=True)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    ensure_hash_seed()

    out = (Path(__file__).resolve().parent / "probes"
           / f"gap_{args.tag}.json")
    partial = out.with_suffix(".partial.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)

    done = {}
    if partial.exists() and not args.no_resume:
        for line in partial.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["seed"]] = r
        print(f"resuming: {len(done)} recorded")

    jobs = [(args.seed_base + k, args.max_steps) for k in range(args.games)]
    todo = [j for j in jobs if j[0] not in done]
    rows = [r for r in done.values()
            if 0 <= r["seed"] - args.seed_base < args.games]
    print(f"gap A/B arm={os.environ.get('GAP_ARM', '-')}   tag={args.tag}   "
          f"field={FIELD}   cap={args.max_steps}")
    print(f"{len(todo)} to play, {len(rows)} reused")

    t0 = time.time()
    if todo:
        with partial.open("a") as sink, managed_pool(args.workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_game, todo), 1):
                rows.append(r)
                sink.write(json.dumps(r) + "\n")
                sink.flush()
                if i % 100 == 0 or i == len(todo):
                    el = (time.time() - t0) / 60
                    rate = i / max(el, 1e-9)
                    print(f"  {i}/{len(todo)}  {rate:.0f} g/min  "
                          f"ETA {(len(todo) - i) / max(rate, 1e-9):.1f} min",
                          flush=True)
    out.write_text(json.dumps(rows, indent=1))

    n = len(rows)
    k = sum(r["leader_win"] for r in rows)
    p, lo, hi = wilson(k, n)
    print(f"\n{args.tag}: {k}/{n}  {100*p:.2f}%  [{100*lo:.2f}, {100*hi:.2f}]")
    print(f"  mechanism: full_group {100*sum(r['full_group'] for r in rows)/n:.1f}%   "
          f"net_complete {sum(r['net_complete'] for r in rows)}   "
          f"accepted(agent party) {sum(r['accepted_party'] for r in rows)}   "
          f"gap_fires {sum(r['gap_fires'] for r in rows)}")
    print(f"  proposals {sum(r['proposed'] for r in rows)}  "
          f"accepted-of-ours {sum(r['accepted'] for r in rows)}   "
          f"decisive {100*sum(r['decisive'] for r in rows)/n:.1f}%   "
          f"bankrupt {100*sum(r['bankrupt'] for r in rows)/n:.1f}%")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
