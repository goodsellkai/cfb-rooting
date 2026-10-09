# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Monte Carlo college football playoff simulator. It plays the rest of the season
2.5 million times and tells a fan which games to root for, published as a static
site at https://cfbroot.com. There is no server at runtime: a GitHub Actions build
simulates the season, writes HTML and JSON, and uploads it to Cloudflare Pages.

## Commands

```bash
pip install -e .                      # the package, editable
pytest -q                             # the whole suite, about a minute
pytest tests/test_selection.py -q     # one file
pytest -q -k tiebreak                 # one pattern
cfbroot serve                         # local app on 127.0.0.1:8000
cfbroot export --out site --sims 50000   # a static build, small
cfbroot republish --out site             # the pages again, numbers as published
cfbroot guide --team "Texas Tech"     # a rooting guide in the terminal
cfbroot massey --top 25               # the model's ratings
cfbroot refresh                       # re-pull scores and ratings into the cache
```

Calibration modules print the numbers that are hard-coded in `config.py`, and are
how those numbers are justified. They are slow and hit ESPN:

```bash
python -m cfbroot.spread        # sigma and the rating-error curve
python -m cfbroot.carry         # carry_gain and carry_offset
python -m cfbroot.calibration   # home field, against closing lines
python -m cfbroot.backtest      # the model against real committee polls
```

Environment: `CFBROOT_SIMS`, `CFBROOT_REPLAY_SIMS`, `CFBROOT_SAMPLES`,
`CFBROOT_SITE_URL` steer a build. `CFBD_API_KEY` in `.env` is only a fallback
source; ESPN is primary and needs no key.

## Architecture

Data in, simulations in the middle, a static site out.

**`data/`** turns ESPN's scoreboard and power index into a `SeasonState`: teams,
conferences, games, ratings. `loader.py` is the entry point and the only place
that fetches. `season.py` builds the state and holds `kernel_inputs()`, which
flattens everything into numpy arrays once so the kernel never touches Python
objects. `conference_rules.py` encodes each league's real tiebreakers as step
codes. `cache.py` keeps responses on disk and falls back to a stale copy rather
than failing a build.

**`sim/`** is the engine. `kernels.py` is numba, compiled once and cached, and
runs every simulated season in parallel: play the games, rate the teams, order
the conferences, fill the bracket. `engine.py` wraps it and returns
`LeagueResults`, one simulation that serves every team. `leverage.py` slices that
per team and turns it into a rooting guide, with Wilson intervals, Newcombe
differences and a false discovery rate correction over the whole slate.
`sample.py` replays a single season in full for the Sim a season tab.

**Rating and selection.** `massey.py` fits a probit power rating plus a Bayesian
win-loss correction to each simulated season, the same way the real one would be
rated. `selection.py` models the committee on top of that rating: its own noise,
best-win and worst-loss boosts, a head-to-head rule, a title-game jump, then the
2026 field rules (four power champions, the best other champion, Notre Dame if
top 12, at-large for the rest).

**`web/`** has two faces that share everything underneath. `app.py` is a FastAPI
dev server. `export.py` writes the same thing as files, and is what runs in CI.
`pages.py` builds every page's head and body as strings, including the written
section that stays on screen after the app loads and is what crawlers read.
`static/app.js` and `static/season.js` are vanilla JavaScript, no build step.

### Things worth knowing before changing them

- **One simulation serves every team.** Conditioning on a game's result is a
  slice of counts already collected, not a second run, which is why a guide is
  instant and why `cond_counts` is shaped `(games, metrics, teams)`.
- **Ratings are carried forward between FPI updates.** FPI publishes daily, so a
  Saturday result is not in it until Sunday. `carry_ratings()` moves both teams
  by the share of the surprise FPI itself would move them. Without it a team that
  just won is simulated at the strength it had on Friday.
- **A second, smaller run values games already played.** `before_week()` sets the
  latest week's results aside and `run_replay()` simulates from there, so a
  finished game gets the same two numbers an upcoming one has. Older weeks are
  not replayed; with Include played on, the page shows their scores only.
- **The kernel is compiled and cached by source hash.** Editing anything under
  `sim/` invalidates it and costs a minute and a half on the next run.
- **Every published number passes a guard.** `export.py` refuses to publish if the
  playoff probabilities do not add up to the field size.
- **Payload size is deliberate.** `GAME_KEYS`, `SWING_KEYS` and `_clean()` exist
  to keep a team's JSON near 150 KB gzipped. `_clean()` also turns NaN into null;
  anything added to a payload must go through it.
- **Data files are named for the team, not its index.** Indices only mean
  something within one build, and a page open across a rebuild would ask for a
  file that no longer exists.
- **A template or stylesheet change does not need a simulation.** `republish.py`
  reads the live site instead: `data/state.json` carries the season and every
  finished game, and any team's file carries the whole league's table, which is
  everything `pages.py` reads. It writes the pages again from current code and
  copies the numbers through. Twenty seconds against seventy-five minutes, and
  the `Republish site` workflow is manual only. Two things to respect: it shares
  the `pages` concurrency group with the full build, because reading the data
  tree mid-deploy would mix two builds; and published numbers are rounded to six
  decimals, so a figure on a display boundary can land a hundredth of a point
  either way of a fresh build. A model change still goes through `export`.
- **`pages.py` only reads seven things off a season** and eight off a team, which
  is what makes the republish possible. `tests/test_republish.py` pins that list.
  Reading something new there means adding it to `republish.Season` too, or the
  republished page quietly loses a sentence.

## Conventions

- **Commits**: author Kai Goodsell <goodsellkai@gmail.com>, and no Claude
  co-author trailer. Push with
  `git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push`.
- **Writing**: plain and short, in READMEs, comments, commit messages and UI
  text. No em dashes, no "comprehensive", no "seamlessly", no stock AI phrasing.
  Comments explain why, not what, and never over-explain.
- **Tests and checks** run with a few thousand seasons, not millions. Do not
  benchmark unless asked.
- **Dev servers** go through `preview_start` and `.claude/launch.json`, never a
  backgrounded Bash process.
- **Model parameters** live in `config.py` and are measured, not guessed. Each
  one names the module that produces it. Changing one means re-running that
  module and reporting what moved.
- **The site already has a look**: the picked team's colour across the band,
  with one of the header photos greyed and blended into it; one self-hosted
  typeface (Archivo, condensed for numbers, normal for prose); and yellow, the
  first-down line, used only for the side to root for and the measure chosen
  (the logo is a yellow ball on the turf green). Each game is one row led by
  the side to root for; the biggest swings open with both sides, the rest
  stay one line.
  Keep it unless Kai asks for a redesign. Several installed design skills will
  offer to replace it.
