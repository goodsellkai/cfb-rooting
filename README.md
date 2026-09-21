# cfbroot

Simulates the rest of the college football season and shows which games this week help your team.

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Games, teams, FPI and polls come from ESPN, which needs no key. A
CollegeFootballData key is optional and only used if ESPN is down. To add one,
get it free at https://collegefootballdata.com/key and put it in a file named
`.env` in this folder:

```
CFBD_API_KEY=your_key_here
```

## Run

Double-click `Start cfbroot.bat`, or run:

```
.venv\Scripts\cfbroot serve
```

Then open http://127.0.0.1:8000.

It simulates a million seasons as it starts. Every simulated season is rated
with the full Massey model and put through the committee rules, so this takes
a while, around a quarter of an hour on a 16-core machine. That one run answers
for every team, so picking a team after that is instant. Restart it to pull new
scores.

The Sample season tab plays out one simulated season week by week: every
score, the standings, the title games, Selection Sunday and the bracket, with
running stats. The committee ranks the teams every week, leaning on the preseason AP poll
for the first six weeks while there is little else to go on, so the top 25
and the numbers beside each team move as the season goes. Each click simulates a brand new season. It is only in the
local app; the hosted version has the rooting guide alone.

## Hosted version

`cfbroot export` simulates the season and writes the whole app as static files
to `site/`, so it can be hosted anywhere with no server. The Publish site
workflow does this on a schedule and puts it on GitHub Pages. It needs Pages
set to deploy from GitHub Actions. A repository secret named `CFBD_API_KEY` is
optional, as a fallback if ESPN is down.

From the command line:

```
.venv\Scripts\cfbroot guide --team Michigan
```

Massey ratings for the current season:

```
.venv\Scripts\cfbroot massey
```

## Tests

```
.venv\Scripts\python -m pytest
```
