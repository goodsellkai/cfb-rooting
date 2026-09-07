# cfbroot — who should I root for this week?

Pick your college football team. The app simulates the rest of the season a few
million times, slices those simulations by the result of every other game on the
slate, and tells you which outcomes actually help you — with confidence
intervals, so you can tell a real rooting interest from Monte Carlo noise.

```
Alabama | 2026 | week 2
Make the playoff: 42.6%  95% CI 42.49% to 42.68%   expected wins 8.65

Your game                          Win%  Root for       Swing pp          95% CI
Alabama at Kentucky                 22%  Alabama          -24.74  -24.95, -24.54

Who to root for (FDR-controlled at q<=0.05 across 344 tests)
Matchup                            Win%  Root for       Swing pp          95% CI
Oregon at Oklahoma State            11%  Oklahoma St       +2.21    +1.90, +2.52
Penn State at Temple                11%  Temple            +2.09    +1.77, +2.40
Louisiana Tech at LSU               98%  Louisiana Tech    -2.08    -2.81, -1.35
Arizona State at Texas A&M          88%  Arizona State     -1.64    -1.93, -1.34
```

Root against your own conference's contenders (LSU, Texas A&M, Georgia) and
against the other leagues' best teams (Oregon, Penn State) — which is what a
fan would guess, but now with a number and an error bar on each one.

## Setup

> **Windows / PowerShell note.** Windows PowerShell 5.1 has no `&&` operator,
> and a quoted path at the start of a line is parsed as a string rather than a
> command. Run one command per line, and put `&` in front of any quoted
> executable path:
>
> ```powershell
> & "C:\Program Files\GitHub CLI\gh.exe" --version
> ```

