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
cfbroot serve --sims 100000          # local app on 127.0.0.1:8000, quick to start
cfbroot export --out site --sims 50000   # a static build, small
cfbroot republish --out site             # the pages again, numbers as published
cfbroot guide --team "Texas Tech"     # a rooting guide in the terminal
cfbroot massey --top 25               # the model's ratings
cfbroot refresh                       # re-pull scores and ratings into the cache
python -m cfbroot.track report <dir>  # the model's record, from a clone of the tracking repo
```

Calibration modules print the numbers that are hard-coded in `config.py`, and are
how those numbers are justified. They are slow and hit ESPN:

```bash
python -m cfbroot.spread        # sigma and the rating-error curve
python -m cfbroot.carry         # carry_gain and carry_offset
python -m cfbroot.calibration   # home field, against closing lines
python -m cfbroot.backtest      # the model against real committee polls
python -m cfbroot.lean          # LEAN_SHARE and LEAN_SD, the committee's ranking carried in
python -m cfbroot.preseason     # AP_PRIOR_*, the preseason poll in a sample season's early rankings
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
top 12, at-large for the rest). A conference title game loss counts for a
quarter of a regular-season loss (`CCG_LOSS_SHARE` in `config.py`, Kai's
choice rather than a measured value): the loser's rating moves that share of
the way from the fit without the title game toward the fit with it, never
up, in the kernel, in `sample.py` and in `massey.rate_selection_day()` alike.

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
- **Payload size is deliberate.** `GAME_KEYS`, `SWING_KEYS`, `SWING_METRICS` and
  `_clean()` exist to keep a team's JSON near 150 KB gzipped. The bracket rounds
  are still simulated and in the league table, but no page switches the guide
  to them, so their per-game swings are left out. `_clean()` also turns NaN into
  null, and keeps a chance too small for six places at 1e-6 rather than zero;
  anything added to a payload must go through it.
