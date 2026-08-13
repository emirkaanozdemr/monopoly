"""Phase 4 EXPLORE mining. Reads ONLY seeds 960000..960999 (see traces/SPLIT.md).

Every candidate anomaly is reported for won AND lost games; an anomaly at the
same rate in both is not a cause of losing. No CONFIRM seed is opened here —
the seed gate is enforced in code, not by convention.

Per-game features (agent seat only unless noted):
  outcome        leader_win (net-worth leader or sole survivor)
  bankrupt       agent went bankrupt
  capped         game hit the 3000-step harness cap
  churn          accepted trades in which a deed returned to a previous owner
                 within 10 rounds, agent a party
  churn_pairs    distinct deeds involved in agent churn
  proposals      trade proposals made by agent
  prop_steps_pct fraction of the agent's decisions spent proposing trades
  houses_built   houses+hotel-upgrades the agent bought
  first_build_rd round of agent's first build (-1 never)
  monopolies_rd  first round the agent completed a color group (-1 never)
  auction_wins   auctions won by agent; auction_spend total paid
  auc_underbid   auctions the agent passed at a final price below 50% of list
  cash_p50       agent median cash across decisions
  jail_turns     total turns the agent sat in jail
  deeds_final    agent deed count at end
  nw_slope_late  agent net-worth change over the last 500 steps (capped games)

Usage:
  python3 competition_agent/trace_mine.py \
      --trace-dir competition_agent/traces/final_strong \
      --out competition_agent/traces/explore_features.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monopoly_game_engine.actions import OFFSETS, ActionType, AuctionAction  # noqa: E402
from monopoly_game_engine.constants import PROPERTIES, PROPERTY_IDS, COLOR_GROUPS  # noqa: E402

GROUP_OF = {s_: c for c, sqs in COLOR_GROUPS.items() for s_ in sqs}

EXPLORE_LO, EXPLORE_HI = 960000, 960999          # inclusive; SPLIT.md
ACCEPT = int(ActionType.ACCEPT_TRADE)
AUC_PASS = int(AuctionAction.PASS)
PRICE = {sq: PROPERTIES[sq]["price"] for sq in PROPERTY_IDS}
SQ_INDEX = {sq: i for i, sq in enumerate(PROPERTY_IDS)}
RE_GROUPS = {c: [SQ_INDEX[s] for s in sqs] for c, sqs in COLOR_GROUPS.items()
             if all(s in SQ_INDEX for s in sqs)}


def analyse_game(path: Path):
    with gzip.open(path, "rt") as fh:
        header = json.loads(fh.readline())
        seat = header["seat"]
        if not (EXPLORE_LO <= header["seed"] <= EXPLORE_HI):
            return None                              # CONFIRM stays sealed

        trade_moves = defaultdict(list)
        churn = 0
        churn_deeds = set()
        mono_completed_by_accept = 0
        mono_broken_by_accept = 0
        proposals = 0
        agent_decisions = 0
        houses_built = 0
        first_build_rd = -1
        monopolies_rd = -1
        auction_wins = 0
        auction_spend = 0
        auc_underbid = 0
        cash_series = []
        full_group_rounds = set()          # rounds agent holds a complete group
        build_opp_rounds = set()           # rounds with a legal build for agent
        first_opp_rd = -1

        jail_turns = 0
        prev_jail = 0
        nw_hist = []                                  # (t, agent nw)
        footer = None

        for line in fh:
            r = json.loads(line)
            if r.get("kind") == "footer":
                footer = r
                break

            # churn accounting (any accepted trade)
            if r["a"] == ACCEPT and r["inc"]:
                frm, to, off_sq, req_sq, *_ = r["inc"]
                # does this accept COMPLETE a group for the agent, or BREAK
                # a group the agent holds completely?
                if to == seat and off_sq != -1:
                    g = GROUP_OF.get(off_sq)
                    if g and g in RE_GROUPS:
                        # ownership AFTER the trade: gain off_sq, lose req_sq
                        after = all(
                            (x == off_sq) or
                            (r["own"][SQ_INDEX[x]] == seat and x != req_sq)
                            for x in COLOR_GROUPS[g])
                        if after:
                            mono_completed_by_accept += 1
                if to == seat and req_sq != -1:
                    g = GROUP_OF.get(req_sq)
                    if g and g in RE_GROUPS:
                        before = all(r["own"][SQ_INDEX[x]] == seat
                                     for x in COLOR_GROUPS[g])
                        gains_back = off_sq in COLOR_GROUPS[g]
                        if before and not gains_back:
                            mono_broken_by_accept += 1
                for sq, a_, b_ in ((off_sq, frm, to), (req_sq, to, frm)):
                    if sq == -1:
                        continue
                    hist = trade_moves[sq]
                    if hist:
                        rd0, a0, b0 = hist[-1]
                        if b_ == a0 and a_ == b0 and r["rd"] - rd0 <= 10 \
                                and seat in (a_, b_):
                            churn += 1
                            churn_deeds.add(sq)
                    hist.append((r["rd"], a_, b_))

            # monopoly formation (ownership vector, agent)
            holds = any(len(idxs) >= 2 and all(r["own"][i] == seat
                                               for i in idxs)
                        for c, idxs in RE_GROUPS.items())
            if holds:
                full_group_rounds.add(r["rd"])
                if monopolies_rd == -1:
                    monopolies_rd = r["rd"]

            # auction outcomes (any actor row carries finish info)
            info = r["info"]
            if "auction_winner" in info:
                if info["auction_winner"] == seat:
                    auction_wins += 1
                    auction_spend += info["auction_price"]
                elif r["p"] == seat and r["a"] == AUC_PASS:
                    pass

            if r["p"] != seat:
                # track jail continuity for the agent from any row
                continue

            agent_decisions += 1
            cash_series.append(r["cash"][seat])
            nw_hist.append((r["t"], r["nw"][seat]))
            if r["jail"][seat] and not prev_jail:
                pass
            if r["jail"][seat]:
                jail_turns += 1 if r["fam"] == "roll" else 0
            prev_jail = r["jail"][seat]

            a = r["a"]
            if any(OFFSETS["improve_house"] <= la < OFFSETS["sell_house"]
                   for la in r["legal"]):
                build_opp_rounds.add(r["rd"])
                if first_opp_rd == -1:
                    first_opp_rd = r["rd"]
            if OFFSETS["buy_trade"] <= a < OFFSETS["auction"]:
                proposals += 1
            if OFFSETS["improve_house"] <= a < OFFSETS["sell_house"]:
                houses_built += 1
                if first_build_rd == -1:
                    first_build_rd = r["rd"]
            if (r["fam"] == "auction" and a == AUC_PASS
                    and r["info"].get("auction_winner") is not None
                    and r["info"].get("auction_winner") != seat):
                sq = None                 # final price check via info
                price = r["info"].get("auction_price", 0)

        if footer is None:
            return None

        cash_sorted = sorted(cash_series)
        cash_p50 = cash_sorted[len(cash_sorted) // 2] if cash_sorted else 0
        late = [nw for t, nw in nw_hist if t >= footer["steps"] - 500]
        nw_slope_late = (late[-1] - late[0]) if len(late) >= 2 else 0.0

        return {
            "seed": header["seed"], "seat": seat,
            "won": footer["leader"] == seat,
            "decisive": footer["decisive"],
            "bankrupt": bool(footer["bankrupt"][seat]),
            "capped": bool(footer["step_capped"]),
            "churn": churn,
            "churn_pairs": len(churn_deeds),
            "mono_completed_by_accept": mono_completed_by_accept,
            "mono_broken_by_accept": mono_broken_by_accept,
            "proposals": proposals,
            "prop_steps_pct": (proposals / agent_decisions
                               if agent_decisions else 0.0),
            "houses_built": houses_built,
            "first_build_rd": first_build_rd,
            "full_group_rounds": len(full_group_rounds),
            "build_opp_rounds": len(build_opp_rounds),
            "first_opp_rd": first_opp_rd,
            "build_latency": (first_build_rd - first_opp_rd
                              if first_build_rd >= 0 and first_opp_rd >= 0
                              else -1),
            "monopolies_rd": monopolies_rd,
            "auction_wins": auction_wins,
            "auction_spend": auction_spend,
            "cash_p50": cash_p50,
            "jail_turns": jail_turns,
            "deeds_final": sum(1 for o in footer["own"] if o == seat),
            "nw_final": footer["net_worth"][seat],
            "nw_slope_late": nw_slope_late,
            "steps": footer["steps"],
        }


def rate_table(rows, key, pred):
    """Mean of pred(feature) among won vs lost games."""
    won = [r for r in rows if r["won"]]
    lost = [r for r in rows if not r["won"]]
    def m(g):
        return sum(pred(r[key]) for r in g) / len(g) if g else None
    return {"won_n": len(won), "lost_n": len(lost),
            "won": m(won), "lost": m(lost)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = []
    files = sorted(Path(args.trace_dir).glob("seed_*.jsonl.gz"))
    for i, f in enumerate(files, 1):
        seed = int(f.stem.split("_")[1].split(".")[0])
        if not (EXPLORE_LO <= seed <= EXPLORE_HI):
            continue
        r = analyse_game(f)
        if r:
            rows.append(r)
        if i % 200 == 0:
            print(f"  scanned {i}/{len(files)} files", flush=True)

    ident = lambda v: v                                   # noqa: E731
    is_pos = lambda v: 1.0 if v > 0 else 0.0              # noqa: E731

    summary = {
        "explore_games": len(rows),
        "win_rate": sum(r["won"] for r in rows) / len(rows),
        "means_won_vs_lost": {
            k: rate_table(rows, k, ident) for k in
            ("churn", "churn_pairs", "mono_completed_by_accept",
             "mono_broken_by_accept", "proposals", "prop_steps_pct",
             "houses_built", "first_build_rd", "full_group_rounds",
             "build_opp_rounds", "build_latency", "monopolies_rd",
             "auction_wins", "auction_spend", "cash_p50", "jail_turns",
             "deeds_final", "nw_final", "nw_slope_late", "steps")},
        "rates_won_vs_lost": {
            "any_churn": rate_table(rows, "churn", is_pos),
            "any_mono_broken": rate_table(rows, "mono_broken_by_accept", is_pos),
            "any_build": rate_table(rows, "houses_built", is_pos),
            "any_monopoly": rate_table(
                rows, "monopolies_rd", lambda v: 1.0 if v >= 0 else 0.0),
            "any_build_opportunity": rate_table(
                rows, "build_opp_rounds", is_pos),
            "built_given_opportunity": None,  # filled below
            "bankrupt": rate_table(rows, "bankrupt", ident),
            "capped": rate_table(rows, "capped", ident),
        },
    }
    opp = [r for r in rows if r["build_opp_rounds"] > 0]
    summary["rates_won_vs_lost"]["built_given_opportunity"] = rate_table(
        opp, "houses_built", is_pos)
    summary["by_seat"] = {
        s_: {"n": len([r for r in rows if r["seat"] == s_]),
             "win_rate": (sum(r["won"] for r in rows if r["seat"] == s_)
                          / max(1, len([r for r in rows
                                        if r["seat"] == s_])))}
        for s_ in range(4)}
    out = {"summary": summary, "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