```powershell
cd cfb-rooting
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Get a free API key at <https://collegefootballdata.com/key>. Create a file
named exactly `.env` next to `pyproject.toml` containing:

```
CFBD_API_KEY=your_key_here
```

Two things go wrong here on Windows: Notepad silently saves it as `.env.txt`
(turn on "File name extensions" in Explorer's View tab), and the `CFBD_API_KEY=`
prefix is required — a file holding only the bare key will not work. Check it:

```powershell
.venv\Scripts\cfbroot check
```

Without a key the app still runs, on a clearly-labelled fabricated season, so
you can see what it does before signing up.

## Use it

Double-click **`Start cfbroot.bat`**. It launches the server and opens your
browser at <http://127.0.0.1:8000>. Keep that console window open while you use
the app — closing it stops the server.

Or from a terminal:

```powershell
.venv\Scripts\cfbroot serve
```

Type your team, hit Run. **Refresh data** re-pulls scores and re-simulates —
that is the button to press after a day of games.

Or stay in the terminal:

```powershell
.venv\Scripts\cfbroot guide --team Michigan --metric make_playoff --sims 1000000
.venv\Scripts\cfbroot guide --team Michigan --metric win_conference --all-weeks
.venv\Scripts\cfbroot --live refresh
```

Global flags (`--live`, `--force`, `--year`, `--demo`) go **before** the
subcommand; `cfbroot refresh --live` is a parse error.

All nine measures of success are computed from the **same** simulation run, so
switching between them in the UI is an instant re-render, not another
simulation. Success can be measured by any of: making the playoff, winning the conference,
reaching the conference title game, earning a top-4 seed and first-round bye,
reaching the quarterfinal / semifinal / title game, winning the national
championship, or finishing the regular season undefeated. Expected win total is
always reported alongside.

## How it works

**Ratings.** FPI, pulled straight from **ESPN's own power-index endpoint**,
falling back to CollegeFootballData's FPI mirror and then to SP+. FPI is a
points-above-average rating, so a rating gap is already on the scale of a point
spread.

ESPN is the primary source because FPI is ESPN's metric and CFBD's mirror lags:
in the 2026 opener CFBD was still serving preseason numbers days after ESPN had
updated, a mean gap of 1.8 points and a maximum of 8.9 across the 138 FBS teams.
CollegeFootballData uses ESPN's team ids, so ratings join to teams on an exact
integer key rather than by fuzzy school-name matching. The header shows how
stale the ratings are, and **Refresh data** re-pulls them.

**Game model.** Projected margin is `slope × (rating gap) + home-field`, and the
win probability is the normal CDF of that margin over `sigma`. Rather than
trusting the defaults, `cfbroot` re-fits `slope`, `home-field` and `sigma` by
least squares on completed games, shrinking toward the priors with weight
`n / (n + 150)` — so week 2 leans on the prior and November leans on the data.
It reports its own Brier score and log loss.

Only **FBS-vs-FBS** games are used for that fit. Roughly half of a season's
early results are an FBS team hosting a non-FBS opponent, always at home, and
that opponent's rating is an assumption (−32 by default) rather than a
measurement — so any error in it gets absorbed by the home-field term, which is
the parameter being estimated. Including those games inflated home-field from
about 3 points to 8.

**A simulated season.** Draw a winner for every unplayed game; accumulate
overall and conference records; order each conference and play the title games;
score and rank every team; select and seed the 12-team playoff; play the
bracket. All of it runs in a numba-compiled kernel across every core — about
**50,000 complete seasons per second** on 16 threads, so 2.5 million seasons
takes around 15 seconds.

**The committee proxy.** Playoff selection is a human vote, so it is modelled,
not reproduced:

```
score = rating + k_resume × (wins − elite_expected_wins) + k_champ × champion
```

`elite_expected_wins` is how many games a reference playoff-caliber team would
win against that exact schedule — a strength-of-record term, so a team is
rewarded for beating a hard schedule and punished for losing to a soft one.

That term is **shrunk 70% toward the FBS average** (`resume_shrink = 0.3`).
Undamped, it is a pure strength-of-record measure, and it hands a team with a
brutal schedule so much credit that losses stop mattering — at full weight, the
top-rated team in the country made the field ~100% of the time *even at 7-5*.
The committee clearly weighs schedule, but it has never taken a four-loss
at-large team, and the shrink is what reconciles those two facts.

The weights were tuned so that P(at-large bid | record) matches the 12-team era.
For a power-conference team that does *not* win its league:

| Record | Modelled at-large odds |
|--------|------------------------|
| 10-2   | 85–100%                |
| 9-3    | 30–90% (depends on who) |
| 8-4    | 0–3%                   |

A test pins this shape, so retuning cannot silently regress it. All of it lives
in `ModelParams` and is tunable.

**The playoff.** The 2026 format: champions of the ACC, Big Ten, Big 12 and SEC
are guaranteed bids, plus the highest-ranked champion from any other conference,
plus seven at-large. Straight seeding 1–12, byes for the top four, no reseeding.
First round at the higher seed's campus, everything after at neutral sites.

**Rooting interests.** This is the part that makes the whole thing affordable.
Because every unplayed game is drawn independently, *slicing one simulation set*
by a game's outcome gives an unbiased conditional distribution — no game needs
its own re-simulation. The two slices also share every other game's random
draws (common random numbers), so their difference is estimated far more
precisely than two independent runs would manage.

**Statistics.** Every reported number is an estimate, and is treated as one:

- probabilities carry **Wilson score intervals**, which stay sane near 0 and 1
  where a Wald interval falls apart;
- each swing is a difference of two binomial proportions with a **Newcombe
  score interval**, which holds its coverage even when a heavy favourite leaves
  only a sliver of simulations in the upset branch;
- a week's slate is dozens of simultaneous tests, so significance is
  **Benjamini–Hochberg FDR-controlled** at q ≤ 0.05 — without it, chance alone
  would decorate a few games as "significant" every single week. The correction
  is applied *within* each metric, so one metric's q-values do not depend on how
  many other metrics happened to be computed;
- a side is named for **every** game, because the point estimate always favours
  one of them and withholding that is not more honest than grading it. Each call
  carries a confidence: **clear** (survived FDR control), **leaning** (the
  interval includes zero, so the direction is a best guess), or **thin** (one
  result is so unlikely that too few simulations landed there to say);
- the guide states its own resolution: at 250,000 sims, swings below roughly
  0.5 percentage points cannot be separated from noise. Resolving 1 point takes
  about 75,000 sims; resolving 0.2 points takes about 2 million. That is why
  the sim count needs to be large.

## What it doesn't do

- The committee proxy is a model of a human vote, not the vote itself. It has no
  notion of injuries, eye tests, or a bad November loss weighing more than a bad
  September one.
- Conference tiebreakers use win percentage, then head-to-head among the tied
  group, then the committee score. Real tiebreakers run several rules deeper
  (common opponents, records against the top of the standings). The final
  fallback is realistic — the Big 12 and Big Ten both end up at "highest ranked"
  — but multi-team ties will occasionally resolve differently than they would
  in reality.
- Ratings are frozen at their current values; the model does not let a team
  improve or decline over a simulated season.
- Conference title games are always simulated at a neutral site. Several
  leagues (Sun Belt, C-USA, American, Mountain West) actually host them at the
  higher seed's stadium, which is worth ~3 points to that team.
- Bowls and actual playoff results are not ingested; the bracket is always
  simulated from the projected field.
- **Ratings are treated as known exactly.** ESPN publishes its own FPI-based
  playoff odds, shown beside ours in the league table, and ours run high at the
  top of the board (Ohio State 91% vs ESPN's 78%, Penn State 61% vs 31%). The
  ordering agrees; the confidence does not. The likely cause is that this model
  freezes each rating for the whole season, while a team's true strength is
  uncertain and drifts. Drawing each team's rating per simulated season from a
  distribution around its point estimate would widen every outcome and pull
  these numbers toward the middle. Not implemented yet.
- FPI already embeds a home-field adjustment, and the calibration re-fits home
  field on top of it, so the two are not cleanly separable.

## Layout

```
src/cfbroot/
  config.py            tunable model parameters, metric definitions
  model.py             rating → win probability, and its calibration
  stats.py             Wilson, Newcombe, Benjamini–Hochberg, power
  data/
    cfbd_source.py     CFBD + ESPN fetchers
    cache.py           TTL disk cache (10 min on game days)
    season.py          payloads → flat arrays
    synthetic.py       fabricated season for tests and the no-key demo
    loader.py          one entry point for a ready-to-simulate season
  sim/
    kernels.py         the numba kernel: one season, many times
    engine.py          batching and accumulation
    leverage.py        conditional analysis → ranked guide
  web/app.py           FastAPI + single-page frontend
  cli.py
tests/                 74 tests, no network required
```

## Tests

```powershell
.venv\Scripts\python -m pytest
```

Structural invariants are asserted exactly, not approximately: across every
simulation there are always exactly 12 playoff teams, 4 byes, 8 quarterfinalists
and 1 champion. Head-to-head tiebreakers are tested against a rigged conference
where the tiebreak loser has a far better rating. The conditional analysis is
checked against the law of total probability, and interval coverage is verified
empirically.