- **Zero means never.** A chance that rounds to nothing shows as `<0.01%`. Only
  conference title and title game odds show `0.00%`, and only when no simulated
  season produced it (Kai's rule): `pct(x, d, zero)` and `pctOf()` in app.js,
  `_pct(x, digits, zero)` in pages.py.
- **Six wins is a measure like the rest** (`M_SIX`, every regular-season win
  counted), so the band, the guide and the record chart treat it as they
  treat the playoff. Kai named it for the wins rather than a bowl, since a
  five-win team can still get a bowl.
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
- **Live scores never touch the build.** The browser reads ESPN's scoreboard
  itself: every fifteen seconds while a game is on, every three minutes
  otherwise, paused in a hidden tab. `liveOf()` turns ESPN's home and away to
  match the build's, which can differ at a neutral site. The clock is built
  from ESPN's period and clock fields, not its summary line. A live game's win
  chance is the random-walk model of a score (Stern, 1994) on the build's own
  pregame chance and measured `sigma`, so at kickoff it equals the build's.
  "Odds with today's results" moves the build's number by each game finished
  since the build, its result against its pregame chance times its swing.
  Games still on do not count until they end (Kai asked for that). It adds
  games' effects, so it is an estimate, and the next build replaces it.
- **The front page and `/this-week/` go out after the team pages.** They rank
  the week's games by the playoff places each is expected to move, which is
  gathered from every team's guide as the loop writes it (`pages.Stakes`). Each
  simulated season fills the same twelve places, so the swings over all teams
  sum to zero and half their absolute sum is the share of the field that
  changes hands; times 2p(1-p), a lopsided game counts for less than a toss-up.
- **Clinching is worked out from the results, not simulated.** `web/clinch.py`
  plays the games left out against each conference's real tiebreakers, a port
  of `kernels._break_tie` that `tests/test_clinch.py` checks against the kernel.
  A team has clinched when it is in however they go and is out when it is in
  under none of them. A step the results cannot settle (the last rating step,
  the Big 12's total wins) could go any way, so no claim rests on it. A search
  past `MAX_NODES` makes no claim either, except that a week's scenario
  that runs out is asked again with `MAX_WEEK_NODES`, since a way to
  clinch left out is one a reader misses. `Race.path()` says what each final
  record takes, with the results spelled out once few enough games are in play
  (`MAX_HELP_GAMES`). "In if no more than one of these finishes 7-2 or better"
  is only the sure thing, so each row also says what happens past it
  (`Race._beyond`): tiebreakers can still put the team in, or it is out. Once
  few enough teams can reach a finish (`MAX_TIE_TEAMS`), `Race._ties` works out
  each tie by name instead: who is level, in or out, and on which step. The conference page draws it as a grid, with
  every record a team can finish with folded under its row (`_clinch_rows`, shared with the team's panel). A week that
  can settle a team is drawn as results to look for (`pages._recipe`): what every way needs said once, then a dashed
  box of the ones any one of which finishes it (Kai asked for the factoring). `Race.out_this_week()` is the same for
  a team the week can put out. It asks all of a week's outcomes, so it uses `_short_cover`, which only looks for
  conditions of four results or fewer; `_cover` took over a minute on a nine-game Big Ten week. A team's page
  writes it into the written section, and `placeClinch()` in app.js moves it up
  beside the team's season; a team that is in, is out or has no race gets no
  panel, and only a clinched team gets a question. `state.json` carries
  `conf_status` for the team line; a republish works it out again. Army-Navy is
  in ESPN's data as a conference game, and `counts_in_standings()` takes it out,
  in the simulation too.
- **The sitemap is read off the disk.** `export.page_urls()` lists every
  `index.html` the build wrote, at the end of `write_front()`, which the full
  build and the republish both run. A new kind of page needs no sitemap edit,
  and one the build skipped is not listed. The methodology carries no
  `lastmod`, since its text does not change with each build.
- **Every page has the strip of links under the masthead.** `pages.subnav()`
  fills `<!--SUBNAV-->` through `load_template()` in the build and `_shell()` in
  the local app, so a new conference appears without editing the template.
- **What a team needs comes from three tallies.** Its own games' two results
  are already in its guide. The kernel also keeps every measure by
  regular-season win total (`h_rec`), and `run_league()` splits the seasons by
  the team's title game (won, lost, not in it) and counts each conference's
  title game pairings. A team's JSON carries `by_record` and `title_game`;
  `state.json` carries `ccg_pairs` and `projection`, which is what lets a
  republish write the conference and ranking pages again. The game named as
  deciding the most is the one that spreads the odds furthest, the gap
  between its results times the square root of p(1-p), so a contender's is a
  hard game it has to win rather than the easy one whose upset would hurt
  most; the square root keeps a coin flip that barely moves the odds from
  outranking it (Kai found 2p(1-p) leaned on likelihood too much). A team opens on the
  race it is in (`fitMetric()` in app.js), not on a measure stuck at 0 or 100.
- **The projected CFP ranking is not simulated.** `committee_now()` rates the
  real results through the last finished week with the same weekly system and
  committee model a sample season uses, with no committee noise, so it is one
  fixed answer. Its "if the season ended today" field gives the automatic
  bids to the current conference leaders. Once the committee publishes, the
  rankings page shows its top 25 instead (`state.polls["cfp"]`), and the
  field is picked from that order, the model's below it. That lasts until a
  week of games has finished after it. From then until the next ranking the
  page is that next ranking as the model expects it, with each team's move
  from the last one (Kai asked for the switch, with as little text as
  possible); `projection["since"]` is what says so, and a republish copies it.
  It goes on past the title games: once every one is played the projection
  is the last ranking (`final`), with champions for leaders, until the
  committee publishes it. `lean.seen_week()` is the last finished week for
  all of this, since `current_week()` jumps to Army-Navy's once the regular
  season ends.
- **The simulation reads the committee's ranking.** `lean.py` turns the
  latest one into a gap per team, the rating of the team the model has at
  the committee's place less the team's own, and a share of it
  (`LEAN_SHARE`, by rankings still to come) is added to the team's rating on
  selection day in the kernel, in a sample season and in `committee_now()`.
  The committee noise becomes `LEAN_SD`, which is higher than `COMMITTEE_SD`
  because what is left after the carry is chance. A ranking numbered past the
  week being played has seen games the state has not, so the run that values
  last week's games takes the ranking before it (`state.cfp_polls` holds the
  last two). `backtest` empties `cfp_polls`, since it checks the model alone.
  A new ranking needs a full build; a republish only copies the projection.
  Championship week is its own case (`Lean.holds`): a team in no title game
  keeps the rating it was last ranked on plus its whole gap, and only the
  teams in a title game move. Once the last ranking is out (`Lean.final`) its
  order stands in every simulated season, so the field is the real one.
- **Sunday has a build after the AP poll, Tuesday one after the committee's.**
  The AP's is shown and never used; it lands on Sunday afternoon, so
  `pages.yml` runs at 5pm and 6pm Central on Sundays to put it on the site.
  The loader looks for the newest poll from one week past the current one,
  since the poll after a week's games carries the next week's number.
- **Titles and questions are worded as the searches that reach the site are.**
  Search Console on 2026-11-10 had "[team] playoff odds" at position 40 to 65,
  behind the sportsbooks, and "[team] chances of making playoffs", "can X make
  the playoffs" and tiebreaker searches at 6 to 9. So a team's title opens on
  "playoff chances", a conference's on "championship game scenarios", and the
  questions include bowl eligibility and national championship odds. The week
  in a title comes from the state at build time, like every number.
- **Google Analytics is off until a build is given an id.** `CFBROOT_GA_ID`,
  from the repository variable `GA_ID`, puts the tag in every head and lets
  Google's origins through the Content-Security-Policy; without it neither
  is written. Cookies are denied by default in the EU, the UK and Switzerland
  (`GA_NO_COOKIES`), which is why there is no consent banner. `/privacy/` says
  what is collected and mentions Google only while the tag is on.
- **The model's record is private.** Each full build runs `cfbroot.track` into
  a private repository (`TRACK_TOKEN` secret, `TRACK_REPO` variable, default
  `goodsellkai/cfbroot-tracking`): every game's last chance before kickoff,
  its score, and each build's odds table. Nothing on the site reads or links
  it, and Kai asked that it stay that way. Without the token the build skips it.
- **`pages.py` only reads eight things off a season** and nine off a team, which
  is what makes the republish possible. `tests/test_republish.py` pins that list.
  Reading something new there means adding it to `republish.Season` too, or the
  republished page quietly loses a sentence.

## Conventions

- **Commits**: author Kai Goodsell <goodsellkai@gmail.com>, and no Claude
  co-author trailer. Push with
  `git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push`.
- **Writing**: plain and short, in READMEs, comments, commit messages and UI
  text. No em dashes, no "comprehensive", no "seamlessly", no stock AI phrasing.
  Comments explain why, not what, and never over-explain. Site text is direct
  and a little formal, without hedges ("the games that move the odds most",
  not "by more than the simulations can put down to chance"), and it does not
  describe what the reader can already see on the page.
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
  the side to root for, with the other side under it marked @ or vs.
  Keep it unless Kai asks for a redesign. Several installed design skills will
  offer to replace it.
- **Data pages show charts, not sentences** (Kai's call, 2026-10-27). The
  site green (`--chart`) is for amounts and `--up`/`--down` for a game's two
  results. The band is one bar chart of the measures, its labels drawn as
  buttons since each bar switches the guide; a team's games are dumbbells on a
  shared scale, zoomed to the stretch the results sit in; every final record
  the team reaches gets a column, its odds above the line and how often below,
  so even losing out has a number (six wins, which the record settles, and a
  chance under 0.01% get how often alone); the slate is a tornado, each side's change
  from a centre line every game shares. A heading can be a sentence; a number
  the chart already shows should not be repeated in words.
