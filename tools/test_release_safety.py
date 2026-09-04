#!/usr/bin/env python3
"""Adversarial tests for check_release_safety.py.

Each case plants a realistic leak into a THROWAWAY COPY of docs/ and asserts the
check rejects it. The real docs/ is never modified. Run it whenever the check or
the site structure changes:

    python tools/test_release_safety.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
SITE = REPO / "docs"

# A realistic fragment of the withheld scoring overlay.
OVERLAY_CSV = (
    "model,mode,n_events,BDRC_point,BDRC_ci90_lo,BDRC_ci90_hi,BP_RMSE_point\n"
    "gpt-5-search-api,final_vs_final,54,0.00358,-0.02086,0.01534,8.40374\n"
    "claude-sonnet-4.5-api,final_vs_final,43,-0.02547,-0.03466,-0.01894,7.66320\n"
    "qwen3-235b-a22b,final_vs_final,28,-0.00454,-0.02795,0.01177,5.05778\n"
    "arima_aic,final_vs_final,55,-0.11158,-0.15256,-0.07871,11.97680\n"
)


def run_check(repo: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(repo / "tools/check_release_safety.py")],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def sandbox(tmp: Path) -> Path:
    """A disposable repo holding pristine copies of docs/ and tools/."""
    repo = tmp / "repo"
    repo.mkdir()
    ign = shutil.ignore_patterns("__pycache__")
    shutil.copytree(SITE, repo / "docs", ignore=ign)
    shutil.copytree(TOOLS, repo / "tools", ignore=ign)
    shutil.copy2(REPO / ".gitignore", repo / ".gitignore")
    return repo / "docs"


# ---------------------------------------------------------------- mutations

def m_raw_csv(d: Path):
    (d / "data/overlay.csv").write_text(OVERLAY_CSV)

def m_csv_renamed_txt(d: Path):
    (d / "assets/notes.txt").write_text(OVERLAY_CSV)

def m_csv_as_allowed_name(d: Path):
    """Row-level data hidden inside a file that IS on the allowlist."""
    p = d / "assets/css/style.css"
    p.write_text(p.read_text() + "\n/*\n" + OVERLAY_CSV + "*/\n")

def m_csv_pasted_in_html(d: Path):
    p = d / "index.html"
    p.write_text(p.read_text() + "\n<!--\n" + OVERLAY_CSV + "-->\n")

def m_hourly_key(d: Path):
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["headline"]["hourly"] = [{"t": f"2026-04-0{i%9+1}", "v": 2.7 + i * 1e-4}
                               for i in range(1423)]
    p.write_text(json.dumps(j))

def m_per_release_rows(d: Path):
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["headline"]["rows"] = [
        {"name": f"gpt-5 release {i}", "kind": "llm", "score": 0.001 * i,
         "ci": [-0.01, 0.01], "events": 1} for i in range(200)]
    p.write_text(json.dumps(j))

def m_extra_row_field(d: Path):
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["headline"]["rows"][1]["raw_consensus"] = 2.68
    p.write_text(json.dumps(j))

def m_base64_blob(d: Path):
    p = d / "assets/js/main.js"
    p.write_text(p.read_text() + "\nconst D='" + "QUJDRGVmZ2hpams" * 60 + "';\n")

def m_number_run(d: Path):
    p = d / "assets/js/main.js"
    series = ", ".join(f"{2.7 + i*0.001:.4f}" for i in range(60))
    p.write_text(p.read_text() + f"\nconst SERIES=[{series}];\n")

def m_rogue_figure(d: Path):
    shutil.copy2(d / "assets/figures/theme_housing.png",
                 d / "assets/figures/per_release_scatter.png")

def m_csv_as_png(d: Path):
    (d / "assets/figures/theme_housing.png").write_text(OVERLAY_CSV)

def m_secret(d: Path):
    p = d / "assets/js/main.js"
    p.write_text(p.read_text() + '\nconst KEY="sk-abcdefghijklmnopqrstuvwxyz012345";\n')

def m_private_link(d: Path):
    p = d / "index.html"
    p.write_text(p.read_text().replace(
        "</body>", '<a href="https://github.com/LiveMacro/LiveMacro.git">src</a></body>'))

def m_abs_path(d: Path):
    p = d / "index.html"
    p.write_text(p.read_text().replace("</body>", "<!-- /home/ruiyi/livemacro/Results --></body>"))

def m_weaken_gitignore(d: Path):
    p = d / ".gitignore"
    p.write_text(p.read_text().replace("*.csv\n", ""))

def m_weaken_repo_gitignore(d: Path):
    p = d.parent / ".gitignore"
    p.write_text(p.read_text().replace(
        "Results/market_surprise_capture_score/**/bloomberg_overlay/\n", ""))

def m_oversized_json(d: Path):
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["_comment"] = "x" * 40_000
    p.write_text(json.dumps(j))

def m_pdf(d: Path):
    (d / "paper.pdf").write_bytes(b"%PDF-1.7\n" + b"0" * 100)

def m_bad_date(d: Path):
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["last_updated"] = "sometime in August"
    p.write_text(json.dumps(j))

def m_source_leaks_path(d: Path):
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["headline"]["source"] = "/home/ruiyi/livemacro/Results/.../bloomberg_overlay/x.csv"
    p.write_text(json.dumps(j))

def m_delete_required(d: Path):
    (d / "data/leaderboard.json").unlink()


# ---- series.json (the line-chart data added 2026-09-01) -------------------
# series.json is the one file allowed to hold a numeric series, so it needs its
# own adversarial cases: the generic comma-run sniffer is deliberately exempted
# there and check_series() carries the load instead.

def _series(d: Path) -> dict:
    return json.loads((d / "data/series.json").read_text())

def _write_series(d: Path, obj: dict):
    (d / "data/series.json").write_text(json.dumps(obj, separators=(",", ":")))

def m_series_hourly_dump(d: Path):
    """The whole point of the caps: an hourly curve must not fit."""
    o = _series(d)
    o["betting"]["markets"][0]["series"][0]["values"] = [
        round(i * 0.1, 1) for i in range(1500)]
    _write_series(d, o)

def m_series_raw_nowcasts(d: Path):
    """Per-hour model output smuggled alongside the smoothed curve."""
    o = _series(d)
    o["case_study"]["panels"][0]["raw"] = [2.412, 2.398, 2.401, 2.377]
    _write_series(d, o)

def m_series_subdaily_grid(d: Path):
    o = _series(d)
    o["betting"]["markets"][0]["step_days"] = 0
    _write_series(d, o)

def m_series_fine_case_grid(d: Path):
    o = _series(d)
    o["case_study"]["panels"][0]["step_hours"] = 1
    _write_series(d, o)

def m_series_full_precision(d: Path):
    """Unrounded values expose more of the underlying than the chart needs."""
    o = _series(d)
    o["betting"]["markets"][0]["series"][0]["values"][0] = 12.34567890123
    _write_series(d, o)

def m_series_case_full_precision(d: Path):
    o = _series(d)
    o["case_study"]["panels"][0]["values"][0] = 2.4123456789
    _write_series(d, o)

def m_series_extra_top_key(d: Path):
    o = _series(d)
    o["overlay"] = {"rows": [1, 2, 3]}
    _write_series(d, o)

def m_series_oversized(d: Path):
    o = _series(d)
    o["_comment"] = "x" * 40000
    _write_series(d, o)

def m_series_too_many_numbers(d: Path):
    """Under the per-curve cap, but far too many curves."""
    o = _series(d)
    mk = o["betting"]["markets"][0]
    proto = mk["series"][0]
    mk["series"] = [dict(proto, name=f"arm{i}") for i in range(12)]
    for m2 in o["betting"]["markets"][1:]:
        m2["series"] = [dict(proto, name=f"arm{i}") for i in range(12)]
    _write_series(d, o)

# ---- the month tabs (2026-09-03): a month block is the same curve shape under
# a second key, so every check above must reach into it too.

def m_series_month_hourly_dump(d: Path):
    o = _series(d)
    o["betting"]["markets"][0]["months"][0]["series"][0]["values"] = [
        round(i * 0.1, 1) for i in range(1500)]
    _write_series(d, o)

def m_series_month_extra_key(d: Path):
    """Per-bet detail riding along inside a month tab's curve."""
    o = _series(d)
    o["betting"]["markets"][0]["months"][0]["series"][0]["bets"] = [1.0, 0.5, 0.25]
    _write_series(d, o)

