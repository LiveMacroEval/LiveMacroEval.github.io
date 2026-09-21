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
├── assets/figures/pipeline/ the Figure 1 icons (every chart is drawn from data/*.json)
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

### The period tabs (added 2026-09-03)

The leaderboard carries an "All quarters" view plus one tab per quarter; each
LiveBetting chart carries "All months" plus one tab per betting window. Two
inputs feed them:

- **Scores.** `score_by_period_<MMDD>.py` in the private
  `step_15_4_live_scoring/` writes `investing_overlay_<MMDD>_by_quarter/`
  (or `_by_month/` with `--group month`), the headline statistic and its
  bootstrap CI restricted to each period's releases. The unit is the release
  EVENT, never split: advance GDP counts in the last month of its quarter,
  and an event whose fields fall in two periods goes whole to the period most
  of its fields belong to. The script asserts that the periods partition the
  headline (counts add up, union reproduces it), and
  `validate_by_period_<MMDD>.py` re-derives every row with no pipeline
  imports. Pass the directory as `--periods-dir`; omit the flag to publish
  the all-quarters table only. The newest quarter is flagged as in progress
  and captioned with the months it holds so far. The by-period run also
  writes the ALL-period rows the site publishes as its headline: identical
  to the overlay's for every arm except those with outage-frozen nowcasts
  (`STALE_EVENTS`: the Claude Code arms, releases of 2026-08-10 to 08-21),
  whose stale events are dropped so the multi-agent headline matched its
  agent-design row (12 events on the 2026-08-25 data). No event counts are
  published.
- **Betting curves.** Derived here from the same continuous-returns CSVs as
  the cumulative curves: each window (a month, or a quarter on the GDP
  market) is re-based at its start, so a tab shows the return on that
  window's bets alone. `python tools/validate_months.py` recomputes every
  window from the raw per-market bet files and checks the published curves
  against them; run it after each refresh.

The full 2026-08-25 refresh command:

```bash
python tools/update_site.py \
  --overlay investing_overlay_0825 --periods-dir investing_overlay_0825_by_quarter \
  --theme-plots market_surprise_capture_score/step_15_5_scoring_by_theme/plots_0825 \
  --betting-dir continuous_returns_20260831_with_qwen \
  --window "Target reference periods Nov 2025 – Jul 2026 (official releases Dec 1, 2025 – Aug 18, 2026)" \
  --betting-window "Target windows Feb – Jul 2026; Q2 2026 for real GDP." \
  --last-updated 2026-08-25 --skip-figures
```

Both Qwen arms changed regime on 2026-07-05, so `BETTING_CUTOFF` drops, for
each of them, the first betting window holding a bet from that date on and
every later window; earlier windows and the score tables keep them. They are
also kept off the cumulative "All months" chart and its table
(`BETTING_CUMULATIVE_HIDDEN`), where a curve that stops months early would
not be comparable. Payrolls is not published (no institutional nowcast).
The betting run must include Qwen
(`plot_continuous_0831.py --include-qwen --output-dir
continuous_returns_20260831_with_qwen`), which also puts Qwen back into the
February shared start that it binds. The arms that went live in June 2026
appear everywhere they have data. The tool-and-agent-design comparison is its
own table (`agent_design` in the JSON), each row naming the arm it is: one base
model run three ways, on one shared set of releases. Since the September 2026
refresh that is the Claude Sonnet 5 trio (`AGENT_DESIGN_ROWS`: the plain prompt
through Claude Code as the control, the financial plug-in, the multi-agent
team; all three moved to Sonnet 5 on 2026-09-04/05, where the shared set
starts). The refresh rebuilds it from the private overlay's
`agent_design_bmsc.csv` / `agent_design_events.csv` and refuses unless the
three rows share one complete release set with a LiveMacro Score;
`--keep-agent-design` publishes the card as it stands. The caption's base-model
name is `AGENT_DESIGN_BASE_MODEL`. (The card published on 2026-08-25 was the
earlier study, with the retired `claude-sonnet-4.5-api` as the control.)

### Adding a model arm

A refresh run through the private pipeline needs nothing here: the arm's entry
in the pipeline roster (`Results/pipeline/config.py` `ARMS`) carries its display
name, and the pipeline hands it to `MODEL_LABELS` / `BETTING_LABELS` before
calling `update_site.py`. Run by hand, `update_site.py` stops on an arm it has
no label for rather than publish its raw id -- add the arm to those two tables.
Arms in `DROPPED` are excluded to stay consistent with Figure 2 of the paper;
adding an arm there is the one decision the pipeline cannot make for you.

A new arm is not shown straight away (user decisions 2026-09-20). Counted in
scored releases -- releases with an S&P 500 futures move -- behind its
all-quarters LiveMacro Score: under `MIN_SCORED_RELEASES` (5) it is shown
nowhere; from 5 it is in the quarter tabs; it joins the all-quarters board and
theme table from `MIN_SCORED_RELEASES_ALL` (11, i.e. more than ten), so a
newcomer's first releases cannot reorder the long-run table. On the LiveBetting
charts an arm joins a market once its bets there span `BETTING_MIN_DAYS` (14);
`validate_months.py` applies the same rule to the raw bets. The run prints who
is held back and each arm appears by itself on a later refresh. The
agent-design card needs `MIN_SCORED_RELEASES` in its shared set; its rows are
published best first, because the page numbers them as a ranking, and a row can
be a succession of arms (the plain control: Sonnet 4.5 until 2026-09-04,
Sonnet 5 since), which its sub-label spells out.

Names are short, by the model the arm runs now plus its configuration: "Sonnet
4.5", "Sonnet 5", "Sonnet 5 w. tool", "Sonnet 5 w. multi-agent", "GPT-6 Astra",
... What "w. tool" (Anthropic's financial-services plug-ins, linked) and "w.
multi-agent" stand for, and that every agent runs at medium reasoning effort,
is said once in a static paragraph under the leaderboard in `docs/index.html`
(the JSON notes are rendered as plain text, so a link cannot live there, and it
has to show under the quarter tabs too). `SERIES_COLORS` in
`docs/assets/js/main.js` is keyed by display name -- rename there too, and give a
new arm a colour before it reaches the LiveBetting charts (an unlisted arm
draws grey). `validate_months.py` looks an arm up in this repo's names first and
falls back to the ones the betting run recorded, the same order `update_site.py`
uses, so a rename needs no betting re-run. `MODEL_NOTES` holds a remark shown
under an arm's name on the all-quarters board only; it marks the arms that no
longer run, by their last target month ("retired Aug 2026", "retired Jul
2026") -- a quarter tab needs none, a retired arm is simply absent from the tabs
after its last quarter. A model switch makes a new arm: the private pipeline
splits the two Claude Code agent arms at 2026-09-04, so "Sonnet 4.5 w. tool" /
"w. multi-agent" end there and "Sonnet 5 w. tool" / "w. multi-agent" start. The
agent-design card has one tab per model line (`AGENT_DESIGN_STUDIES`: Claude
Sonnet over its whole history, GPT-6 Astra on its own newest results); the
first tab is also written to the block's top level.
Qwen3-235B is one row: the private pipeline scores its export arm and its
OpenRouter arm as one model, so only the first id reaches the score tables.

`LIVEMACRO_SITE_EDITORIAL=<json>` pins an earlier publication's names, dropped
arms and who-is-shown lines at import; only the private pipeline's regression
sets it.

### Adding a figure

Add the filename to `FIGURES` in `update_site.py`. `check_release_safety.py`
imports that list, so the allowlist follows automatically — but only aggregate
figures that appear in the paper belong there.

## Editing content

Prose lives in `docs/index.html`; numbers live in `docs/data/leaderboard.json`.
Keeping that split is what makes the refresh a one-command operation, and what
lets the schema check be strict.
