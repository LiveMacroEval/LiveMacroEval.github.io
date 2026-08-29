# LiveMacroEval — project website

Source for **https://livemacroeval.github.io/**, the site for the LiveMacroEval
benchmark: a live, contamination-resistant evaluation of LLM agents on
nowcasting sixteen U.S. headline macroeconomic indicators.

- **Code and data release:** https://github.com/LiveMacroEval/LiveMacroEval
- **Leaderboard:** refreshed every two weeks

## Layout

| | |
|---|---|
| `docs/` | the published site — GitHub Pages serves this folder, and nothing else |
| `tools/` | build and release-safety machinery; never published |

`docs/` is deliberately minimal: one HTML page, one stylesheet, one script, the
aggregate figures from the paper, and a single `leaderboard.json` holding every
number the site displays. Several of the pipeline's inputs are licensed and
cannot be redistributed, so `tools/check_release_safety.py` enforces an
allowlist over `docs/` on every commit and every push. See
[tools/README.md](tools/README.md) for how that works and how to run the
biweekly refresh.

## Citation

See `CITATION.cff` in the
[code repo](https://github.com/LiveMacroEval/LiveMacroEval).
