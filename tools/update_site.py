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
    python tools/update_site.py --overlay investing_overlay_0906 \
        --window "Investing.com consensus proxy - target periods Apr-Aug 2026"
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
]

# The betting CSVs use their own arm names, distinct from the scoring ones.
BETTING_LABELS = {
    "gpt-5-search-api": "GPT-5",
    "claude-sonnet-4.5": "Claude-4.5-Sonnet",
    "qwen3-235b-a22b-instruct-2507": "Qwen3-235B",
    "qwen3-next-80b-a3b-instruct": "Qwen3-80B",
    "bloomberg-consensus": "Bloomberg ECOS consensus",
    "fed-atlanta": "Atlanta Fed GDPNow",
    "fed-newyork": "NY Fed Staff Nowcast",
    "fed-stlouis": "St. Louis Fed Nowcast",
    "fed-forecast": "Cleveland Fed Nowcast",
    "fed-nowcast": "Chicago Fed CHURN",
}
BETTING_DROPPED = {"claude-code-agent"}


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
    """Final cumulative return per arm, per market. Aggregates only."""
    markets = []
    for key, label in BETTING_MARKETS:
        path = betting_dir / f"continuous_returns_{key}_{BETTING_ANCHOR}.csv"
        if not path.exists():
            sys.exit(f"betting CSV not found:\n  {path}\n"
                     "Pass --betting-dir at the continuous-returns output directory.")
        # Keep only the last point of each arm's stitched series.
        last: dict[str, tuple] = {}
        with path.open() as fh:
            for r in csv.DictReader(fh):
                arm = r["model"]
                if arm in BETTING_DROPPED:
                    continue
                x = float(r["stitched_x_days"])
                if arm not in last or x > last[arm][0]:
                    last[arm] = (x, float(r["cumulative_return_pct"]))
        rows = [{
            "name": BETTING_LABELS.get(arm, arm),
            "kind": "human" if _is_human(arm) else "llm",
            "ret": round(val, 1),
        } for arm, (_x, val) in last.items()]
        rows.sort(key=lambda r: -r["ret"])
        markets.append({"label": label, "rows": rows})
    return markets


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


def read_betting_series(betting_dir: Path) -> list[dict]:
    """Cumulative-return curves, one point per day of stitched time.

    `stitched_x_days` is the x-axis the paper figure plots, so downsampling on
    it keeps the shape identical while cutting ~1,400 hourly points per market
    to a few dozen. Arms join the chart at their own first bet, so each series
    carries its own `start` offset rather than a shared x grid.
    """
    markets = []
    for key, label in BETTING_MARKETS:
        path = betting_dir / f"continuous_returns_{key}_{BETTING_ANCHOR}.csv"
        if not path.exists():
            sys.exit(f"betting CSV not found:\n  {path}")
        by_arm: dict[str, dict[int, tuple[float, float]]] = {}
        for r in csv.DictReader(path.open()):
            arm = r["model"]
            if arm in BETTING_DROPPED:
                continue
            x = float(r["stitched_x_days"])
            day = int(x // SERIES_BETTING_STEP_DAYS)
            cell = by_arm.setdefault(arm, {})
            # last observation within the day wins
            if day not in cell or x > cell[day][0]:
                cell[day] = (x, float(r["cumulative_return_pct"]))
        series = []
        for arm, cell in by_arm.items():
            days = sorted(cell)
            lo, hi = days[0], days[-1]
            # null for a day with no bet, so the chart can break the line
            values = [round(cell[d][1], 1) if d in cell else None
                      for d in range(lo, hi + 1)]
            series.append({
                "name": BETTING_LABELS.get(arm, arm),
                "kind": "human" if _is_human(arm) else "llm",
                "start": lo,
                "values": values,
            })
        series.sort(key=lambda s: -(next((v for v in reversed(s["values"])
                                          if v is not None), 0.0)))
        markets.append({"key": key, "label": label,
                        "step_days": SERIES_BETTING_STEP_DAYS, "series": series})
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


def read_scores(csv_path: Path) -> list[dict]:
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
            model = r["model"]
            if model in DROPPED:
                continue
            rows.append({
                "name": MODEL_LABELS.get(model, model),
                "kind": MODEL_KIND.get(model, "llm"),
                "score": round(float(r["BDRC_point"]), 3),
                "ci": [round(float(r["BDRC_ci90_lo"]), 3), round(float(r["BDRC_ci90_hi"]), 3)],
                "events": int(r["n_events"]),
                "note": "",
            })

    rows.sort(key=lambda x: -x["score"])
    if rows:
        rows[0]["note"] = "leads the panel"

    # The consensus is 0 by construction and is not a row in the CSV.
    rows.insert(0, {
        "name": "Bloomberg ECOS consensus", "kind": "human", "score": 0.0,
        "ci": None, "events": None, "note": "reference, 0 by construction",
    })
    return rows


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
    ap.add_argument("--next-update", default=None,
                    help="YYYY-MM-DD of the next refresh (default: today + 30 days)")
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
    data["headline"]["rows"] = read_scores(csv_path)
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

    today = dt.date.today()
    data["last_updated"] = today.isoformat()
    data["next_update"] = args.next_update or (today + dt.timedelta(days=30)).isoformat()

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
        n = sum(len(s["values"]) for m in series["betting"]["markets"]
                for s in m["series"]) + sum(len(p["values"])
                                            for p in series["case_study"]["panels"])
        print(f"wrote docs/data/series.json  ({sp.stat().st_size} bytes, {n} points)")

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
