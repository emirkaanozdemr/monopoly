"""Phase 2 invariant checker over trace_run.py output. Diagnosis only.

Runs over ALL traced games (wins and losses alike). Deterministic: any
violation count above zero is a defect (or a documented ruleset deviation —
classified separately, see notes on checks (d-sell) and (f)).

Checks
------
  a  chosen action inside the legal set
  b  cash never negative outside active debt resolution (stricter here:
     never negative at all, since the engine clamps every payment)
  c  deed ownership changes only via purchase / trade / auction / bankruptcy
     transfer / sell-to-bank (the ruleset's extra liquidity channel)
  d  house supply <= 32, hotel supply <= 12, bank counts consistent with the
     board; even-BUILD rule on build actions (even-sell is documented as
     unenforced in PPO_PLUS_RULES.md and reported separately)
  e  per-player cash conservation: every cash delta between consecutive
     decisions is exactly explained by the action + step info
  f  no build on a color group containing a mortgaged deed (documented as a
     deliberate per-deed relaxation; counted separately)
  g  unmortgage charged exactly int(1.1 * mortgage_value), once
  h  trades: both sides solvent at proposal; no null trade; cyclic re-trade
     of the same deed within CYCLE_ROUNDS rounds (descriptive)
  i  auction triggered whenever a rolled-upon unowned deed is not bought

Each violation carries a minimal repro: seed + step index + the 5 preceding
decision rows (verbatim). Violations without repro are not emitted.

Usage:
  python3 competition_agent/trace_check.py --trace-dir competition_agent/traces/final_strong \
      --out competition_agent/traces/check_results.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monopoly_game_engine.actions import (  # noqa: E402
    ACTION_SPACE_SIZE, OFFSETS, ActionType, AuctionAction,
    AUCTION_ACTION_TO_INCREMENT, action_to_description)
from monopoly_game_engine.constants import (  # noqa: E402
    BOARD, COLOR_GROUPS, GO_SALARY, HOUSE_SUPPLY, HOTEL_SUPPLY,
    INCOME_TAX_SQUARE, JAIL_BAIL, JAIL_SQUARE, LUXURY_TAX_SQUARE,
    MAX_JAIL_TURNS, PROPERTIES, PROPERTY_IDS, REAL_ESTATE_IDS,
    TRADE_CASH_LEVELS)

SQ_INDEX = {sq: i for i, sq in enumerate(PROPERTY_IDS)}
RE_INDEX = {sq: i for i, sq in enumerate(REAL_ESTATE_IDS)}
MORTGAGE_V = {sq: PROPERTIES[sq]["mortgage"] if "mortgage" in PROPERTIES[sq]
              else PROPERTIES[sq]["price"] // 2 for sq in PROPERTY_IDS}
PRICE = {sq: PROPERTIES[sq]["price"] for sq in PROPERTY_IDS}
HOUSE_PRICE = {sq: PROPERTIES[sq].get("house_price") for sq in PROPERTY_IDS}
GROUP_OF = {}
for color, sqs in COLOR_GROUPS.items():
    for sq in sqs:
        GROUP_OF[sq] = color

END_TURN = int(ActionType.END_TURN)
ROLL = int(ActionType.ROLL_DICE)
BUY = int(ActionType.BUY_PROPERTY)
GOOJ = int(ActionType.USE_GOOJ_CARD)
BAIL = int(ActionType.PAY_BAIL)
BANKRUPT = int(ActionType.DECLARE_BANKRUPT)
ACCEPT = int(ActionType.ACCEPT_TRADE)
DECLINE = int(ActionType.DECLINE_TRADE)
AUC_PASS = int(AuctionAction.PASS)

CYCLE_ROUNDS = 10          # window for the cyclic re-trade check (h)
REPRO_ROWS = 5


def section_of(a: int) -> str:
    prev = "binary"
    for name, start in sorted(OFFSETS.items(), key=lambda kv: kv[1]):
        if a < start:
            return prev
        prev = name
    return prev


class GameChecker:
    def __init__(self, seed: int, header: dict):
        self.seed = seed
        self.header = header
        self.prev = None            # previous row
        self.recent = deque(maxlen=REPRO_ROWS + 1)
        self.violations = []
        # (h) cyclic trades: deed -> list of (round, from, to)
        self.trade_moves = {}
        self.counts = {}

    # ── reporting ─────────────────────────────────────────────────────────
    def flag(self, check: str, row: dict, msg: str, cls: str = "defect"):
        repro = [dict(r, legal_n=len(r["legal"])) for r in list(self.recent)[:-1]]
        for r in repro:                       # keep repros readable
            r.pop("legal", None)
        self.violations.append({
            "check": check, "class": cls, "seed": self.seed,
            "t": row["t"], "round": row["rd"], "actor": row["p"],
            "action": row.get("a"),
            "action_desc": (action_to_description(row["a"])
                            if row.get("a") is not None else None),
            "msg": msg,
            "repro": {"seed": self.seed, "turn": row["t"],
                      "preceding_rows": repro},
        })
        self.counts[check] = self.counts.get(check, 0) + 1

    # ── per-row checks on the row itself ──────────────────────────────────
    def check_row(self, row: dict):
        self.recent.append(row)
        a = row["a"]

        # (a) legality
        if a not in set(row["legal"]):
            self.flag("a_illegal_action", row,
                      f"chosen {a} ({action_to_description(a)}) not in legal set")

        # (b) negative cash
        for pid, c in enumerate(row["cash"]):
            if c < 0:
                in_debt = row["debt"][0] == pid
                self.flag("b_negative_cash", row,
                          f"player {pid} cash {c} (debt_active={in_debt})")

        # (d) supply consistency + caps
        houses_on_board = sum(h for h in row["h"] if h < 5)
        hotels_on_board = sum(1 for h in row["h"] if h == 5)
        if row["ha"] < 0 or row["ha"] > HOUSE_SUPPLY:
            self.flag("d_supply", row, f"houses_available={row['ha']}")
        if row["ho"] < 0 or row["ho"] > HOTEL_SUPPLY:
            self.flag("d_supply", row, f"hotels_available={row['ho']}")
        if houses_on_board + row["ha"] != HOUSE_SUPPLY:
            self.flag("d_supply", row,
                      f"board houses {houses_on_board} + bank {row['ha']} "
                      f"!= {HOUSE_SUPPLY}")
        if hotels_on_board + row["ho"] != HOTEL_SUPPLY:
            self.flag("d_supply", row,
                      f"board hotels {hotels_on_board} + bank {row['ho']} "
                      f"!= {HOTEL_SUPPLY}")

        # (h) proposal-time solvency + null trades, on the action itself
        if OFFSETS["buy_trade"] <= a < OFFSETS["auction"]:
            self._check_proposal(row, a)

        if self.prev is not None:
            self.check_transition(self.prev, row)
        self.prev = row

    def _check_proposal(self, row: dict, a: int):
        pid = row["p"]
        n_props, n_cash = len(PROPERTY_IDS), len(TRADE_CASH_LEVELS)
        others = [i for i in range(4) if i != pid]
        if a < OFFSETS["sell_trade"]:                      # buy offer
            local = a - OFFSETS["buy_trade"]
            tgt = others[local // (n_props * n_cash)]
            rem = local % (n_props * n_cash)
            sq = PROPERTY_IDS[rem // n_cash]
            cash = int(PRICE[sq] * TRADE_CASH_LEVELS[rem % n_cash])
            if cash > row["cash"][pid]:
                self.flag("h_insolvent_proposal", row,
                          f"buy offer {cash} > proposer cash {row['cash'][pid]}")
        elif a < OFFSETS["exch_trade"]:                    # sell offer
            local = a - OFFSETS["sell_trade"]
            tgt = others[local // (n_props * n_cash)]
            rem = local % (n_props * n_cash)
            sq = PROPERTY_IDS[rem // n_cash]
            cash = int(PRICE[sq] * TRADE_CASH_LEVELS[rem % n_cash])
            if cash > row["cash"][tgt]:
                self.flag("h_insolvent_proposal", row,
                          f"sell offer requests {cash} > target cash "
                          f"{row['cash'][tgt]}")
        else:                                              # exchange
            local = a - OFFSETS["exch_trade"]
            rem = local % (n_props * (n_props - 1))
            offer_idx = rem // (n_props - 1)
            req_raw = rem % (n_props - 1)
            req_idx = req_raw if req_raw < offer_idx else req_raw + 1
            if offer_idx == req_idx:
                self.flag("h_null_trade", row,
                          "exchange offers a deed for itself")

    # ── transition checks between consecutive rows ────────────────────────
    def check_transition(self, r0: dict, r1: dict):
        a = r0["a"]
        sec = section_of(a)
        actor = r0["p"]

        self._check_ownership(r0, r1, a, sec, actor)
        self._check_cash(r0, r1, a, sec, actor)
        self._check_houses(r0, r1, a, sec, actor)
        self._check_auction_trigger(r0, r1, a, actor)

    # (c) ownership transitions
    def _check_ownership(self, r0, r1, a, sec, actor):
        changes = [(i, o0, o1) for i, (o0, o1)
                   in enumerate(zip(r0["own"], r1["own"])) if o0 != o1]
        if not changes:
            return
        ok = False
        if sec == "binary" and a == BUY:
            pos = r0["pos"][actor]
            ok = (len(changes) == 1 and changes[0][0] == SQ_INDEX.get(pos)
                  and changes[0][1] == -1 and changes[0][2] == actor)
        elif sec == "binary" and a == ACCEPT and r0["inc"]:
            frm, to, off_sq, req_sq, *_ = r0["inc"]
            expected = {}
            if off_sq != -1:
                expected[SQ_INDEX[off_sq]] = (frm, to)
            if req_sq != -1:
                expected[SQ_INDEX[req_sq]] = (to, frm)
            ok = all(i in expected and (o0, o1) == expected[i]
                     for i, o0, o1 in changes) and len(changes) == len(expected)
        elif sec == "binary" and a == BANKRUPT:
            # all debtor deeds -> creditor (or bank)
            cred = r0["debt"][1] if r0["debt"][0] == actor else -1
            ok = all(o0 == actor and o1 in (cred, -1) for _, o0, o1 in changes)
        elif sec == "sell_prop":
            sq = PROPERTY_IDS[a - OFFSETS["sell_prop"]]
            ok = (len(changes) == 1 and changes[0][0] == SQ_INDEX[sq]
                  and changes[0][1] == actor and changes[0][2] == -1)
        elif sec == "auction" and "auction_winner" in r0["info"]:
            w = r0["info"]["auction_winner"]
            ok = (len(changes) == 1 and changes[0][1] == -1
                  and changes[0][2] == w)
        if not ok:
            desc = ", ".join(f"{PROPERTY_IDS[i]}:{o0}->{o1}"
                             for i, o0, o1 in changes)
            self.flag("c_ownership", r0,
                      f"unattributable ownership change [{desc}] on "
                      f"{action_to_description(a)}")

        # (h) record trade-driven deed movement for the cycle check
        if sec == "binary" and a == ACCEPT and r0["inc"]:
            for i, o0, o1 in changes:
                sq = PROPERTY_IDS[i]
                self.trade_moves.setdefault(sq, []).append(
                    (r0["rd"], o0, o1, r0["t"]))

    # (e)+(g) exact per-player cash conservation
    def _check_cash(self, r0, r1, a, sec, actor):
        exp = [0, 0, 0, 0]
        info = r0["info"]
        unexplained_ok = False

        if sec == "binary":
            if a == ROLL and "dice" in info:
                self._expected_roll_delta(r0, r1, exp, info)
            elif a == BAIL:
                exp[actor] = -JAIL_BAIL
            elif a == BUY:
                sq = r0["pos"][actor]
                exp[actor] = -PRICE.get(sq, 0)
            elif a == BANKRUPT:
                # liquidation proceeds + full transfer; debtor ends at 0
                proceeds = 0
                for i, sq in enumerate(REAL_ESTATE_IDS):
                    if r0["own"][SQ_INDEX[sq]] == actor and r0["h"][i] > 0:
                        n = 5 if r0["h"][i] == 5 else r0["h"][i]
                        proceeds += n * (HOUSE_PRICE[sq] // 2)
                total = r0["cash"][actor] + proceeds
                cred = r0["debt"][1] if r0["debt"][0] == actor else -1
                exp[actor] = -r0["cash"][actor]
                if cred != -1:
                    exp[cred] = total
            elif a == ACCEPT and r0["inc"]:
                frm, to, _, _, c_off, c_req = r0["inc"]
                exp[frm] += c_req - c_off
                exp[to] += c_off - c_req
        elif sec == "mortgage":
            sq = PROPERTY_IDS[a - OFFSETS["mortgage"]]
            if r0["own"][SQ_INDEX[sq]] == actor and not r0["mort"][SQ_INDEX[sq]]:
                exp[actor] = MORTGAGE_V[sq]
                exp = self._after_debt_settle(r0, actor, exp)
        elif sec == "unmortgage":
            sq = PROPERTY_IDS[a - OFFSETS["unmortgage"]]
            if r0["own"][SQ_INDEX[sq]] == actor and r0["mort"][SQ_INDEX[sq]]:
                cost = int(MORTGAGE_V[sq] * 1.1)
                exp[actor] = -cost
                # (g) exactness — any other charge is a double/missing interest
                actual = r1["cash"][actor] - r0["cash"][actor]
                if actual != -cost and r0["debt"][0] != actor:
                    self.flag("g_mortgage_interest", r0,
                              f"unmortgage {sq}: charged {-actual}, "
                              f"expected {cost} (=1.1x{MORTGAGE_V[sq]})")
        elif sec in ("improve_house", "improve_hotel"):
            sq = REAL_ESTATE_IDS[a - OFFSETS[sec]]
            if r1["h"][RE_INDEX[sq]] != r0["h"][RE_INDEX[sq]]:
                exp[actor] = -HOUSE_PRICE[sq]
        elif sec in ("sell_house", "sell_hotel"):
            sq = REAL_ESTATE_IDS[a - OFFSETS[sec]]
            if r1["h"][RE_INDEX[sq]] != r0["h"][RE_INDEX[sq]]:
                exp[actor] = HOUSE_PRICE[sq] // 2
                exp = self._after_debt_settle(r0, actor, exp)
        elif sec == "sell_prop":
            sq = PROPERTY_IDS[a - OFFSETS["sell_prop"]]
            if r0["own"][SQ_INDEX[sq]] == actor:
                exp[actor] = MORTGAGE_V[sq]
                exp = self._after_debt_settle(r0, actor, exp)
        elif sec == "auction":
            if "auction_winner" in info:
                exp[info["auction_winner"]] = -info["auction_price"]

        actual = [c1 - c0 for c0, c1 in zip(r0["cash"], r1["cash"])]
        if actual != exp:
            self.flag("e_conservation", r0,
                      f"cash delta {actual} != expected {exp} on "
                      f"{action_to_description(a)} info={info}")

    def _after_debt_settle(self, r0, actor, exp):
        """Liquidation while in debt routes cash straight to the creditor."""
        if r0["debt"][0] != actor or r0["debt"][2] <= 0:
            return exp
        raised = exp[actor]
        available = r0["cash"][actor] + raised
        payment = min(available, r0["debt"][2])
        out = list(exp)
        out[actor] = raised - payment
        cred = r0["debt"][1]
        if cred != -1:
            out[cred] = out[cred] + payment
        return out

    def _expected_roll_delta(self, r0, r1, exp, info):
        actor = r0["p"]
        d1, d2 = info["dice"]
        if info.get("three_doubles"):
            return                                    # sent to jail, no move
        pos0 = r0["pos"][actor]
        was_jailed = bool(r0["jail"][actor])
        if was_jailed:
            if d1 == d2:
                pass                                  # released free
            elif r0["jt"][actor] + 1 >= MAX_JAIL_TURNS:
                exp[actor] -= min(JAIL_BAIL, r0["cash"][actor])
            else:
                return                                # stayed in jail
        new_pos = (pos0 + d1 + d2) % 40
        if new_pos < pos0 and not (was_jailed and d1 != d2
                                   and r0["jt"][actor] + 1 < MAX_JAIL_TURNS):
            # passed Go (in_jail was cleared before the move in every case
            # where the player moves)
            exp[actor] += GO_SALARY
        landed = info.get("landed_on")
        if landed == INCOME_TAX_SQUARE:
            exp[actor] -= min(200, r0["cash"][actor] + exp[actor])
        elif landed == LUXURY_TAX_SQUARE:
            exp[actor] -= min(100, r0["cash"][actor] + exp[actor])
        rent = info.get("rent_paid")
        if rent:
            owner = r1["own"][SQ_INDEX[landed]] if landed in SQ_INDEX else None
            # owner unchanged by rent; read from r0 to be safe
            owner = r0["own"][SQ_INDEX[landed]]
            exp[actor] -= rent
            if owner not in (-1, None):
                exp[owner] += rent

    # (d) even-build on build/sell actions + (f) mortgaged-group build
    def _check_houses(self, r0, r1, a, sec, actor):
        if sec not in ("improve_house", "improve_hotel",
                       "sell_house", "sell_hotel"):
            return
        sq = REAL_ESTATE_IDS[a - OFFSETS[sec]]
        if r1["h"][RE_INDEX[sq]] == r0["h"][RE_INDEX[sq]]:
            return                       # engine rejected silently; no change
        group = COLOR_GROUPS[GROUP_OF[sq]]
        levels_after = [r1["h"][RE_INDEX[s]] for s in group]
        if sec.startswith("improve"):
            # even-build: built deed must have been a least-developed one
            before = [r0["h"][RE_INDEX[s]] for s in group]
            if r0["h"][RE_INDEX[sq]] != min(before):
                self.flag("d_even_build", r0,
                          f"built on {sq} at level {r0['h'][RE_INDEX[sq]]} "
                          f"while group at {before}")
            # (f) mortgaged sibling
            if any(r0["mort"][SQ_INDEX[s]] for s in group):
                self.flag("f_build_on_mortgaged_group", r0,
                          f"built on {sq}, group mortgage flags "
                          f"{[r0['mort'][SQ_INDEX[s]] for s in group]}",
                          cls="ruleset-documented")
        else:
            # even-sell is documented as unenforced; record, do not defect
            if max(levels_after) - min(levels_after) > 1:
                self.flag("d_uneven_sell", r0,
                          f"sell left group {GROUP_OF[sq]} at {levels_after}",
                          cls="ruleset-documented")

    # (i) declined deed must be auctioned
    def _check_auction_trigger(self, r0, r1, a, actor):
        if (r0["ph"] == "post_roll" and r0["hr"] and a == END_TURN
                and r0["debt"][0] == -1):
            pos = r0["pos"][actor]
            if pos in SQ_INDEX and r0["own"][SQ_INDEX[pos]] == -1:
                if r1["ph"] != "auction":
                    self.flag("i_no_auction", r0,
                              f"unowned deed {pos} passed without auction "
                              f"(next phase {r1['ph']})")

    # ── end of game ───────────────────────────────────────────────────────
    def finish(self, footer: dict):
        # (h) cyclic re-trade: same deed returning to a previous owner
        # within CYCLE_ROUNDS rounds, via trades only
        for sq, moves in self.trade_moves.items():
            for i in range(1, len(moves)):
                rd1, frm1, to1, t1 = moves[i]
                rd0, frm0, to0, t0 = moves[i - 1]
                if to1 == frm0 and frm1 == to0 and rd1 - rd0 <= CYCLE_ROUNDS:
                    # need repro rows — use whatever is in recent as context
                    self.violations.append({
                        "check": "h_cyclic_trade", "class": "descriptive",
                        "seed": self.seed, "t": t1, "round": rd1,
                        "actor": None, "action": None, "action_desc": None,
                        "msg": f"deed {sq} traded {frm0}->{to0} (round {rd0})"
                               f" then back {frm1}->{to1} (round {rd1})",
                        "repro": {"seed": self.seed, "turn": t1,
                                  "preceding_rows": []},
                    })
                    self.counts["h_cyclic_trade"] = (
                        self.counts.get("h_cyclic_trade", 0) + 1)


def check_file(path: Path):
    with gzip.open(path, "rt") as fh:
        header = json.loads(fh.readline())
        gc = GameChecker(header["seed"], header)
        footer = None
        for line in fh:
            obj = json.loads(line)
            if obj.get("kind") == "footer":
                footer = obj
                break
            gc.check_row(obj)
        gc.finish(footer or {})
    return gc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-repros-per-check", type=int, default=10)
    args = ap.parse_args()

    files = sorted(Path(args.trace_dir).glob("seed_*.jsonl.gz"))
    totals, all_viol = {}, []
    games = 0
    for i, f in enumerate(files, 1):
        gc = check_file(f)
        games += 1
        for k, v in gc.counts.items():
            totals[k] = totals.get(k, 0) + v
        all_viol.extend(gc.violations)
        if i % 200 == 0:
            print(f"  {i}/{len(files)} games checked", flush=True)

    # cap stored repros per check to keep the artifact reviewable
    kept, seen = [], {}
    for v in all_viol:
        k = v["check"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= args.max_repros_per_check:
            kept.append(v)

    out = {"games_checked": games, "violation_totals": totals,
           "violations": kept}
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"\n{games} games checked")
    for k in sorted(totals):
        print(f"  {k}: {totals[k]}")
    if not totals:
        print("  no violations")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
