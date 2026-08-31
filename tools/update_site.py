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

    if args.dry_run:
        print(json.dumps({k: data[k] for k in ("headline", "themes", "betting")}, indent=2))
        return

    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote docs/data/leaderboard.json  ({len(data['headline']['rows'])} rows)")

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
