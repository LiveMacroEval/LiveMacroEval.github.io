#!/usr/bin/env python3
"""Refresh the LiveMacroEval website from the Results pipeline outputs.

Run after the monthly data refresh (see the private Results/UPDATE_PIPELINE.md).
Rewrites docs/data/leaderboard.json and re-copies the paper figures. The site
renders every number from that JSON, so this is the only step.

IMPORTANT — this repo is public, the scoring inputs are not.
------------------------------------------------------------
The scoring overlay this reads lives under
`Results/market_surprise_capture_score/**/bloomberg_overlay/`, which the repo's
.gitignore deliberately excludes: it is derived from the Bloomberg ECOS survey
and FirstRateData ES futures bars, neither of which may be redistributed
(see DATA_SOURCES.md). So:

  * the overlay is read from a SEPARATE PRIVATE CHECKOUT, never from this repo;
  * only aggregate scores (point estimate, CI, event count) are written into
    the site — the same numbers the paper reports in Figure 2;
  * no row-level, per-release, or raw vendor value is ever copied here.

Point --results-root at your private Results/ checkout (or set
LIVEMACRO_RESULTS). Run check_release_safety.py before every push.

Usage
-----
    . /home/ruiyi/anaconda3/bin/activate && conda activate livemacro
    python tools/update_site.py --dry-run
    python tools/update_site.py \
        --overlay investing_overlay_0825 --periods-dir investing_overlay_0825_by_quarter \
        --theme-plots market_surprise_capture_score/step_15_5_scoring_by_theme/plots_0825 \
        --betting-dir continuous_returns_20260831_with_qwen \
        --window "Target reference periods Nov 2025 - Jul 2026" \
        --last-updated 2026-08-25

The leaderboard's quarter tabs read `--periods-dir`, the by-period sibling of
the overlay written by score_by_period_<MMDD>.py in the private checkout. The
betting charts' month tabs are the `months` blocks in series.json, derived
here from the same continuous-returns CSVs as the cumulative curves.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent         # <repo>/tools
REPO = TOOLS.parent                             # <repo>  (public)
SITE = REPO / "docs"                            # the published site

# The private working tree that holds the pipeline outputs and paper figures.
DEFAULT_RESULTS = Path(os.environ.get("LIVEMACRO_RESULTS", "/home/ruiyi/livemacro/Results"))
DEFAULT_FIGURES = Path(os.environ.get("LIVEMACRO_FIGURES", "/home/ruiyi/livemacro/Paper/figures"))

SCORING_SUBPATH = "market_surprise_capture_score/step_15_4_live_scoring"

# code arm -> display name shown on the site
MODEL_LABELS = {
    "gpt-5-search-api": "GPT-5",
    "claude-sonnet-4.5-api": "Claude-4.5-Sonnet",
    "qwen3-235b-a22b-instruct-2507": "Qwen3-235B",
    "qwen3-next-80b-a3b-instruct": "Qwen3-80B",
    "arima_aic": "auto-ARIMA",
    "claude-code-agent": "Claude Code agent",
    "claude-code-multiagent": "Claude Code multi-agent",
    "gpt-5-search-api-reasoned": "GPT-5 (reasoned)",
}
MODEL_KIND = {"arima_aic": "econ"}          # everything else defaults to "llm"

# Figure 2 in the paper drops this arm (n=11 outlier); keep the site consistent.
DROPPED = {"claude-code-agent"}

# The arms that went live in June 2026 sit in the all-months table like every
# other arm (user decision 2026-09-03: the 0825 window covers their whole live
# span, so nothing is non-overlapping), in the month tabs for the months they
# have events, and simply absent from earlier months.

# Only these columns are ever read out of the overlay. Aggregates only.
COLS = ("model", "n_events", "BDRC_point", "BDRC_ci90_lo", "BDRC_ci90_hi")

FIGURES = [
    # Rendered on the site.
    "pipeline_overview.png",
    "case_study_cpi_yoy_2026_03.png",
    "case_study_pce_mom_2026_03.png",
    # Superseded by the on-page tables (themes, LiveBetting, headline bar
    # chart). Kept on the allowlist so the files may remain in docs/ until
    # someone decides to prune them; nothing links to them.
    "bdrc_score_no_agent_ci90.png",
    "theme_production.png",
    "theme_inflation_consumption_services.png",
    "theme_labor_market.png",
    "theme_housing.png",
    "continuous_returns_real_gdp_qoq_mar-anchor-claude-sonnet-4.5.png",
    "continuous_returns_cpi_yoy_mar-anchor-claude-sonnet-4.5.png",
    "continuous_returns_unemployment_rate_mar-anchor-claude-sonnet-4.5.png",
]

# ---------------------------------------------------------------- themes ----
# LiveMacro Score restricted to each thematic block. Same BDRC metric as the
# headline table, so the two are directly comparable.
THEME_SUBPATH = "market_surprise_capture_score/step_15_5_scoring_by_theme/plots"
# The paper's Figure 3 was rendered from the pre-0709 backup, which is the
# frozen paper window. Pass --theme-plots to point at a newer run.
PAPER_THEME_PLOTS = "market_surprise_capture_score/_plots_backup_pre0709/step_15_5"

THEMES = [
    ("Production", "Supply & Production"),
    ("Inflation_Consumption_Services", "Demand & Inflation"),
    ("Labor_Market", "Labor Market"),
    ("Housing", "Housing"),
]

# --------------------------------------------------------------- betting ----
# Final cumulative LiveBetting return per model, per prediction market.
BETTING_SUBPATH = "polymarket_return"
PAPER_BETTING_DIR = "feb_mar_continuous_20260523"
BETTING_ANCHOR = "mar-anchor-claude-sonnet-4.5"

BETTING_MARKETS = [
    ("real_gdp_qoq", "Real GDP"),
    ("unemployment_rate", "Unemployment Rate"),
    ("cpi_yoy", "CPI"),
    # Added with the 2026-08-31 run; the frozen paper run has no payrolls CSV,
    # and a market whose CSV is absent is skipped with a printed note.
    ("nonfarm_payrolls_change", "Nonfarm Payrolls"),
]
# A quarterly market's tab is the whole quarter: the Q1 GDP market is bet
# across a "feb" and a "mar" segment (the paper's split of one window), and
# the tab merges them, re-based once at the Feb shared start.
QUARTERLY_MARKETS = {"real_gdp_qoq"}

# Segment tags written by the continuous-returns scripts: three-letter target
# months, plus "q2" for the second-quarter GDP market.
MONTH_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# The betting CSVs use their own arm names, distinct from the scoring ones.
BETTING_LABELS = {
    "gpt-5-search-api": "GPT-5",
    "claude-sonnet-4.5": "Claude-4.5-Sonnet",
    "qwen3-235b-a22b-instruct-2507": "Qwen3-235B",
    "qwen3-next-80b-a3b-instruct": "Qwen3-80B",
    "claude-code-agent": "Claude Code agent",
    "claude-code-multiagent": "Claude Code multi-agent",
    "bloomberg-consensus": "Bloomberg ECOS consensus",
    "fed-atlanta": "Atlanta Fed GDPNow",
    "fed-newyork": "NY Fed Staff Nowcast",
    "fed-stlouis": "St. Louis Fed Nowcast",
    "fed-forecast": "Cleveland Fed Nowcast",
    "fed-nowcast": "Chicago Fed CHURN",
}
# Arms never drawn on the LiveBetting charts. Empty: both Claude Code arms are
# drawn, and the Qwen arms are handled by the cutoff below rather than dropped.
BETTING_DROPPED: set[str] = set()

# Both Qwen arms changed regime on 2026-07-05 -- a synchronised upstream change
# to their job (HAZARD 2 in the private UPDATE_PIPELINE.md) that moved every
# indicator at once. Bets from that date on are not a nowcasting signal: the
# June CPI window returned +2771% from an open-ended bucket, July unemployment
# +569%. So for these arms a window holding ANY bet on or after the cutoff is
# dropped, together with every later window, and the arm's cumulative curve
# ends with its last clean window. Earlier windows stay (user decision
# 2026-09-03: keep the past months, drop only the broken ones). The betting
# run must therefore INCLUDE Qwen (`plot_continuous_0831.py --include-qwen`);
# note that puts Qwen back into the Feb shared start, which it binds.
BETTING_CUTOFF = {
    "qwen3-235b-a22b-instruct-2507": dt.datetime(2026, 7, 5),
    "qwen3-next-80b-a3b-instruct": dt.datetime(2026, 7, 5),
}


def _is_human(arm: str) -> bool:
    return arm.startswith("fed-") or arm == "bloomberg-consensus"


def read_themes(plots_root: Path, variant: str) -> dict:
    """One row per model, one score per theme. Aggregates only."""
    per_model: dict[str, list] = {}
    for key, _label in THEMES:
        table = plots_root / "plots_bloomberg_no_agent" / variant / key / "metric_ranking_table.csv"
        if not table.exists():
            sys.exit(f"theme table not found:\n  {table}\n"
                     "Pass --theme-plots at the directory holding plots_bloomberg_no_agent/.")
        with table.open() as fh:
            for r in csv.DictReader(fh):
                arm = r["model"]
                if arm in DROPPED:
                    continue
                # "or 0.0" collapses a rounded -0.0 to plain 0.0
                per_model.setdefault(arm, {})[key] = round(float(r["BDRC"]), 3) or 0.0

    rows = []
    for arm, by_theme in per_model.items():
        scores = [by_theme.get(k) for k, _ in THEMES]
        if any(v is None for v in scores):
            continue
        rows.append({
            "name": MODEL_LABELS.get(arm, arm),
            "kind": MODEL_KIND.get(arm, "llm"),
            "scores": scores,
        })
    # Order by mean score so the strongest all-round model leads.
    rows.sort(key=lambda r: -sum(r["scores"]) / len(r["scores"]))
    return {"columns": [label for _, label in THEMES], "rows": rows}


def read_betting(betting_dir: Path) -> list[dict]:
    """Final cumulative return per arm, per market: the last point of each
    published cumulative curve, so the table and the chart cannot disagree."""
    return [{"label": m["label"],
             "rows": [{"name": c["name"], "kind": c["kind"], "ret": _final(c)}
                      for c in m["series"]]}
            for m in read_betting_series(betting_dir)]


# ---------------------------------------------------------------- series ----
# The line charts are drawn on the page from data rather than shipped as PNGs,
# so they carry the site's own palette and type. What goes into series.json is
# deliberately COARSER than the pipeline's own output -- see check_release_safety
# for the caps that enforce it:
#   * betting curves are downsampled to ONE POINT PER DAY of stitched time and
#     rounded to 0.1pp. The underlying series is hourly.
#   * the case-study panels publish a SMOOTHED CURVE, not the raw nowcasts. A
#     Gaussian-kernel smooth of the 12-hourly means is a derived aggregate; the
#     per-hour records themselves stay private.
# Both are already public as PNGs in the paper; this changes precision, not kind.
SERIES_BETTING_STEP_DAYS = 1
CASE_STUDY_SUBPATH = "remove_outlier_and_plot/processed_final_analysis_data"
CASE_STUDY_ARM = "model_gpt-5-search-api"
CASE_STUDY_FILE = "2026-03_core_macroeconomic_conditions.csv"
CASE_STUDY_STEP_H = 12
CASE_STUDY_BANDWIDTH_H = 18.0   # Gaussian sigma, in hours
# Marker drawn on both panels: EVENT_TIME in render_paper_cpi_pce.py.
CASE_STUDY_EVENT = "2026-04-08T10:30"
# Same right edge as the paper's event-study figure. Without it the PCE panel
# runs to 2026-05-03, where a much larger end-April move dwarfs the 8 April
# shift the section is actually about -- the chart would then contradict the
# prose next to it.
CASE_STUDY_CLIP = "2026-04-20"
CASE_STUDY_PANELS = [
    ("cpi_yoy", "March 2026 CPI", "Year-over-year growth (%)"),
    ("pce_price_index_mom", "March 2026 PCE price index", "Month-over-month growth (%)"),
]


def _parse_ts(text: str):
    """Parse the pipeline's timestamp_local, which is mixed-format."""
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _segment_label(tag: str, first_bet: dt.datetime | None) -> tuple[str, str]:
    """('2026-02', 'Feb 2026') for a monthly tag; ('2026-Q2', 'Q2 2026') for 'q2'.

    The year is the bet year, or the one before when the target month is later
    in the calendar than the bet month (a December window bet in January)."""
    t = tag.lower()
    year = first_bet.year if first_bet else 0
    if t.startswith("q") and t[1:].isdigit():
        return f"{year}-Q{t[1:]}", f"Q{t[1:]} {year}"
    mnum = MONTH_ABBR.get(t)
    if mnum is None or not first_bet:
        return tag, tag
    if mnum > first_bet.month:
        year -= 1
    return f"{year}-{mnum:02d}", f"{MONTH_NAMES[mnum - 1]} {year}"


