"""Instrumented benchmark pass: full per-decision traces of policy-vs-field games.

Diagnosis only — no agent behavior is modified. The game loop is copied from
`field_ref.py` / `field_ab.py` (seat = seed % 4 for 1-vs-3 fields) so that a
traced game is the *same* game as the corresponding row in the un-instrumented
field runs; `--verify-against` diffs per-seed outcomes against such a run to
prove the tracer does not perturb play.

Trace format
------------
One gzip'd JSONL file per game: <out-dir>/seed_<seed>.jsonl.gz
  line 1   header  {"kind":"header", seed, policy, field, seat, turn_order}
  line 2.. decision rows, one per env.step() call:
    t      step index (0-based)
    rd     env.round at decision time
    ph     phase                     hr   has_rolled
    p      acting player id (whose_turn)
    fam    decision-family label (derived from context, see _family)
    legal  full legal action set (sorted ints)
    a      chosen action
    cash   [4] player cash BEFORE the action
    nw     [4] player net worth BEFORE the action
    pos    [4] positions             jail [4] in_jail    jt [4] jail_turns
    own    [28] deed owner (-1 = bank), PROPERTY_IDS order
    mort   [28] mortgaged flags
    h      [22] house counts (5 = hotel), REAL_ESTATE_IDS order
    ha,ho  bank houses / hotels available
    debt   [debt_player, debt_creditor, debt_amount] (-1,-1,0 when none)
    pt     pending trades [[from,to,off_sq,req_sq,cash_off,cash_req],...]
    inc    incoming offer for the actor (same tuple) or null
    info   the info dict returned by env.step (dice, rent_paid, landed_on,
           auction_winner/price, ...)
  last     footer  {"kind":"footer", steps, decisive, winner, leader,
                    step_capped, net_worth, bankrupt, cash, own, h, ha, ho}

Replay: game state is fully determined by (seed, PYTHONHASHSEED=0, policy
code); rerunning the same seed must reproduce the file byte-for-byte
(gzip written with mtime=0 so the archive bytes are stable too).
"""

from __future__ import annotations

import argparse
import gzip
import json
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
from monopoly_game_engine.constants import PROPERTY_IDS, REAL_ESTATE_IDS  # noqa: E402

from competition_agent.policies import build_policy  # noqa: E402
from competition_agent.proc import ensure_hash_seed, managed_pool  # noqa: E402

FIELDS = {
    "strong": ("fixed-b", "fixed-d", "fixed-e"),
    "weak": ("fixed-a", "fixed-b", "fixed-c"),
}
ACCEPT = int(ActionType.ACCEPT_TRADE)
DECLINE = int(ActionType.DECLINE_TRADE)


def _family(env, pid: int, legal: list) -> str:
    """Context-derived decision family. Mirrors spec_policy's rule order."""
    if env.phase == "auction":
        return "auction"
    if getattr(env, "debt_player", None) == pid:
        return "debt_rescue"
    if ACCEPT in legal:
        return "trade_reply"
    if env.phase == "post_roll" and not env.has_rolled:
        return "roll"
    if env.phase == "post_roll":
        return ("buy_decision"
                if int(ActionType.BUY_PROPERTY) in legal else "post_roll_manage")
    if env.phase == "pre_roll":
        return "pre_roll_manage"
    if env.phase == "out_of_turn":
        return "oot_offer"
    return "other"


def _offer_tuple(sender: int, offer) -> list:
    return [
        sender,
        offer.to_player,
        offer.offered_prop.square_id if offer.offered_prop else -1,
        offer.requested_prop.square_id if offer.requested_prop else -1,
        offer.cash_offered,
        offer.cash_requested,
    ]


