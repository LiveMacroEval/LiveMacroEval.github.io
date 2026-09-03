#!/usr/bin/env python3
"""Gate on what docs/ is allowed to publish. Exit 0 = safe, 1 = stop.

This repo is public. Several pipeline inputs are not redistributable -- the
Bloomberg ECOS survey exports, the FirstRateData ES futures minute bars, and the
scraped Investing.com calendar (see ../DATA_SOURCES.md). The website may show the
FINAL AGGREGATE FIGURES AND TABLES that the paper reports, and nothing else. No
row-level record, no per-release value, no raw vendor number.

The check is an ALLOWLIST, not a blocklist: every file in docs/ must be named in
ALLOWED_FILES or in update_site.FIGURES, or this fails. Adding anything new to
the published site is therefore a deliberate, reviewable edit to this file. On
top of that:

  * leaderboard.json is validated against an explicit recursive schema, with
    caps on array length, numeric-literal count, and byte size, so a per-release
    or hourly dump cannot fit through even under an approved key;
  * every file's bytes are sniffed -- a CSV renamed to .txt, a table pasted into
    the HTML, or a base64 blob is caught by shape, not by extension;
  * images must really be images, and are capped in size;
  * both .gitignore layers are re-verified.

Run it: python tools/check_release_safety.py
It also runs automatically from update_site.py, from the git pre-commit hook
(tools/hooks/pre-commit), and in CI on every push (.github/workflows/).
tools/test_release_safety.py exercises it against 36 planted leaks.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
SITE = REPO / "docs"

sys.path.insert(0, str(TOOLS))
from update_site import FIGURES  # single source of truth for the figure set

# --------------------------------------------------------------------------
# 1. The allowlist. Anything under docs/ not named here fails the check.
# --------------------------------------------------------------------------
ALLOWED_FILES = {
    ".gitignore",
    ".nojekyll",
    "CNAME",                      # only once a custom domain is set up
    "index.html",
    "assets/css/style.css",
    "assets/js/main.js",
    "data/leaderboard.json",
    "data/series.json",
}
ALLOWED_FILES |= {f"assets/figures/{name}" for name in FIGURES}

# Figure 1 (the pipeline overview) is drawn inline in index.html since
# 2026-09-01 -- vector shapes and real text rebuilt from Paper/plots/figure1.pptx
# -- so only its raster icons ship as files: cropped, downscaled copies of the
# slide's pictures (factory, cart, ... , the FED building). No data in any of them.
PIPELINE_ICONS = {"arima", "briefcase", "cart", "factory", "fed", "housing",
                  "polymarket", "target", "theme"}
PIPELINE_ICON_FILES = [f"assets/figures/pipeline/{n}.png" for n in sorted(PIPELINE_ICONS)]
ALLOWED_FILES |= set(PIPELINE_ICON_FILES)

# Per-file byte ceilings. Generous, but a data dump blows past them.
MAX_BYTES = {
    ".json": 64 * 1024,   # series.json carries the month tabs; each JSON also has its own cap below
    ".html": 128 * 1024,
    ".css": 64 * 1024,
    ".js": 64 * 1024,
    ".md": 32 * 1024,
    ".py": 64 * 1024,
    ".png": 2 * 1024 * 1024,
}
DEFAULT_MAX_BYTES = 16 * 1024

IMAGE_MAGIC = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".svg": b"<",
}

# --------------------------------------------------------------------------
# 2. leaderboard.json schema. Only the final table may live here.
# --------------------------------------------------------------------------
MAX_JSON_BYTES = 32 * 1024
MAX_ARRAY_LEN = 30        # a per-release or hourly series is far longer
# One row per model per PANEL. The period tabs (2026-09-03) add a panel per
# quarter -- four so far, each <= 8 rows of score + CI pair + count -- so the
# real file holds ~180 numbers (the cap was sized when the tabs were monthly).
# Still one row per model, never one per release: a release-level dump would
# need thousands.
MAX_NUMERIC_LITERALS = 500

# --------------------------------------------------------------------------
# 2b. series.json -- the line charts, drawn on the page instead of shipped as
# PNGs. This file DOES hold time series, which leaderboard.json deliberately
# forbids, so it gets its OWN caps rather than relaxing the ones above: the
# leaderboard's job is to stay one row per model, and that must not change
# because the charts arrived.
#
# What makes the series publishable at all (decision 2026-09-01):
#   * betting curves are cumulative RETURN PERCENTAGES derived from our own
#     bets and PUBLIC Polymarket prices. Polymarket is not among the withheld
#     sources (Bloomberg ECOS, FirstRateData ES bars, the Investing calendar),
#     so nothing non-redistributable can be reconstructed from them.
#   * case-study panels publish a SMOOTHED CURVE only. The per-hour nowcasts
#     behind it are not published; `raw` is rejected outright below so a later
#     edit cannot quietly add them.
#   * both are already public as PNGs in the paper. This changes precision,
#     not kind.
# The caps are sized just above the real payload, so a full hourly dump
# (>1,400 points per market) still cannot fit. Raised 2026-09-03 for the month
# tabs: four markets across six target months, each published twice -- the
# cumulative curve and the month-by-month re-based curve -- is ~3,300 daily
# points in ~27 KB. The per-curve length cap and the one-day step floor are
# what keep an hourly series out; the totals just have to hold the real set.
SERIES_MAX_BYTES = 40 * 1024
SERIES_MAX_LEN = 200          # longest single curve; hourly would be 1,400+
SERIES_MAX_NUMERIC = 4500     # the real file holds ~3,300
SERIES_MAX_MARKETS = 8
SERIES_MAX_CURVES = 12        # per market, and per month within a market
SERIES_MAX_MONTHS = 12        # per market
SERIES_BETTING_DP = 1         # betting values rounded to 0.1pp
SERIES_CASE_DP = 3

S = str
N = (int, float)

ROW = {"name": S, "kind": S, "score": N, "ci": "ci", "events": "int_or_null",
       "note?": S}
# Agent-design rows: one configuration each, naming the arm it is, on one
# coverage-matched event set. No CI of their own.
AGENT_ROW = {"name": S, "score": N, "kind?": S, "best?": bool, "note?": S,
             "model?": S, "events?": "int_or_null"}
# One aggregate LiveMacro Score per model per theme -- 4 numbers a row, the same
# shape as the paper's Figure 3 panels. No per-release value can ride along.
THEME_ROW = {"name": S, "kind": S, "scores": [N]}
# One final cumulative LiveBetting return per arm per market. The hourly series
# behind it (>1,400 points per window) stays in the private checkout; only its
# last value is published.
BET_ROW = {"name": S, "kind": S, "ret": N, "note?": S}
# A period tab (a quarter): the headline table restricted to the releases of
# that period. Same row shape, so nothing finer than a per-model aggregate can
# appear; `covers` and `current` caption a quarter still in progress.
PERIOD_PANEL = {"key": S, "label": S, "rows": [ROW], "covers?": S, "current?": bool}
SCHEMA = {
    "_comment?": S,
    "last_updated": "date",
    "next_update": "date",
    "headline": {"title": S, "window": S, "note": S, "period_note?": S, "source": S,
                 "rows": [ROW], "periods?": [PERIOD_PANEL]},
    "agent_design": {"title": S, "window": S, "note": S, "rows": [AGENT_ROW]},
    "themes": {"title": S, "window": S, "note": S,
               "columns": [S], "rows": [THEME_ROW]},
    "betting": {"title": S, "window": S, "note": S,
                "markets": [{"label": S, "rows": [BET_ROW]}]},
    # Each indicator carries a link to the agency that publishes it.
    "indicators": [{"theme": S, "blurb": S, "items": [{"name": S, "url": S}]}],
    "comparators": {"fed": [{"name": S, "target": S, "url": S}]},
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# --------------------------------------------------------------------------
# 3. Content sniffing, applied to every text file regardless of extension.
# --------------------------------------------------------------------------
SECRET_PATTERNS = [
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,}|gh[pousr]_[A-Za-z0-9]{30,})"), "API key or GitHub token"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|passwd|password|bearer)\s*[:=]\s*['\"][^'\"]{8,}"), "hardcoded credential"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
]
LEAK_PATTERNS = [
    (re.compile(r"github\.com[:/]LiveMacro/LiveMacro(?:\.git|\b)"), "link to the PRIVATE repo"),
    (re.compile(r"\bece228-\d+\b"), "internal hostname"),
    (re.compile(r"/home/[a-z0-9_]+/"), "absolute local filesystem path"),
    (re.compile(r"(?i)\bbloomberg[_ ]?(daily|release)[_ ]?consensus\b"), "withheld Bloomberg table name"),
    (re.compile(r"(?i)\b(firstratedata|es[_ ]futures[_ ]minute)\b"), "withheld futures-bar source"),
]
# docs/ is published content only; nothing in it may name a private path.
PROSE_EXEMPT: set[str] = set()
# Only series.json may carry a numeric run -- check_series() validates it far
# more tightly than the generic sniffer could. Keep this set to exactly one file.
NUMBER_RUN_EXEMPT = {"data/series.json"}

# A delimiter-separated row: >=4 separators and >=3 numeric fields.
CSV_ROW_RE = re.compile(r"^[^,\t]*([,\t][^,\t]*){4,}$")
NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{500,}={0,2}")
# A long run of comma-separated numbers pasted inline, e.g. an embedded series.
NUMBER_RUN_RE = re.compile(r"(-?\d+\.\d+\s*,\s*){25,}")

TEXT_EXT = {".html", ".css", ".js", ".json", ".md", ".py", ".txt", ""}

problems: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


# --------------------------------------------------------------------------


def check_allowlist() -> None:
    seen = set()
    for p in sorted(SITE.rglob("*")):
        if p.is_dir() or "__pycache__" in p.parts or ".git" in p.parts:
            continue
        rel = p.relative_to(SITE).as_posix()
        seen.add(rel)
        if rel not in ALLOWED_FILES:
            fail(f"{rel}: not on the allowlist. If it belongs on the public site, "
                 "add it to ALLOWED_FILES here and say why in the commit.")
            continue
        limit = MAX_BYTES.get(p.suffix.lower(), DEFAULT_MAX_BYTES)
        size = p.stat().st_size
        if size > limit:
            fail(f"{rel}: {size:,} bytes exceeds the {limit:,} cap for {p.suffix or 'this file'} "
                 "-- unexpectedly large files are how bulk data arrives")

    required = {"index.html", "assets/css/style.css", "assets/js/main.js",
                "data/leaderboard.json", ".nojekyll", ".gitignore"}
    for r in sorted(required - seen):
        fail(f"{r} is missing -- the site will not render correctly")

    missing_figs = [f"assets/figures/{n}" for n in FIGURES
                    if f"assets/figures/{n}" not in seen]
    if missing_figs:
        notes.append(f"{len(missing_figs)} figure(s) declared but absent: "
                     f"{', '.join(missing_figs)}")
    notes.append(f"{len(seen)} file(s), all on the allowlist")


def check_images() -> None:
    rels = [f"assets/figures/{name}" for name in FIGURES] + PIPELINE_ICON_FILES
    for rel in rels:
        p = SITE / rel
        if not p.exists():
            continue
        magic = IMAGE_MAGIC.get(p.suffix.lower())
        head = p.read_bytes()[:16]
        if magic and not head.startswith(magic):
            fail(f"{rel}: not a real {p.suffix} file -- "
                 "a data file may have been renamed")


# --------------------------------------------------------------------------


def walk_schema(node, spec, path: str) -> None:
    if spec == "date":
        if not (isinstance(node, str) and DATE_RE.match(node)):
            fail(f"leaderboard.json {path}: expected YYYY-MM-DD, got {node!r}")
        return
    if spec == "int_or_null":
        if not (node is None or isinstance(node, int)):
            fail(f"leaderboard.json {path}: expected an integer or null, got {node!r}")
        return
    if spec == "ci":
        if node is None:
            return
        if not (isinstance(node, list) and len(node) == 2
                and all(isinstance(x, N) and not isinstance(x, bool) for x in node)):
            fail(f"leaderboard.json {path}: 'ci' must be null or exactly two numbers, got {node!r}")
        return

    if isinstance(spec, dict):
        if not isinstance(node, dict):
            fail(f"leaderboard.json {path}: expected an object, got {type(node).__name__}")
            return
        required = {k for k in spec if not k.endswith("?")}
        optional = {k[:-1] for k in spec if k.endswith("?")}
        for k in sorted(required - set(node)):
            fail(f"leaderboard.json {path}: missing required key {k!r}")
        for k in sorted(set(node) - required - optional):
            fail(f"leaderboard.json {path}: unknown key {k!r} -- every published field "
                 "must be declared in SCHEMA, so row-level data cannot ride along")
        for k, v in node.items():
            sub = spec.get(k, spec.get(k + "?"))
            if sub is not None:
                walk_schema(v, sub, f"{path}.{k}")
        return

    if isinstance(spec, list):
        if not isinstance(node, list):
            fail(f"leaderboard.json {path}: expected a list, got {type(node).__name__}")
            return
        if len(node) > MAX_ARRAY_LEN:
            fail(f"leaderboard.json {path}: {len(node)} elements exceeds the "
                 f"{MAX_ARRAY_LEN} cap -- this looks like per-release data, not a summary table")
        for i, item in enumerate(node):
            walk_schema(item, spec[0], f"{path}[{i}]")
        return

    if spec is bool:
        if not isinstance(node, bool):
            fail(f"leaderboard.json {path}: expected a boolean, got {node!r}")
        return
    if spec is N or spec == N:
        if isinstance(node, bool) or not isinstance(node, N):
            fail(f"leaderboard.json {path}: expected a number, got {node!r}")
        return
    if spec is S:
        if not isinstance(node, str):
            fail(f"leaderboard.json {path}: expected a string, got {type(node).__name__}")
        return


def count_numbers(o) -> int:
    if isinstance(o, bool):
        return 0
    if isinstance(o, N):
        return 1
    if isinstance(o, dict):
        return sum(count_numbers(v) for v in o.values())
    if isinstance(o, list):
        return sum(count_numbers(v) for v in o)
    return 0


def _round_ok(v: float, dp: int) -> bool:
    return abs(v - round(v, dp)) < 1e-9


def check_series() -> None:
    """series.json: time series are allowed here, but only coarsened ones.

    Validated explicitly rather than through walk_schema, so the leaderboard's
    schema and its messages stay untouched.
    """
    f = SITE / "data/series.json"
    if not f.exists():
        return  # optional; the allowlist pass reports an unexpected file
    raw = f.read_bytes()
    if len(raw) > SERIES_MAX_BYTES:
        fail(f"data/series.json: {len(raw):,} bytes exceeds the "
             f"{SERIES_MAX_BYTES:,} cap")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"data/series.json is not valid JSON: {e}")
        return

    allowed_top = {"_comment", "betting", "case_study"}
    extra = set(d) - allowed_top
    if extra:
        fail(f"data/series.json: unexpected top-level key(s) {sorted(extra)}")

    n_pts = 0

    def check_curves(curves: list, where: str) -> int:
        """Betting curves under one market or one month tab. Returns points."""
        pts = 0
        if len(curves) > SERIES_MAX_CURVES:
            fail(f"data/series.json {where}: {len(curves)} curves exceeds "
                 f"{SERIES_MAX_CURVES}")
        for c in curves:
            extra = set(c) - {"name", "kind", "start", "values"}
            if extra:
                fail(f"data/series.json curve {c.get('name')!r} ({where}): "
                     f"unexpected key(s) {sorted(extra)}")
            vals = c.get("values", [])
            if len(vals) > SERIES_MAX_LEN:
                fail(f"data/series.json curve {c.get('name')!r} ({where}): "
                     f"{len(vals)} points exceeds the {SERIES_MAX_LEN} cap")
            pts += len(vals)
            for v in vals:
                if v is None:
                    continue
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    fail(f"data/series.json curve {c.get('name')!r}: non-numeric "
                         f"point {v!r}")
                elif not _round_ok(float(v), SERIES_BETTING_DP):
                    fail(f"data/series.json curve {c.get('name')!r}: {v} carries "
                         f"more than {SERIES_BETTING_DP} dp -- betting curves "
                         f"must be published rounded")
        return pts

    bet = d.get("betting", {})
    markets = bet.get("markets", [])
    if len(markets) > SERIES_MAX_MARKETS:
        fail(f"data/series.json: {len(markets)} betting markets exceeds "
             f"{SERIES_MAX_MARKETS}")
    for m in markets:
        extra = set(m) - {"key", "label", "step_days", "series", "months"}
        if extra:
            fail(f"data/series.json betting market {m.get('key')!r}: "
                 f"unexpected key(s) {sorted(extra)}")
        if m.get("step_days", 0) < 1:
            fail(f"data/series.json betting market {m.get('key')!r}: step_days "
                 f"must be >= 1 day -- a finer grid is an hourly dump")
        n_pts += check_curves(m.get("series", []), f"market {m.get('key')!r}")
        # The month tabs: the same bets re-based per target month. Same curve
        # shape and the same daily grid, so the same checks apply; a market
        # may not carry more tabs than there are months in a year.
        months = m.get("months", [])
        if len(months) > SERIES_MAX_MONTHS:
            fail(f"data/series.json market {m.get('key')!r}: {len(months)} "
                 f"months exceeds {SERIES_MAX_MONTHS}")
        for mo in months:
            extra = set(mo) - {"key", "label", "series"}
            if extra:
                fail(f"data/series.json market {m.get('key')!r} month "
                     f"{mo.get('key')!r}: unexpected key(s) {sorted(extra)}")
            n_pts += check_curves(mo.get("series", []),
                                  f"market {m.get('key')!r} month {mo.get('key')!r}")

    cs = d.get("case_study", {})
    panels = cs.get("panels", [])
    if len(panels) > SERIES_MAX_MARKETS:
        fail(f"data/series.json: {len(panels)} case-study panels exceeds "
             f"{SERIES_MAX_MARKETS}")
    for pn in panels:
        extra = set(pn) - {"key", "label", "unit", "start", "step_hours",
                           "event", "values"}
        if extra:
            fail(f"data/series.json panel {pn.get('key')!r}: unexpected key(s) "
                 f"{sorted(extra)} -- raw per-hour nowcasts must NOT be published")
        if pn.get("step_hours", 0) < 6:
            fail(f"data/series.json panel {pn.get('key')!r}: step_hours "
                 f"{pn.get('step_hours')!r} is finer than the 6h floor")
        vals = pn.get("values", [])
        if len(vals) > SERIES_MAX_LEN:
            fail(f"data/series.json panel {pn.get('key')!r}: {len(vals)} points "
                 f"exceeds the {SERIES_MAX_LEN} cap")
        n_pts += len(vals)
        for v in vals:
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                fail(f"data/series.json panel {pn.get('key')!r}: non-numeric "
                     f"point {v!r}")
            elif not _round_ok(float(v), SERIES_CASE_DP):
                fail(f"data/series.json panel {pn.get('key')!r}: {v} carries more "
                     f"than {SERIES_CASE_DP} dp")

    n = count_numbers(d)
    if n > SERIES_MAX_NUMERIC:
        fail(f"data/series.json holds {n} numeric literals, over the "
             f"{SERIES_MAX_NUMERIC} cap -- the charts publish a downsampled "
             "curve, not the hourly series")

    n_months = sum(len(m.get("months", [])) for m in markets)
    notes.append(f"series.json: {len(markets)} betting markets ({n_months} month "
                 f"tabs), {len(panels)} case-study panels, {n_pts} plotted points, "
                 f"{len(raw):,} bytes -- all within caps")


def check_leaderboard() -> None:
    f = SITE / "data/leaderboard.json"
    if not f.exists():
        return  # already reported by the allowlist pass
    raw = f.read_bytes()
    if len(raw) > MAX_JSON_BYTES:
        fail(f"data/leaderboard.json: {len(raw):,} bytes exceeds the {MAX_JSON_BYTES:,} cap")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"data/leaderboard.json is not valid JSON: {e}")
        return

    walk_schema(d, SCHEMA, "$")

    n = count_numbers(d)
    if n > MAX_NUMERIC_LITERALS:
        fail(f"data/leaderboard.json holds {n} numeric literals, over the "
             f"{MAX_NUMERIC_LITERALS} cap -- the published table should be one row "
             "per model, not one per release")

    src = str(d.get("headline", {}).get("source", ""))
    if src.startswith("/") or "\\" in src or re.search(r"/home/|C:", src):
        fail(f"data/leaderboard.json 'source' leaks a filesystem path: {src!r}")

    rows = d.get("headline", {}).get("rows", [])
    n_months = len(d.get("headline", {}).get("periods", []))
    notes.append(f"leaderboard.json: {len(rows)} model rows, {n_months} period tabs, "
                 f"{n} numeric literals, {len(raw):,} bytes -- all within caps")


# --------------------------------------------------------------------------


def check_content() -> None:
    """Sniff every text file's bytes. Extension is not trusted."""
    for p in sorted(SITE.rglob("*")):
        if p.is_dir() or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(SITE).as_posix()
        if p.suffix.lower() not in TEXT_EXT:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue

        for pat, label in SECRET_PATTERNS:
            if pat.search(text):
                fail(f"{rel}: possible {label}")
        for pat, label in LEAK_PATTERNS:
            if rel in PROSE_EXEMPT:
                continue
            m = pat.search(text)
            if m:
                fail(f"{rel}: {label} -- {m.group(0)!r}")

        if BASE64_BLOB_RE.search(text):
            fail(f"{rel}: long base64 blob -- data may be embedded inline")
        # series.json is the ONE file allowed to hold a numeric series, and it is
        # validated structurally by check_series() instead -- shape, per-curve
        # length, rounding, step floor and total point count. Exempting it here
        # removes no coverage; it swaps a generic sniff for a stricter check.
        # Every other file, this one included in spirit, still fails on a run.
        if rel not in NUMBER_RUN_EXEMPT and NUMBER_RUN_RE.search(text):
            fail(f"{rel}: a long run of comma-separated decimals -- "
                 "a numeric series appears to be embedded inline")

        # Consecutive delimiter-separated numeric rows = a pasted table.
        run = 0
        for line in text.splitlines():
            s = line.strip()
            if CSV_ROW_RE.match(s) and sum(
                    1 for c in re.split(r"[,\t]", s) if NUMERIC_RE.match(c.strip())) >= 3:
                run += 1
                if run >= 3:
                    fail(f"{rel}: {run}+ consecutive delimiter-separated numeric rows "
                         "-- tabular data appears to be embedded in this file")
                    break
            else:
                run = 0


def check_gitignore() -> None:
    repo_gi = REPO / ".gitignore"
    if not repo_gi.exists():
        fail("repo .gitignore is missing")
    else:
        text = repo_gi.read_text()
        for needed in ("bloomberg_overlay/", "Results/bloomberg_consensus/",
                       "Results/data_sp500futures/"):
            if needed not in text:
                fail(f"repo .gitignore no longer excludes {needed!r} -- "
                     "a withheld input could now be committed")

    docs_gi = SITE / ".gitignore"
    if not docs_gi.exists():
        fail("docs/.gitignore is missing")
        return
    text = docs_gi.read_text()
    for needed in ("*.csv", "*.parquet", "*.xlsx", "*.pdf"):
        if needed not in text:
            fail(f"docs/.gitignore no longer excludes {needed!r}")
    notes.append("both .gitignore layers intact")


def main() -> int:
    check_allowlist()
    check_images()
    check_leaderboard()
    check_series()
    check_content()
    check_gitignore()

    for n in notes:
        print(f"  ok   {n}")
    if problems:
        print(f"\nFAIL -- {len(problems)} issue(s); do not publish:\n")
        for p in problems:
            print(f"  !! {p}")
        return 1
    print("\nPASS -- docs/ holds only the final figures and tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
