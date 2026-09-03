# LiveMacroEval website — tooling

The published site is `../docs/`, served by GitHub Pages at
**https://livemacroeval.github.io/**. This folder holds the machinery that
maintains it. Nothing here is published.

This repo holds **only** the website. The code and data release lives in
[LiveMacroEval/LiveMacroEval](https://github.com/LiveMacroEval/LiveMacroEval),
and the nowcasting pipeline that produces the leaderboard numbers is in a
separate private repo — see "The rule this enforces" below.

```
docs/                        THE PUBLISHED SITE — 16 files, nothing else
├── index.html               structure and prose. Contains NO numbers.
├── data/leaderboard.json    every number in the tables, incl. the month tabs
├── data/series.json         the line charts: cumulative + per-month curves
├── assets/css/style.css
├── assets/js/main.js        fetches the JSON, renders the tables
├── assets/figures/*.png     the 10 aggregate figures from the paper
├── .gitignore               blocks row-level file types from landing here
└── .nojekyll                serve as-is, no Jekyll

tools/                       NOT published
├── update_site.py           regenerates the JSON from the private pipeline
├── check_release_safety.py  the gate: what docs/ may contain
├── test_release_safety.py   36 adversarial cases against that gate
├── hooks/pre-commit         local enforcement
├── README.md                this file
└── DEPLOY.md                enabling Pages, custom domains
```

## The rule this enforces

The repo is public. Three pipeline inputs are not redistributable — the
Bloomberg ECOS survey exports, the FirstRateData ES futures minute bars, and the
scraped Investing.com calendar (see [DATA_SOURCES.md](https://github.com/LiveMacroEval/LiveMacroEval/blob/main/DATA_SOURCES.md) in the code repo). `.gitignore` already
excludes them, along with
`Results/market_surprise_capture_score/**/bloomberg_overlay/`, which is the
scoring output the website reads. This repo mirrors those patterns in its own
root `.gitignore` so the same guarantee holds here.

**`docs/` may contain the final aggregate figures and tables the paper reports,
and nothing else.** No row-level record, no per-release value, no raw vendor
number. Today that is 16 files: one HTML page, one stylesheet, one script, ten
figures, one JSON, and two dotfiles.

## How that is enforced

`check_release_safety.py` is an **allowlist**, not a blocklist — every file in
`docs/` must be named in `ALLOWED_FILES` or in `update_site.FIGURES`, so adding
anything to the public site is a deliberate, reviewable edit. On top of that:

| Layer | Catches |
|---|---|
| Allowlist + per-file size caps | any new file; anything unexpectedly large |
| `leaderboard.json` schema | unknown keys at any depth, arrays over 30, over 150 numeric literals, over 32 KB, malformed dates, paths in `source` |
| Byte sniffing of every text file | a CSV renamed to `.txt`, a table pasted into the HTML or CSS, base64 blobs, long numeric runs — extension is never trusted |
| Image magic bytes | a data file renamed to `.png` |
| Pattern scan | API keys, tokens, absolute `/home/...` paths, links to the private repo, names of withheld sources |
| `.gitignore` verification | either ignore layer being weakened |

`test_release_safety.py` plants 21 realistic leaks — the real overlay CSV dropped
in, renamed, hidden inside `style.css`, pasted into the HTML; 1,423 hourly points
under an approved key; 200 per-release rows; a raw consensus field added to a
model row; a rogue figure; a CSV renamed to `.png`; a leaked key; and more — and
asserts each is rejected *for the expected reason*, while a pristine `docs/`
still passes.

It runs in three places, so it is not something you can forget:

1. **`update_site.py`** runs it after every refresh and exits non-zero if it fails.
2. **`tools/hooks/pre-commit`** blocks any commit touching `docs/`. Install once
   per clone:
   ```bash
   ln -sf ../../tools/hooks/pre-commit .git/hooks/pre-commit
   ```
3. **`.github/workflows/release-safety.yml`** runs the check *and* the test suite
   on every push and pull request, plus a scan of the entire git history for
   withheld data paths. This is the authoritative gate — it holds even for
   commits made from a machine without the hook.

## Preview locally

```bash
cd docs && python3 -m http.server 8000
```

Serve it — don't open `index.html` via `file://`, or the browser blocks the
`fetch()` of `leaderboard.json` and the tables stay empty.

## The monthly refresh

1. Run the pipeline in your private checkout, per its `UPDATE_PIPELINE.md`.
   That produces a new dated overlay, e.g. `investing_overlay_0906/`.
2. Regenerate and audit in one step:

   ```bash
   . /home/ruiyi/anaconda3/bin/activate && conda activate livemacro
   python tools/update_site.py --results-root /home/ruiyi/livemacro/Results
   ```

   `--dry-run` inspects without writing. `--results-root` defaults to
   `/home/ruiyi/livemacro/Results` (override with `$LIVEMACRO_RESULTS`); figures
   come from `--figures-root`, default `/home/ruiyi/livemacro/Paper/figures`.
   The script refuses to read this repo's own `Results/`, since the overlay is
   withheld from it by design.
3. Commit and push. The hook and CI re-run the audit.

   ```bash
   git add docs && git commit -m "site: refresh $(date +%F)" && git push
   ```

### Which overlay

The default is `bloomberg_overlay`, the **frozen paper window** (Nov 2025 – Mar
2026). The Bloomberg-based score is frozen at the 2026-05-05 cutoff; the live
board scores every month against the Investing.com calendar consensus
(`investing_overlay_<MMDD>`), which is Bloomberg-derived and tracks ECOS at
0.997 correlation in surprise units, so the site keeps calling the reference
row Bloomberg (user decision 2026-09-03).

### The month tabs (added 2026-09-03)

The leaderboard and the LiveBetting charts each carry an "All months" view plus
one tab per target month. Two inputs feed them:

- **Scores.** `score_by_month_<MMDD>.py` in the private
  `step_15_4_live_scoring/` writes `investing_overlay_<MMDD>_by_month/`, the
  headline statistic and its bootstrap CI restricted to each month's releases.
  The unit is the release EVENT, never split: advance GDP counts in the last
  month of its quarter, and an event whose fields carry two reference months
  goes whole to the month most of its fields belong to. The script asserts
  that the months partition the headline (counts add up, union reproduces
  it), and `validate_by_month_<MMDD>.py` re-derives every row with no
  pipeline imports. Pass the directory as `--months-dir`; omit the flag to
  publish the all-months table only.
- **Betting curves.** Derived here from the same continuous-returns CSVs as
  the cumulative curves: each window (a month, or a quarter on the GDP
  market) is re-based at its start, so a tab shows the return on that
  window's bets alone. `python tools/validate_months.py` recomputes every
  window from the raw per-market bet files and checks the published curves
  against them; run it after each refresh.

The full 2026-08-25 refresh command:

```bash
python tools/update_site.py \
  --overlay investing_overlay_0825 --months-dir investing_overlay_0825_by_month \
  --theme-plots market_surprise_capture_score/step_15_5_scoring_by_theme/plots_0825 \
  --betting-dir continuous_returns_20260831 \
  --window "Target reference periods Nov 2025 – Jul 2026 (official releases Dec 1, 2025 – Aug 18, 2026)" \
  --betting-window "Target windows Feb – Jul 2026; Q2 2026 for real GDP." \
  --last-updated 2026-08-25 --skip-figures
```

Both Qwen arms (`BETTING_DROPPED`) are off the betting charts after their
July 2026 regime break, while staying in every score table. The arms that
went live in June 2026 appear everywhere they have data.

### Adding a model arm

Add its code name to `MODEL_LABELS` in `update_site.py`. Arms in `DROPPED` are
excluded to stay consistent with Figure 2 of the paper.

### Adding a figure

Add the filename to `FIGURES` in `update_site.py`. `check_release_safety.py`
imports that list, so the allowlist follows automatically — but only aggregate
figures that appear in the paper belong there.

## Editing content

Prose lives in `docs/index.html`; numbers live in `docs/data/leaderboard.json`.
Keeping that split is what makes the refresh a one-command operation, and what
lets the schema check be strict.