def trace_game(seed: int, field: str, policy: str, max_steps: int,
               out_dir: Path) -> dict:
    from ASU_FROZEN_TEACHER.evaluate import _new_seeded_game

    seat = seed % 4                    # field_ab / field_ref convention
    game = _new_seeded_game(seed)
    env = game.env

    agents, fill = {}, list(FIELDS[field])
    for s in range(4):
        agents[s] = (build_policy(policy, s, seed * 4 + s) if s == seat
                     else build_policy(fill.pop(0), s, seed * 4 + s))

    path = out_dir / f"seed_{seed}.jsonl.gz"
    tmp = path.with_suffix(".tmp")
    steps = 0
    t0 = time.perf_counter()
    # mtime=0 keeps the gzip container byte-stable across reruns.
    with open(tmp, "wb") as raw, gzip.GzipFile(
            fileobj=raw, mode="wb", mtime=0) as zf:
        def emit(obj):
            zf.write((json.dumps(obj, separators=(",", ":")) + "\n")
                     .encode("utf-8"))

        emit({"kind": "header", "seed": seed, "policy": policy,
              "field": field, "seat": seat,
              "turn_order": list(env.turn_order)})

        while not env.done and steps < max_steps:
            actor = env.whose_turn()
            legal = sorted(int(x) for x in env.get_allowed_actions(actor))
            row = {
                "t": steps,
                "rd": int(env.round),
                "ph": env.phase,
                "hr": bool(env.has_rolled),
                "p": actor,
                "fam": _family(env, actor, legal),
                "legal": legal,
                "cash": [int(p.cash) for p in env.players],
                "nw": [float(p.net_worth()) for p in env.players],
                "pos": [int(p.position) for p in env.players],
                "jail": [int(p.in_jail) for p in env.players],
                "jt": [int(p.jail_turns) for p in env.players],
                "own": [(-1 if env.properties[sq].owner is None
                         else int(env.properties[sq].owner))
                        for sq in PROPERTY_IDS],
                "mort": [int(env.properties[sq].mortgaged)
                         for sq in PROPERTY_IDS],
                "h": [int(env.properties[sq].houses)
                      for sq in REAL_ESTATE_IDS],
                "ha": int(env.houses_available),
                "ho": int(env.hotels_available),
                "debt": [
                    -1 if env.debt_player is None else int(env.debt_player),
                    -1 if env.debt_creditor is None else int(env.debt_creditor),
                    int(env.debt_amount),
                ],
                "pt": [_offer_tuple(s_, o) for s_, o in
                       sorted(env.pending_trades.items())],
            }
            inc = env._incoming_trade_entry(actor)
            row["inc"] = None if inc is None else _offer_tuple(inc[0], inc[1])

            action = int(agents[actor].choose_action(env))
            row["a"] = action
            _, _, _, info = game.step(action)
            # info may contain tuples (dice); json turns them into lists.
            row["info"] = info
            emit(row)
            steps += 1

        active = [p.player_id for p in env.players if not p.bankrupt]
        decisive = len(active) == 1
        emit({"kind": "footer", "steps": steps,
              "decisive": decisive,
              "winner": active[0] if decisive else None,
              "leader": env.winner(),
              "step_capped": steps >= max_steps and not env.done,
              "net_worth": [float(p.net_worth()) for p in env.players],
              "bankrupt": [bool(p.bankrupt) for p in env.players],
              "cash": [int(p.cash) for p in env.players],
              "own": [(-1 if env.properties[sq].owner is None
                       else int(env.properties[sq].owner))
                      for sq in PROPERTY_IDS],
              "h": [int(env.properties[sq].houses) for sq in REAL_ESTATE_IDS],
              "ha": int(env.houses_available), "ho": int(env.hotels_available)})
    os.replace(tmp, path)

    return {"seed": seed, "seat": seat, "steps": steps,
            "decisive": decisive,
            "leader_win": env.winner() == seat,
            "bankrupt": bool(env.players[seat].bankrupt),
            "seconds": time.perf_counter() - t0}


def plain_game(seed: int, field: str, policy: str, max_steps: int) -> dict:
    """Un-instrumented control for overhead measurement. Same loop, no writes."""
    from ASU_FROZEN_TEACHER.evaluate import _new_seeded_game

    seat = seed % 4
    game = _new_seeded_game(seed)
    env = game.env
    agents, fill = {}, list(FIELDS[field])
    for s in range(4):
        agents[s] = (build_policy(policy, s, seed * 4 + s) if s == seat
                     else build_policy(fill.pop(0), s, seed * 4 + s))
    steps = 0
    t0 = time.perf_counter()
    while not env.done and steps < max_steps:
        actor = env.whose_turn()
        game.step(int(agents[actor].choose_action(env)))
        steps += 1
    return {"seed": seed, "seat": seat, "steps": steps,
            "leader_win": env.winner() == seat,
            "seconds": time.perf_counter() - t0}


