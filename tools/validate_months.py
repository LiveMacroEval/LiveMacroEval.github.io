#!/usr/bin/env python3
"""Check the published LiveBetting curves against the RAW per-market bet files.

update_site.py derives docs/data/series.json from the pipeline's stitched
continuous-returns CSVs. This script goes one layer deeper and rebuilds every
number from the per-bet files that those CSVs were built from --
`results/bet_hourly_latest_<MonDD>/<market>/hourly_latest/betting_results_*` --
using only the segment SPEC from the pipeline (which months, which calendar
windows, which arm anchors the shared start) and arithmetic written out here:

    invested  = sum of bet_amount            over the kept bets
    value     = sum of shares_bought         over the kept WINNING bets
    return    = (value - invested) / invested

per arm, per tab window (a month, or a quarter on the GDP market), and for
the cumulative curve as the sum over windows. Every final value in
series.json and every row of the LiveBetting table in leaderboard.json must
match to the published 0.1pp, every arm with kept bets must be present, and
each window curve must span exactly the days its bets span.

Reads the private Results checkout (LIVEMACRO_RESULTS); publishes nothing.
Conda env: livemacro (the pipeline's segment module imports matplotlib).
Usage: python tools/validate_months.py [--betting-run bet_hourly_latest_Aug31]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
SITE = REPO / "docs/data"
RESULTS = Path(os.environ.get("LIVEMACRO_RESULTS", "/home/ruiyi/livemacro/Results"))

sys.path.insert(0, str(TOOLS))
from update_site import (  # noqa: E402
    BETTING_ANCHOR, BETTING_CUTOFF, BETTING_DROPPED, BETTING_LABELS, BETTING_MARKETS,
    _window_of,
)
sys.path.insert(0, str(RESULTS / "polymarket_return"))
from plot_continuous_0831 import make_segments  # noqa: E402  (segment SPEC only)

fails = 0


def check(ok: bool, msg: str) -> None:
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails += 1


def discover(detail: Path, token: str) -> dict[str, Path]:
    out = {}
    for path in detail.glob(f"betting_results_*_{token}_hourly*.csv"):
        parts = path.stem.split("_")
        if token not in parts:
            continue
        i = parts.index(token)
        if i <= 2:
            continue
        out["_".join(parts[2:i])] = path
    return out


def final(curve: dict) -> float:
    return next(v for v in reversed(curve["values"]) if v is not None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--betting-run", default="bet_hourly_latest_Aug31",
                    help="raw run under polymarket_return/results/ (default: %(default)s)")
    args = ap.parse_args()
    raw_root = RESULTS / "polymarket_return/results" / args.betting_run
    if not raw_root.exists():
        sys.exit(f"raw betting run not found: {raw_root}")

    series = json.loads((SITE / "series.json").read_text())
    board = json.loads((SITE / "leaderboard.json").read_text())
    site_markets = {m["key"]: m for m in series["betting"]["markets"]}
    table = {m["label"]: {r["name"]: r["ret"] for r in m["rows"]}
             for m in board["betting"]["markets"]}
    # BETTING_ANCHOR is the CSV filename suffix ("mar-anchor-<model>"); the
    # segment spec wants the model name
    anchor_model = BETTING_ANCHOR.split("mar-anchor-", 1)[-1]
    specs = make_segments(anchor_model)
    day = pd.Timedelta(days=1)
    n_curves = 0

    for key, label in BETTING_MARKETS:
        detail = raw_root / key / "hourly_latest"
        if not detail.exists():
            check(key not in site_markets, f"{label}: no raw files and not published")
            continue
        # ---- rebuild the kept bets per segment, exactly as the pipeline defines them
        seg_bets: dict[str, dict[str, pd.DataFrame]] = {}
        seg_first: dict[str, pd.Timestamp] = {}
        for seg in specs[key]:
            bm = {}
            for model, path in discover(detail, seg.month_token).items():
                if model in BETTING_DROPPED:
                    continue
                df = pd.read_csv(path, parse_dates=["datetime_utc"])
                df = df[["datetime_utc", "bet_amount", "shares_bought", "is_winning_bet"]]
                df = df.sort_values("datetime_utc").reset_index(drop=True)
                if seg.calendar_window is not None:
                    lo, hi = (pd.Timestamp(t) for t in seg.calendar_window)
                    df = df[(df["datetime_utc"] >= lo) & (df["datetime_utc"] < hi)]
                if not df.empty:
                    bm[model] = df
            if seg.apply_shared_start and bm:
                if seg.shared_start_anchor is not None:
                    start = bm[seg.shared_start_anchor]["datetime_utc"].iloc[0]
                else:
                    start = max(d["datetime_utc"].iloc[0] for d in bm.values())
                bm = {m: d[d["datetime_utc"] >= start] for m, d in bm.items()}
                bm = {m: d for m, d in bm.items() if not d.empty}
            if bm:
                seg_bets[seg.label] = bm
                seg_first[seg.label] = min(d["datetime_utc"].iloc[0] for d in bm.values())

        # ---- windows: a month, or the quarter on a quarterly market
        win_of = {seg: _window_of(key, seg, seg_first[seg].to_pydatetime()) for seg in seg_bets}
        win_order = list(dict.fromkeys(w for w, _l in win_of.values()))
        win_t0 = {}
        for seg, (w, _l) in win_of.items():
            win_t0[w] = min(win_t0.get(w, seg_first[seg]), seg_first[seg])

        # ---- per arm, per window: invested, value, last bet
        per: dict[str, dict[str, dict]] = {}
        for seg, bm in seg_bets.items():
            w = win_of[seg][0]
            for model, df in bm.items():
                cell = per.setdefault(model, {}).setdefault(w, {"inv": 0.0, "val": 0.0,
                                                                  "first": None, "last": None})
                cell["inv"] += float(df["bet_amount"].sum())
                cell["val"] += float(df.loc[df["is_winning_bet"].astype(bool), "shares_bought"].sum())
                cell["first"] = df["datetime_utc"].iloc[0] if cell["first"] is None \
                    else min(cell["first"], df["datetime_utc"].iloc[0])
                cell["last"] = df["datetime_utc"].iloc[-1] if cell["last"] is None \
                    else max(cell["last"], df["datetime_utc"].iloc[-1])

        # the cutoff rule, applied to the raw windows: an arm with a cutoff
        # loses the first window holding a bet at or after it and all later ones
        for model, cutoff in BETTING_CUTOFF.items():
            wins = per.get(model)
            if not wins:
                continue
            order = sorted(wins, key=lambda w: win_t0[w])
            bad = next((i for i, w in enumerate(order)
                        if wins[w]["last"] >= pd.Timestamp(cutoff)), None)
            if bad is not None:
                dropped = order[bad:]
                for w in dropped:
                    del wins[w]
                print(f"  note {label}: {model} loses {dropped} (cutoff {cutoff:%Y-%m-%d})")
                if not wins:
                    del per[model]

        sm = site_markets.get(key)
        check(sm is not None, f"{label}: published")
        if sm is None:
            continue
        pub_months = {mo["key"]: mo for mo in sm.get("months", [])}
        check(list(pub_months) == win_order,
              f"{label}: windows {win_order} match the published tabs")
        cum = {c["name"]: final(c) for c in sm["series"]}

        for model, wins in per.items():
            name = BETTING_LABELS.get(model, model)
            tot_inv = sum(c["inv"] for c in wins.values())
            tot_val = sum(c["val"] for c in wins.values())
            r_cum = (tot_val - tot_inv) / tot_inv * 100.0
            n_curves += 1
            check(name in cum and abs(cum[name] - r_cum) <= 0.05 + 1e-9,
                  f"{label:17s} {name:26s} cumulative {r_cum:+9.1f}%  site {cum.get(name)}")
            check(abs(table[label].get(name, float('nan')) - r_cum) <= 0.05 + 1e-9,
                  f"{label:17s} {name:26s} leaderboard table {table[label].get(name)}")
            for w, c in wins.items():
                r_w = (c["val"] - c["inv"]) / c["inv"] * 100.0
                curve = next((s for s in pub_months[w]["series"] if s["name"] == name), None)
                n_curves += 1
                ok = curve is not None and abs(final(curve) - r_w) <= 0.05 + 1e-9
                check(ok, f"{label:17s} {name:26s} {w}: {r_w:+9.1f}%  site "
                          f"{final(curve) if curve else None}")
                if curve is not None:
                    d0 = int((c["first"] - win_t0[w]) // day)
                    d1 = int((c["last"] - win_t0[w]) // day)
                    check(curve["start"] == d0 and len(curve["values"]) == d1 - d0 + 1,
                          f"{label:17s} {name:26s} {w}: spans days {d0}..{d1}")
        # nothing published that the raw files do not support
        raw_names = {BETTING_LABELS.get(m, m) for m in per}
        check(set(cum) == raw_names, f"{label}: published arms == arms with kept bets")
        for w, mo in pub_months.items():
            pub = {s["name"] for s in mo["series"]}
            raw = {BETTING_LABELS.get(m, m) for m, wins in per.items() if w in wins}
            check(pub == raw, f"{label} {w}: published arms == arms with kept bets")

    print(f"\n{n_curves} curves recomputed from raw bets; "
          + ("PASS" if not fails else f"{fails} FAILURE(S)"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
