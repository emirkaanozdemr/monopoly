"""Phase 3 coverage analysis over trace_run.py output. Outcome-independent.

Restricted to the traced agent's own seat (its decisions are the object of
study; the field agents are frozen opponents). Reports:

  1. Per decision family: distribution of chosen actions by action-space
     section, plus family volume.
  2. Legal actions never chosen across the entire run (by section, with
     legal-exposure counts, and the top never-chosen concrete action ids).
  3. Per family: no-op / default-branch rate; families above 90% flagged.
     Defaults: pre_roll_manage/oot_offer/post_roll_manage/buy_decision ->
     END_TURN, auction -> PASS, trade_reply -> DECLINE, roll -> ROLL_DICE.
  4. trade_reply detail: acceptance rate and the distribution of offered
     net value (list-price basis) at accept vs at reject. Descriptive only.
  5. Cyclic-trade involvement: how many accepted trades put a deed back with
     a previous owner within 10 rounds, split by whether the agent was a
     party. (Feeds the Phase 2 h-check's descriptive section.)

Usage:
  python3 competition_agent/trace_coverage.py \
      --trace-dir competition_agent/traces/final_strong \
      --out competition_agent/traces/coverage.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monopoly_game_engine.actions import (  # noqa: E402
    OFFSETS, ActionType, AuctionAction, action_to_description)
from monopoly_game_engine.constants import (  # noqa: E402
    PROPERTIES, PROPERTY_IDS)

END_TURN = int(ActionType.END_TURN)
ROLL = int(ActionType.ROLL_DICE)
ACCEPT = int(ActionType.ACCEPT_TRADE)
DECLINE = int(ActionType.DECLINE_TRADE)
AUC_PASS = int(AuctionAction.PASS)
PRICE = {sq: PROPERTIES[sq]["price"] for sq in PROPERTY_IDS}

DEFAULTS = {
    "pre_roll_manage": {END_TURN},
    "oot_offer": {END_TURN},
    "post_roll_manage": {END_TURN},
    "buy_decision": {END_TURN},
    "auction": {AUC_PASS},
    "trade_reply": {DECLINE},
    "roll": {ROLL},
}

_SECTIONS = sorted(OFFSETS.items(), key=lambda kv: kv[1])


def section_of(a: int) -> str:
    prev = _SECTIONS[0][0]
    for name, start in _SECTIONS:
        if a < start:
            return prev
        prev = name
    return prev


def sub_label(a: int) -> str:
    """Coarser-than-id, finer-than-section label for distributions."""
    sec = section_of(a)
    if sec == "binary":
        return ActionType(a).name
    if sec == "auction":
        return "auction_pass" if a == AUC_PASS else "auction_bid"
    return sec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    fam_chosen = defaultdict(Counter)      # family -> sub_label -> n
    fam_total = Counter()
    fam_default = Counter()
    legal_count = Counter()                # action id -> times legal
    chosen_count = Counter()               # action id -> times chosen
    reply_rows = []                        # (accepted, net_value_to_agent)
    cyc = Counter()
    games = 0

    files = sorted(Path(args.trace_dir).glob("seed_*.jsonl.gz"))
    for i, f in enumerate(files, 1):
        with gzip.open(f, "rt") as fh:
            header = json.loads(fh.readline())
            seat = header["seat"]
            trade_moves = defaultdict(list)
            for line in fh:
                r = json.loads(line)
                if r.get("kind") == "footer":
                    break
                # cyclic-trade bookkeeping needs all players
                if (r["p"] is not None and r["a"] == ACCEPT and r["inc"]):
                    frm, to, off_sq, req_sq, c_off, c_req = r["inc"]
                    for sq, a_, b_ in ((off_sq, frm, to), (req_sq, to, frm)):
                        if sq == -1:
                            continue
                        hist = trade_moves[sq]
                        if hist:
                            rd0, a0, b0 = hist[-1]
                            if b_ == a0 and a_ == b0 and r["rd"] - rd0 <= 10:
                                key = ("agent_party"
                                       if seat in (a_, b_) else "field_only")
                                cyc[key] += 1
                        hist.append((r["rd"], a_, b_))
                if r["p"] != seat:
                    continue
                fam = r["fam"]
                a = r["a"]
                fam_total[fam] += 1
                fam_chosen[fam][sub_label(a)] += 1
                if a in DEFAULTS.get(fam, ()):  # noqa: SIM118
                    fam_default[fam] += 1
                for la in r["legal"]:
                    legal_count[la] += 1
                chosen_count[a] += 1
                if fam == "trade_reply" and r["inc"] is not None:
                    frm, to, off_sq, req_sq, c_off, c_req = r["inc"]
                    # net value flowing TO the agent if accepted
                    gain = (PRICE.get(off_sq, 0) if off_sq != -1 else 0) + c_off
                    give = (PRICE.get(req_sq, 0) if req_sq != -1 else 0) + c_req
                    reply_rows.append((int(a == ACCEPT), gain - give))
        games += 1
        if i % 200 == 0:
            print(f"  {i}/{len(files)} games", flush=True)

    never_chosen = {a: n for a, n in legal_count.items()
                    if chosen_count[a] == 0}
    nc_by_section = Counter()
    exposure_by_section = Counter()
    for a, n in never_chosen.items():
        nc_by_section[section_of(a)] += 1
        exposure_by_section[section_of(a)] += n
    legal_ids_by_section = Counter(section_of(a) for a in legal_count)

    top_never = sorted(never_chosen.items(), key=lambda kv: -kv[1])[:40]

    acc = [v for ok, v in reply_rows if ok]
    rej = [v for ok, v in reply_rows if not ok]

    def dist(vals):
        if not vals:
            return None
        s = sorted(vals)
        q = lambda p: s[min(len(s) - 1, int(p * len(s)))]  # noqa: E731
        return {"n": len(s), "min": s[0], "p10": q(.10), "p25": q(.25),
                "median": q(.50), "p75": q(.75), "p90": q(.90), "max": s[-1],
                "mean": sum(s) / len(s)}

    out = {
        "games": games,
        "family_totals": dict(fam_total),
        "family_choice_distributions": {k: dict(v)
                                        for k, v in fam_chosen.items()},
        "family_default_rates": {
            k: {"decisions": fam_total[k], "default": fam_default[k],
                "rate": fam_default[k] / fam_total[k]}
            for k in fam_total},
        "flagged_over_90pct_default": [
            k for k in fam_total
            if fam_total[k] >= 100
            and fam_default[k] / fam_total[k] > 0.90],
        "never_chosen": {
            "distinct_action_ids_ever_legal": len(legal_count),
            "distinct_action_ids_chosen": len(chosen_count),
            "never_chosen_ids": len(never_chosen),
            "by_section": {
                s: {"ever_legal_ids": legal_ids_by_section[s],
                    "never_chosen_ids": nc_by_section.get(s, 0),
                    "legal_exposures_of_never_chosen":
                        exposure_by_section.get(s, 0)}
                for s in legal_ids_by_section},
            "top_never_chosen": [
                {"action": a, "desc": action_to_description(a),
                 "times_legal": n} for a, n in top_never],
        },
        "trade_reply": {
            "replies": len(reply_rows),
            "accepted": sum(ok for ok, _ in reply_rows),
            "acceptance_rate": (sum(ok for ok, _ in reply_rows)
                                / len(reply_rows) if reply_rows else None),
            "net_value_to_agent_at_accept": dist(acc),
            "net_value_to_agent_at_reject": dist(rej),
        },
        "cyclic_trades_within_10_rounds": dict(cyc),
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in
                      ("games", "family_default_rates",
                       "flagged_over_90pct_default")}, indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