def m_series_month_block_extra_key(d: Path):
    o = _series(d)
    o["betting"]["markets"][0]["months"][0]["prices"] = [0.31, 0.29, 0.33]
    _write_series(d, o)

def m_series_too_many_months(d: Path):
    """A 'month' per market resolution instead of per target month."""
    o = _series(d)
    mk = o["betting"]["markets"][0]
    proto = mk["months"][0]
    mk["months"] = [dict(proto, key=f"2026-{i:02d}", label=f"m{i}") for i in range(13)]
    _write_series(d, o)

def m_month_row_extra_field(d: Path):
    """leaderboard.json: a raw value added to a period tab's model row."""
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["headline"]["periods"][0]["rows"][1]["raw_surprise"] = 0.42
    p.write_text(json.dumps(j))

def m_theme_period_extra_field(d: Path):
    """leaderboard.json: a per-field value added to a theme period row."""
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    j["themes"]["periods"][0]["rows"][0]["surprises"] = [0.1, -0.2, 0.3]
    p.write_text(json.dumps(j))

def m_month_panels_per_release(d: Path):
    """leaderboard.json: a 'period' tab per release is a per-release table."""
    p = d / "data/leaderboard.json"
    j = json.loads(p.read_text())
    row = j["headline"]["periods"][0]["rows"][1]
    j["headline"]["periods"] = [
        {"key": f"r{i}", "label": f"release {i}", "rows": [dict(row, score=0.001 * i)]}
        for i in range(200)]
    p.write_text(json.dumps(j))