def _window_of(market_key: str, tag: str, first_bet: dt.datetime | None) -> tuple[str, str]:
    """The tab a segment belongs to: its month, or its quarter on a quarterly market."""
    key, label = _segment_label(tag, first_bet)
    if market_key in QUARTERLY_MARKETS and "-Q" not in key and "-" in key:
        y, m = key.split("-")
        q = (int(m) - 1) // 3 + 1
        return f"{y}-Q{q}", f"Q{q} {y}"
    return key, label


def _curve(arm: str, cell: dict[int, tuple[float, float]]) -> dict:
    """One published curve from {day -> (x, value)}: null for a day with no
    bet, so the chart can break the line."""
    days = sorted(cell)
    lo, hi = days[0], days[-1]
    # "or 0.0" turns a rounded -0.0 into plain 0.0
    values = [(round(cell[d][1], 1) or 0.0) if d in cell else None
              for d in range(lo, hi + 1)]
    return {
        "name": BETTING_LABELS.get(arm, arm),
        "kind": "human" if _is_human(arm) else "llm",
        "start": lo,
        "values": values,
    }


def _final(curve: dict) -> float:
    return next((v for v in reversed(curve["values"]) if v is not None), 0.0)


def read_betting_series(betting_dir: Path) -> list[dict]:
    """Cumulative-return curves, one point per day of stitched time, plus the
    same bets re-based window by window for the month tabs.

    `stitched_x_days` is the x-axis the paper figure plots, so downsampling on
    it keeps the shape identical while cutting ~1,400 hourly points per market
    to a few dozen. Arms join the chart at their own first bet, so each series
    carries its own `start` offset rather than a shared x grid.

    A tab window is a target month, or a quarter on a quarterly market. Its
    curve re-bases each arm at the window's start:
        return = (profit - profit at window start) / (dollars bet since),
    so the tab shows the return on THAT window's bets alone, and the windows'
    profits and stakes add back up to the cumulative curve. The pipeline's
    stitched series is continuous across segments (each segment's first row is
    the previous last row plus one bet), so an arm's last row before the window
    IS its window-start total. Day 0 is the window's earliest kept bet (the
    shared start, the same instant for every arm), measured in real time; an
    arm that joins later starts at its own first day.
    """
    markets = []
    for key, label in BETTING_MARKETS:
        path = betting_dir / f"continuous_returns_{key}_{BETTING_ANCHOR}.csv"
        if not path.exists():
            print(f"  ! no continuous-returns CSV for {key} in {betting_dir.name}; "
                  "market skipped")
            continue
        rows_by_arm: dict[str, list[tuple]] = {}
        for r in csv.DictReader(path.open()):
            arm = r["model"]
            if arm in BETTING_DROPPED:
                continue
            ts = _parse_ts(r["datetime_utc"])
            if ts is None:
                sys.exit(f"{path.name}: unparseable datetime_utc {r['datetime_utc']!r}")
            rows_by_arm.setdefault(arm, []).append((
                float(r["stitched_x_days"]), r["segment"],
                float(r["cumulative_profit"]), float(r["cumulative_invested"]),
                float(r["cumulative_return_pct"]), ts,
            ))
        # an arm with a cutoff loses the first window holding a bet at or
        # after it, and every window after that (the window is read off the
        # row's own timestamp, so no other arm's rows are needed)
        for arm, cutoff in BETTING_CUTOFF.items():
            rows = rows_by_arm.get(arm)
            if not rows:
                continue
            rows.sort(key=lambda t: t[0])
            wins = [_window_of(key, seg, ts)[0] for _x, seg, _p, _i, _r, ts in rows]
            bad = next((i for i, (row, w) in enumerate(zip(rows, wins))
                        if row[5] >= cutoff), None)
            if bad is not None:
                first_bad = wins[bad]
                keep = [r for r, w in zip(rows, wins)
                        if wins.index(w) < wins.index(first_bad)]
                if keep:
                    rows_by_arm[arm] = keep
                else:
                    del rows_by_arm[arm]
        # segments in chart order, each mapped to its tab window; a window
        # opens at the earliest kept bet any arm places in it
        seg_lo: dict[str, float] = {}
        seg_first: dict[str, dt.datetime] = {}
        for rows in rows_by_arm.values():
            for x, seg, _p, _i, _ret, ts in rows:
                if seg not in seg_lo or x < seg_lo[seg]:
                    seg_lo[seg] = x
                if seg not in seg_first or ts < seg_first[seg]:
                    seg_first[seg] = ts
        seg_order = sorted(seg_lo, key=seg_lo.get)
        win_of: dict[str, str] = {}
        win_label: dict[str, str] = {}
        win_t0: dict[str, dt.datetime] = {}
        for seg in seg_order:
            wkey, wlabel = _window_of(key, seg, seg_first[seg])
            win_of[seg] = wkey
            win_label.setdefault(wkey, wlabel)
            if wkey not in win_t0 or seg_first[seg] < win_t0[wkey]:
                win_t0[wkey] = seg_first[seg]
        win_order = list(dict.fromkeys(win_of[seg] for seg in seg_order))

        cumulative: list[dict] = []
        by_win: dict[str, list[dict]] = {w: [] for w in win_order}
        day = dt.timedelta(days=SERIES_BETTING_STEP_DAYS)
        for arm, rows in rows_by_arm.items():
            rows.sort(key=lambda t: t[0])
            cell: dict[int, tuple[float, float]] = {}
            win_cells: dict[str, dict[int, tuple[float, float]]] = {}
            cur = None
            p0 = i0 = last_p = last_i = 0.0
            for x, seg, profit, invested, ret, ts in rows:
                d = int(x // SERIES_BETTING_STEP_DAYS)
                # last observation within the day wins
                if d not in cell or x > cell[d][0]:
                    cell[d] = (x, ret)
                w = win_of[seg]
                if w != cur:
                    # a new window: re-base on the running totals at its start
                    p0, i0, cur = last_p, last_i, w
                last_p, last_i = profit, invested
                bet = invested - i0
                if bet <= 0:
                    continue
                wret = (profit - p0) / bet * 100.0
                wd = int((ts - win_t0[w]) // day)
                wc = win_cells.setdefault(w, {})
                if wd not in wc or x > wc[wd][0]:
                    wc[wd] = (x, wret)
            cumulative.append(_curve(arm, cell))
            for w, wc in win_cells.items():
                by_win[w].append(_curve(arm, wc))

        cumulative.sort(key=lambda c: -_final(c))
        months = []
        for w in win_order:
            curves = by_win[w]
            if not curves:
                continue
            curves.sort(key=lambda c: -_final(c))
            months.append({"key": w, "label": win_label[w], "series": curves})
        markets.append({"key": key, "label": label,
                        "step_days": SERIES_BETTING_STEP_DAYS,
                        "series": cumulative, "months": months})
    return markets


def _gaussian_smooth(hours: list[float], values: list[float],
                     bandwidth: float) -> list[float]:
    """Kernel-smooth an irregular series. Pure stdlib on purpose: this module is
    imported by check_release_safety.py, which the pre-commit hook runs under the
    system python3, where scipy is not guaranteed."""
    out = []
    for h in hours:
        num = den = 0.0
        for hj, vj in zip(hours, values):
            w = math.exp(-0.5 * ((h - hj) / bandwidth) ** 2)
            num += w * vj
            den += w
        out.append(num / den if den else vj)
    return out


def read_case_study(results_root: Path) -> list[dict]:
    """Smoothed GPT-5 nowcast curves for the two case-study indicators.

    Reads the outlier-filtered mirror (`processed_final_analysis_data/`), bins to
    12-hourly means, then publishes the Gaussian-smoothed curve ONLY. The raw
    per-hour nowcasts are not published.
    """
    path = results_root / CASE_STUDY_SUBPATH / CASE_STUDY_ARM / CASE_STUDY_FILE
    if not path.exists():
        sys.exit(f"case-study CSV not found:\n  {path}\n"
                 "It is produced by remove_outlier_and_plot (Step 3).")
    by_var: dict[str, list[tuple[dt.datetime, float]]] = {}
    for r in csv.DictReader(path.open()):
        v = r.get("variable")
        if v not in {k for k, _l, _u in CASE_STUDY_PANELS}:
            continue
        ts = _parse_ts(r.get("timestamp_local", ""))
        try:
            val = float(r["value"])
        except (TypeError, ValueError):
            continue
        if ts is not None:
            by_var.setdefault(v, []).append((ts, val))

    panels = []
    step = dt.timedelta(hours=CASE_STUDY_STEP_H)
    for key, label, unit in CASE_STUDY_PANELS:
        clip = dt.datetime.strptime(CASE_STUDY_CLIP, "%Y-%m-%d")
        pts = sorted(pt for pt in by_var.get(key, []) if pt[0] <= clip)
        if not pts:
            sys.exit(f"case study: no rows for {key} in {path}")
        t0 = pts[0][0].replace(minute=0, second=0, microsecond=0)
        buckets: dict[int, list[float]] = {}
        for ts, val in pts:
            buckets.setdefault(int((ts - t0) / step), []).append(val)
        idx = sorted(buckets)
        hours = [i * CASE_STUDY_STEP_H for i in idx]
        means = [sum(buckets[i]) / len(buckets[i]) for i in idx]
        smooth = _gaussian_smooth(hours, means, CASE_STUDY_BANDWIDTH_H)
        panels.append({
            "key": key,
            "label": label,
            "unit": unit,
            "start": t0.isoformat(timespec="minutes"),
            "step_hours": CASE_STUDY_STEP_H,
            "event": CASE_STUDY_EVENT,
            "values": [round(v, 3) for v in smooth],
        })
    return panels


def _score_row(r: dict) -> dict:
    return {
        "name": MODEL_LABELS.get(r["model"], r["model"]),
        "kind": MODEL_KIND.get(r["model"], "llm"),
        "score": round(float(r["BDRC_point"]), 3),
        "ci": [round(float(r["BDRC_ci90_lo"]), 3), round(float(r["BDRC_ci90_hi"]), 3)],
        "events": int(r["n_events"]),
        "note": "",
    }


def _consensus_row(label: str) -> dict:
    # The consensus is 0 by construction and is not a row in the CSV.
    return {"name": label, "kind": "human", "score": 0.0,
            "ci": None, "events": None, "note": "reference, 0 by construction"}


def read_scores(csv_path: Path, consensus_label: str,
                exclude: set[str] = frozenset()) -> list[dict]:
    if not csv_path.exists():
        sys.exit(
            f"scoring CSV not found:\n  {csv_path}\n\n"
            "This file is intentionally NOT in the public repo (see DATA_SOURCES.md).\n"
            "Point --results-root at your private Results/ checkout, e.g.\n"
            "  python tools/update_site.py --results-root /home/ruiyi/livemacro/Results"
        )

    rows = []
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in COLS if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"{csv_path} is missing expected columns: {missing}")
        for r in reader:
            if r["model"] in DROPPED or r["model"] in exclude:
                continue
            rows.append(_score_row(r))

    rows.sort(key=lambda x: -x["score"])
    if rows:
        rows[0]["note"] = "leads the panel"
    rows.insert(0, _consensus_row(consensus_label))
    return rows


def _period_label(key: str) -> str:
    """'2026-Q1' -> 'Q1 2026'; '2026-03' -> 'Mar 2026'."""
    y, rest = key.split("-", 1)
    if rest.startswith("Q"):
        return f"{rest} {y}"
    return f"{MONTH_NAMES[int(rest) - 1]} {y}"


def _months_span(months: list[str]) -> str:
    """['2025-11', '2025-12'] -> 'Nov–Dec 2025'; ['2026-07'] -> 'Jul 2026'."""
    if not months:
        return ""
    def name(m):
        return MONTH_NAMES[int(m.split("-")[1]) - 1]
    y0, y1 = months[0].split("-")[0], months[-1].split("-")[0]
    if len(months) == 1:
        return f"{name(months[0])} {y0}"
    if y0 == y1:
        return f"{name(months[0])}–{name(months[-1])} {y0}"
    return f"{name(months[0])} {y0}–{name(months[-1])} {y1}"


def read_period_scores(period_dir: Path, consensus_label: str) -> list[dict]:
    """One leaderboard panel per period (quarters on the site), from the
    by-period sibling of the overlay (score_by_period_<MMDD>.py). Same columns
    and rounding as the headline; each panel is the same statistic on the
    releases whose reference month falls in that period. The newest panel is
    flagged as the quarter in progress when it does not yet hold three months,
    and any short panel says which months it covers."""
    meta_path = period_dir / "metadata.json"
    if not meta_path.exists():
        sys.exit(f"by-period metadata not found:\n  {meta_path}\n"
                 "Run score_by_period_<MMDD>.py in the private checkout first, "
                 "or drop --periods-dir.")
    meta = json.loads(meta_path.read_text())
    group = meta.get("group", "quarter")
    csv_path = period_dir / f"final_vs_final_by_{group}_ci.csv"
    if not csv_path.exists():
        sys.exit(f"by-period CSV not found:\n  {csv_path}")
    need = ("model", "period") + COLS[1:]
    by_period: dict[str, list[dict]] = {}
    with csv_path.open() as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in need if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"{csv_path} is missing expected columns: {missing}")
        for r in reader:
            if r["model"] in DROPPED:
                continue
            by_period.setdefault(r["period"], []).append(_score_row(r))

    months_of = {p["key"]: p["months"] for p in meta.get("periods", [])}
    keys = sorted(by_period)
    panels = []
    for i, key in enumerate(keys):
        rows = by_period[key]
        rows.sort(key=lambda x: -x["score"])
        rows[0]["note"] = f"leads the {group}"
        rows.insert(0, _consensus_row(consensus_label))
        panel = {"key": key, "label": _period_label(key), "rows": rows}
        months = months_of.get(key, [])
        newest = i == len(keys) - 1
        if group == "quarter" and months and (newest or len(months) < 3):
            panel["covers"] = _months_span(months) + (" so far" if newest and len(months) < 3 else "")
        if newest and (group != "quarter" or len(months) < 3):
            panel["current"] = True
        panels.append(panel)
    return panels


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS,
                    help="private Results/ checkout holding the scoring overlays "
                         "(default: %(default)s, or $LIVEMACRO_RESULTS)")
    ap.add_argument("--figures-root", type=Path, default=DEFAULT_FIGURES,
                    help="private Paper/figures/ directory (default: %(default)s)")
    ap.add_argument("--overlay", default="bloomberg_overlay",
                    help="overlay dir under %s (default: %%(default)s)" % SCORING_SUBPATH)
    ap.add_argument("--window", default=None, help="override the headline window caption")
    ap.add_argument("--periods-dir", default=None,
                    help="by-period sibling of the overlay under %s, written by "
                         "score_by_period_<MMDD>.py; adds the quarter tabs to the "
                         "leaderboard. Omit to publish the all-quarters table only."
                         % SCORING_SUBPATH)
    ap.add_argument("--consensus-label", default="Bloomberg ECOS consensus",
                    help="name of the zero-by-construction reference row (default: "
                         "%(default)s -- the live months score against the Investing.com "
                         "calendar consensus, which is Bloomberg-derived and tracks ECOS "
                         "at 0.997 correlation in surprise units, so it keeps the name)")
    ap.add_argument("--betting-window", default=None,
                    help="override the LiveBetting window caption")
    ap.add_argument("--theme-plots", default=None,
                    help="dir holding plots_bloomberg_no_agent/, relative to --results-root "
                         "(default: the frozen paper backup %s; the live path is %s)"
                         % (PAPER_THEME_PLOTS, THEME_SUBPATH))
    ap.add_argument("--theme-variant", default="final_vs_final",
                    help="scoring variant subdir under the theme plots (default: %(default)s)")
    ap.add_argument("--betting-dir", default=None,
                    help="continuous-returns dir under %s (default: the frozen paper run %s)"
                         % (BETTING_SUBPATH, PAPER_BETTING_DIR))
    ap.add_argument("--skip-themes", action="store_true",
                    help="leave the themes/betting blocks in the JSON untouched")
    ap.add_argument("--skip-series", action="store_true",
                    help="leave docs/data/series.json (the line charts) untouched")
    ap.add_argument("--last-updated", default=None,
                    help="YYYY-MM-DD the RESULTS last moved. Defaults to today, which "
                         "is only right when this run actually refreshed the numbers -- "
                         "a cosmetic or chart-only run should pass the previous value, "
                         "or the page overstates how fresh the data is.")
    ap.add_argument("--next-update", default=None,
                    help="YYYY-MM-DD of the next refresh (default: last_updated + 30 days)")
    ap.add_argument("--skip-figures", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the JSON, write nothing")
    args = ap.parse_args()

    if args.results_root.resolve() == (REPO / "Results").resolve():
        sys.exit(
            "refusing to read the public repo's own Results/ as the pipeline source.\n"
            "The scoring overlays are withheld from this repo by design; point\n"
            "--results-root at the private checkout instead."
        )

    out = SITE / "data/leaderboard.json"
    data = json.loads(out.read_text())

    csv_path = args.results_root / SCORING_SUBPATH / args.overlay / "bloomberg_final_vs_final_ci.csv"
    data["headline"]["rows"] = read_scores(csv_path, args.consensus_label)
    data["headline"].pop("months", None)   # the pre-quarter key, never published again
    if args.periods_dir:
        data["headline"]["periods"] = read_period_scores(
            args.results_root / SCORING_SUBPATH / args.periods_dir, args.consensus_label)
    else:
        data["headline"].pop("periods", None)
    # Record the overlay by name only. The absolute private path stays out of the
    # published JSON.
    data["headline"]["source"] = f"{SCORING_SUBPATH}/{args.overlay}/ (not redistributed)"
    if args.window:
        data["headline"]["window"] = args.window

    if not args.skip_themes:
        theme_root = args.results_root / (args.theme_plots or PAPER_THEME_PLOTS)
        themes = read_themes(theme_root, args.theme_variant)
        data["themes"]["columns"] = themes["columns"]
        data["themes"]["rows"] = themes["rows"]

        betting_dir = args.results_root / BETTING_SUBPATH / (args.betting_dir or PAPER_BETTING_DIR)
        data["betting"]["markets"] = read_betting(betting_dir)
    if args.betting_window:
        data["betting"]["window"] = args.betting_window

    stamp = (dt.date.fromisoformat(args.last_updated) if args.last_updated
             else dt.date.today())
    data["last_updated"] = stamp.isoformat()
    data["next_update"] = args.next_update or (stamp + dt.timedelta(days=30)).isoformat()

    series = None
    if not args.skip_series:
        betting_dir = args.results_root / BETTING_SUBPATH / (args.betting_dir or PAPER_BETTING_DIR)
        series_path = SITE / "data/series.json"
        series = json.loads(series_path.read_text()) if series_path.exists() else {}
        series["_comment"] = (
            "Line-chart data for the site. Deliberately coarser than the pipeline: "
            "betting curves are one point per day of stitched time (the source is "
            "hourly); case-study panels are a Gaussian-smoothed curve, not the raw "
            "nowcasts. Aggregates only -- see tools/check_release_safety.py."
        )
        series["betting"] = {
            "note": data["betting"].get("note", ""),
            "window": data["betting"].get("window", ""),
            "markets": read_betting_series(betting_dir),
        }
        series["case_study"] = {
            "note": "GPT-5 nowcasts, Gaussian-smoothed. The marker is 10:30 ET, "
                    "8 April 2026.",
            "panels": read_case_study(args.results_root),
        }

    if args.dry_run:
        print(json.dumps({k: data[k] for k in ("headline", "themes", "betting")}, indent=2))
        if series is not None:
            print(json.dumps(series, indent=2)[:2000])
        return

    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote docs/data/leaderboard.json  ({len(data['headline']['rows'])} rows)")

    if series is not None:
        sp = SITE / "data/series.json"
        sp.write_text(json.dumps(series, separators=(",", ":")) + "\n")
        mk = series["betting"]["markets"]
        n_cum = sum(len(s["values"]) for m in mk for s in m["series"])
        n_mon = sum(len(s["values"]) for m in mk for mo in m.get("months", [])
                    for s in mo["series"])
        n_case = sum(len(p["values"]) for p in series["case_study"]["panels"])
        print(f"wrote docs/data/series.json  ({sp.stat().st_size} bytes; "
              f"{n_cum} cumulative + {n_mon} month + {n_case} case-study points)")

    if not args.skip_figures:
        dest = SITE / "assets/figures"
        for name in FIGURES:
            src = args.figures_root / name
            if src.exists():
                shutil.copy2(src, dest / name)
            else:
                print(f"  ! missing figure, left as-is: {name}")
        print("refreshed docs/assets/figures/")

    print("\nrunning the release-safety check...")
    sys.stdout.flush()
    rc = subprocess.call([sys.executable, str(TOOLS / "check_release_safety.py")])
    if rc != 0:
        sys.exit("\nrefresh written, but docs/ is NOT safe to publish. Fix the above "
                 "before committing.")
    print(f"\nnext:\n  git add docs && git commit -m 'site: refresh "
          f"{data['last_updated']}' && git push")


if __name__ == "__main__":
    main()