def _worker(job):
    seed, field, policy, max_steps, out_dir = job
    return trace_game(seed, field, policy, max_steps, Path(out_dir))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", choices=sorted(FIELDS), default="strong")
    ap.add_argument("--policy", default="final")
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--seed-base", type=int, default=960000)
    ap.add_argument("--max-steps", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out-dir", type=str, required=True)
    ap.add_argument("--replay-check", type=int, default=0, metavar="N",
                    help="replay the first N already-traced games and require "
                         "byte-identical traces; exits non-zero on mismatch")
    ap.add_argument("--overhead-check", type=int, default=0, metavar="N",
                    help="time N games instrumented vs plain (single process) "
                         "and report the overhead")
    ap.add_argument("--verify-against", type=str, default=None,
                    help="partial.jsonl of an un-instrumented run; per-seed "
                         "leader_win must match")
    args = ap.parse_args()
    ensure_hash_seed()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── overhead measurement ──────────────────────────────────────────────
    if args.overhead_check:
        seeds = [args.seed_base + k for k in range(args.overhead_check)]
        scratch = out_dir / "_overhead_scratch"
        scratch.mkdir(exist_ok=True)
        t_plain = t_traced = 0.0
        for s in seeds:                       # interleave to be load-fair
            t_traced += trace_game(s, args.field, args.policy,
                                   args.max_steps, scratch)["seconds"]
            t_plain += plain_game(s, args.field, args.policy,
                                  args.max_steps)["seconds"]
        pct = 100.0 * (t_traced - t_plain) / t_plain
        print(f"overhead over {len(seeds)} games: plain {t_plain:.2f}s  "
              f"traced {t_traced:.2f}s  -> {pct:+.1f}%")
        return 0

    # ── replay verification ───────────────────────────────────────────────
    if args.replay_check:
        seeds = [args.seed_base + k for k in range(args.replay_check)]
        bad = 0
        scratch = out_dir / "_replay_scratch"
        scratch.mkdir(exist_ok=True)
        for s in seeds:
            orig = out_dir / f"seed_{s}.jsonl.gz"
            if not orig.exists():
                print(f"seed {s}: no original trace; run the main pass first")
                bad += 1
                continue
            trace_game(s, args.field, args.policy, args.max_steps, scratch)
            replayed = scratch / f"seed_{s}.jsonl.gz"
            a = gzip.open(orig, "rb").read()
            b = gzip.open(replayed, "rb").read()
            raw_same = orig.read_bytes() == replayed.read_bytes()
            if a == b and raw_same:
                print(f"seed {s}: OK ({len(a)} bytes, archive identical)")
            else:
                print(f"seed {s}: MISMATCH (content_equal={a == b}, "
                      f"archive_equal={raw_same})")
                bad += 1
        print(f"replay check: {len(seeds) - bad}/{len(seeds)} identical")
        return 1 if bad else 0

    # ── main traced pass ──────────────────────────────────────────────────
    jobs = []
    for k in range(args.games):
        s = args.seed_base + k
        if not (out_dir / f"seed_{s}.jsonl.gz").exists():
            jobs.append((s, args.field, args.policy, args.max_steps,
                         str(out_dir)))
    print(f"{args.policy} vs {FIELDS[args.field]}  traced pass  "
          f"{len(jobs)} to play, {args.games - len(jobs)} already on disk")

    results = []
    t0 = time.time()
    if jobs:
        with managed_pool(args.workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_worker, jobs), 1):
                results.append(r)
                if i % 100 == 0 or i == len(jobs):
                    el = (time.time() - t0) / 60
                    rate = i / max(el, 1e-9)
                    print(f"  {i}/{len(jobs)}  {rate:.0f} g/min  "
                          f"ETA {(len(jobs) - i) / max(rate, 1e-9):.1f} min",
                          flush=True)

    # ── outcome verification against an un-instrumented run ──────────────
    if args.verify_against:
        ref = {}
        for line in Path(args.verify_against).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                ref[r["seed"]] = bool(r["leader_win"])
        mism = checked = 0
        for k in range(args.games):
            s = args.seed_base + k
            f = out_dir / f"seed_{s}.jsonl.gz"
            if s not in ref or not f.exists():
                continue
            with gzip.open(f, "rt") as fh:
                lines = fh.read().splitlines()
            foot = json.loads(lines[-1])
            head = json.loads(lines[0])
            if foot["kind"] != "footer":
                print(f"seed {s}: truncated trace")
                mism += 1
                continue
            checked += 1
            if (foot["leader"] == head["seat"]) != ref[s]:
                print(f"seed {s}: outcome mismatch vs reference run")
                mism += 1
        print(f"outcome verification: {checked} compared, {mism} mismatches")
        return 1 if mism else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
