"""Rebuild the site from what is already published, without simulating.

Changing a stylesheet, a template or a header does not move a single number,
but a normal build spends an hour working the numbers out again. This reads the
live site instead. `data/state.json` carries the season and every finished
game; each team's JSON carries its guide and, in its `league` table, every
team's odds. Between them that is everything `pages.py` reads, so the pages can
be written again from current code while the numbers are copied through
untouched.

Use it for anything that is not a model change. The numbers it publishes are
exactly the ones already live, which is the point: a republish never has to be
timed against a game.

One caveat. Published numbers are rounded to six decimals, so a figure sitting
on a display boundary can land a hundredth of a percentage point either way of
what a fresh build would have shown. It shows up only among teams with no real
playoff chance.
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .app import HERE
from . import cards, pages
from .export import load_template, render, write_front, write_meta

# The published site is the input, so a republish of a site that has never
# been built has nothing to read. 16 at a time keeps the whole data tree
# inside a minute without leaning on the edge.
WORKERS = 16
TIMEOUT = 60


@dataclass
class Team:
    """What `pages.py` asks of a team, and nothing more."""

    idx: int
    school: str
    conference: str | None
    rating: float
    logo: str
    color: str | None
    is_fbs: bool
    abbreviation: str = ""
    # The writeup puts the model's playoff number next to ESPN's. A real
    # SeasonState gets this from FPI; here it comes out of the league table.
    espn_odds: dict | None = None


class Season:
    """A stand-in for `SeasonState`, built from the published state file.

    `pages.py` reads seven things off a season: the year, the current week,
    the default week, every team, the FBS teams, the games and the polls.
    Everything else a real
    `SeasonState` carries exists to run the simulation, which is the part
    being skipped.
    """

    def __init__(self, payload: dict):
        self._payload = payload
        self.year = payload["year"]
        self._week = payload["current_week"]
        self._default_week = payload.get("default_week")

        index = payload["team_index"]
        self.teams = [
            Team(idx=int(i), school=t["name"], conference=t["conference"],
                 rating=t["rating"], logo=t["logo"] or "", color=t["color"],
                 is_fbs=bool(t["fbs"]), abbreviation=t.get("abbr") or "")
            for i, t in sorted(index.items(), key=lambda kv: int(kv[0]))
        ]
        by_idx = {t.idx: t for t in self.teams}
        self.fbs_teams = sorted((t for t in self.teams if t.is_fbs),
                                key=lambda t: t.school)

        # `record()` wants the shape the simulator uses, not the shape the
        # page is served, so the published rows are translated back.
        self.games = [
            {"week": g["week"], "completed": True, "is_ccg": g["title_game"],
             "home_idx": g["home"], "away_idx": g["away"],
             "home": by_idx[g["home"]].school, "away": by_idx[g["away"]].school,
             "home_points": g["home_points"], "away_points": g["away_points"],
             "neutral": g["neutral"], "conference_game": g["conference"]}
            for g in payload["played"]
        ]

        # The share cards put the team's poll rank next to its record.
        self.polls = {k: {int(i): r for i, r in v.items()}
                      for k, v in (payload.get("polls") or {}).items()}

    def current_week(self) -> int:
        return self._week

    def default_week(self):
        return self._default_week


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "cfbroot-republish"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def fetch_data(base: str, into: Path, log=print) -> int:
    """Pull the published `data/` tree down whole.

    Every file has to be on disk even though only the pages are being
    rewritten: a Pages deploy serves the directory it is given, so anything
    missing from it stops being served.
    """
    base = base.rstrip("/")
    into.mkdir(parents=True, exist_ok=True)
    (into / "team").mkdir(exist_ok=True)
    (into / "season" / "fresh").mkdir(parents=True, exist_ok=True)

    state = json.loads(_get(f"{base}/data/state.json"))
    (into / "state.json").write_bytes(
        json.dumps(state, separators=(",", ":")).encode("utf-8"))

    slugs = [t["slug"] for t in state["teams"]]
    n = int(state.get("sample_seasons") or 0)
    jobs = [(f"{base}/data/team/{s}.json", into / "team" / f"{s}.json")
            for s in slugs]
    jobs += [(f"{base}/data/season/{i}.json", into / "season" / f"{i}.json")
             for i in range(n)]
    jobs += [(f"{base}/data/season/fresh/{i}.json",
              into / "season" / "fresh" / f"{i}.json") for i in range(n)]

    size = len((into / "state.json").read_bytes())
    done = 0

    def one(job):
        url, dest = job
        dest.write_bytes(_get(url))
        return dest.stat().st_size

    with ThreadPoolExecutor(WORKERS) as pool:
        for got in pool.map(one, jobs):
            size += got
            done += 1
            if done % 200 == 0 or done == len(jobs):
                log(f"  fetched {done} / {len(jobs)} files, {size / 1e6:.0f} MB")
    return size


def republish(out: Path, source: str, log=print) -> None:
    """Write the site again from the published numbers."""
    t0 = time.perf_counter()
    log(f"reading {source}")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    size = fetch_data(source, out / "data", log=log)
    state = Season(json.loads((out / "data" / "state.json").read_text("utf-8")))
    log(f"{state.year} week {state.current_week()}: "
        f"{len(state.fbs_teams)} teams, {len(state.games)} games played")

    teams = state.fbs_teams
    first = json.loads(
        (out / "data" / "team" / f"{pages.slug(teams[0].school)}.json")
        .read_text("utf-8"))
    n_sims = int(first["n_sims"])
    # Every team's file carries the whole league's table, so the standings the
    # home page ranks by, and ESPN's numbers the writeups quote, both come out
    # of the first file read.
    standing = sorted(((r["team"], r["p"]["make_playoff"])
                       for r in first["league"]),
                      key=lambda r: (-r[1], r[0]))
    espn = {r["team"]: r.get("espn") for r in first["league"]}
    for t in state.teams:
        t.espn_odds = espn.get(t.school)

    shutil.copytree(HERE / "static", out / "static")
    template = load_template(str(int(time.time())))
    shares = cards.Cards(out, state, log)
    write_front(out, state, teams, n_sims, standing, template,
                shares.site(state.current_week(), standing))
    write_meta(out, state, teams, n_sims, template)

    week = state.default_week() or state.current_week()
    for i, t in enumerate(teams, 1):
        guide = json.loads(
            (out / "data" / "team" / f"{pages.slug(t.school)}.json")
            .read_text("utf-8"))
        team_dir = out / "team" / pages.slug(t.school)
        team_dir.mkdir(parents=True)
        (team_dir / "index.html").write_text(
            render(template, pages.team_head(state, t, guide, week,
                                             shares.team(state, t, guide, week)),
                   pages.team_body(state, t, guide, week), 2, t.school,
                   writeup=pages.team_writeup(state, t, guide, week),
                   color=t.color, photo=t.idx % 4 + 1),
            encoding="utf-8")
        if i % 25 == 0 or i == len(teams):
            log(f"  wrote {i} / {len(teams)} teams")

    log(f"{out}: {size / 1e6:.0f} MB in {time.perf_counter() - t0:.0f}s, "
        f"numbers unchanged from {source}")