CASES = [
    ("raw overlay CSV dropped in docs/",          m_raw_csv,             "not on the allowlist"),
    ("overlay CSV renamed to .txt",               m_csv_renamed_txt,     "not on the allowlist"),
    ("overlay CSV hidden inside style.css",       m_csv_as_allowed_name, "tabular data"),
    ("overlay CSV pasted into index.html",        m_csv_pasted_in_html,  "tabular data"),
    ("1423 hourly points under an allowed key",   m_hourly_key,          "unknown key"),
    ("200 per-release rows in the table",         m_per_release_rows,    "exceeds the 30 cap"),
    ("raw consensus added to a model row",        m_extra_row_field,     "unknown key"),
    ("base64 blob embedded in main.js",           m_base64_blob,         "base64"),
    ("numeric series embedded in main.js",        m_number_run,          "numeric series"),
    ("new figure not declared in FIGURES",        m_rogue_figure,        "not on the allowlist"),
    ("CSV renamed to an approved .png",           m_csv_as_png,          "not a real .png"),
    ("API key committed in main.js",              m_secret,              "API key"),
    ("link to the private repo",                  m_private_link,        "PRIVATE repo"),
    ("absolute local path in index.html",         m_abs_path,            "absolute local"),
    ("docs/.gitignore weakened",                  m_weaken_gitignore,    "no longer excludes"),
    ("repo .gitignore weakened",                  m_weaken_repo_gitignore, "no longer excludes"),
    ("leaderboard.json inflated past cap",        m_oversized_json,      "exceeds"),
    ("paper PDF added",                           m_pdf,                 "not on the allowlist"),
    ("malformed date field",                      m_bad_date,            "YYYY-MM-DD"),
    ("source field leaking a filesystem path",    m_source_leaks_path,   "leaks a filesystem path"),
    ("required file deleted",                     m_delete_required,     "is missing"),
    ("series.json: hourly curve dump",            m_series_hourly_dump,  "exceeds the"),
    ("series.json: raw nowcasts smuggled in",     m_series_raw_nowcasts, "unexpected key"),
    ("series.json: sub-daily betting grid",       m_series_subdaily_grid, "step_days"),
    ("series.json: sub-6h case-study grid",       m_series_fine_case_grid, "finer than"),
    ("series.json: unrounded betting values",     m_series_full_precision, "rounded"),
    ("series.json: unrounded case-study values",  m_series_case_full_precision, "dp"),
    ("series.json: unexpected top-level key",     m_series_extra_top_key, "unexpected top-level"),
    ("series.json: inflated past byte cap",       m_series_oversized,    "exceeds the"),
    ("series.json: too many numeric literals",    m_series_too_many_numbers, "cap"),
    ("series.json: hourly dump inside a month tab", m_series_month_hourly_dump, "exceeds the"),
    ("series.json: per-bet detail in a month curve", m_series_month_extra_key, "unexpected key"),
    ("series.json: prices smuggled into a month block", m_series_month_block_extra_key, "unexpected key"),
    ("series.json: a month tab per resolution",    m_series_too_many_months, "months exceeds"),
    ("leaderboard.json: raw value in a period row", m_month_row_extra_field, "unknown key"),
    ("leaderboard.json: a period tab per release", m_month_panels_per_release, "exceeds the 30 cap"),
    ("leaderboard.json: per-field values in a theme row", m_theme_period_extra_field, "unknown key"),
]


def main() -> int:
    failures = []

    with tempfile.TemporaryDirectory() as t:
        d = sandbox(Path(t))
        rc, out = run_check(d.parent)
        if rc != 0:
            print("BASELINE FAILED — a pristine copy of docs/ should pass:\n")
            print(out)
            return 1
    print(f"  ok   baseline: pristine docs/ passes\n")

    for name, mutate, expect in CASES:
        with tempfile.TemporaryDirectory() as t:
            d = sandbox(Path(t))
            mutate(d)
            rc, out = run_check(d.parent)
        if rc == 0:
            failures.append((name, "NOT CAUGHT — check exited 0", out))
            print(f"  FAIL {name}")
        elif expect.lower() not in out.lower():
            failures.append((name, f"caught, but not for the expected reason ({expect!r})", out))
            print(f"  WARN {name} — rejected, but not via {expect!r}")
        else:
            print(f"  ok   blocked: {name}")

    print()
    if failures:
        print(f"{len(failures)} of {len(CASES)} case(s) need attention:\n")
        for name, why, out in failures:
            print(f"--- {name}: {why}")
            print("\n".join("    " + l for l in out.strip().splitlines()[-12:]))
            print()
        return 1
    print(f"PASS — all {len(CASES)} leak attempts blocked, baseline still passes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
