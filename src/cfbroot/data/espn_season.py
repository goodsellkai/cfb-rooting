"""Teams, conferences and games from ESPN's public scoreboard.

CollegeFootballData meters its API at 1,000 calls a month, which a site
rebuilt hourly runs through. ESPN has the same games, does not meter them, and
uses the same team ids, so this hands back the shapes CFBDSource does and
nothing downstream can tell the difference.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import cache

SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/"
              "college-football/scoreboard?dates={year}&week={week}"
              "&seasontype=2&groups={group}&limit=500")
FBS, FCS = 80, 81
WEEKS = range(1, 18)
# The site API refuses browser-like user agents and accepts plain ones.
_HEADERS = {"User-Agent": "curl/8.9.1"}

# ESPN's conference ids for FBS, under the names CFBD uses for them.
FBS_CONFERENCES = {
    1: ("ACC", "ACC"), 4: ("Big 12", "B12"), 5: ("Big Ten", "B1G"),
    8: ("SEC", "SEC"), 9: ("Pac-12", "PAC"), 12: ("Conference USA", "CUSA"),
    15: ("Mid-American", "MAC"), 17: ("Mountain West", "MWC"),
    18: ("FBS Independents", "IND"), 37: ("Sun Belt", "SBC"),
    151: ("American Athletic", "AAC"),
}

# How long a week's games are kept before asking again. A week that is over
# does not change; one still being played does.
TTL_FINISHED = 7 * 86400
TTL_LIVE = 10 * 60
TTL_IDLE = 3 * 3600


class ESPNSeasonError(RuntimeError):
    pass


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ESPNSeasonError(f"ESPN scoreboard request failed: {exc}") from exc


def _events(year: int, week: int, group: int, *, live: bool, force: bool) -> list:
    key = {"year": year, "week": week, "group": group}

    def go():
        return _get(SCOREBOARD.format(year=year, week=week, group=group)).get(
            "events") or []

    events = cache.get_or_fetch("espn_scoreboard", key, TTL_FINISHED, go,
                                force=force)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    unsettled = any(
        not e["competitions"][0]["status"]["type"].get("completed")
        and (e.get("date") or "") < now
        for e in events)
    upcoming = any(not e["competitions"][0]["status"]["type"].get("completed")
                   for e in events)
    if unsettled or upcoming:
        # Something here can still change, so the long copy will not do.
        events = cache.get_or_fetch("espn_scoreboard", key,
                                    TTL_LIVE if live else TTL_IDLE, go)
    return events


def _game(e: dict, week: int) -> dict:
    c = e["competitions"][0]
    side = {x["homeAway"]: x for x in c["competitors"]}
    done = bool(c["status"]["type"].get("completed"))

    def team(s):
        t = side[s]["team"]
        conf = FBS_CONFERENCES.get(int(t.get("conferenceId") or -1))
        return int(t["id"]), t.get("location") or t.get("displayName"), (
            conf[0] if conf else None)

    def points(s):
        v = side[s].get("score")
        return int(v) if done and v not in (None, "") else None

    hid, hname, hconf = team("home")
    aid, aname, aconf = team("away")
    notes = [n.get("headline") for n in c.get("notes") or [] if n.get("headline")]
    return {
        "id": int(e["id"]), "week": week, "season_type": "regular",
        "start_date": e.get("date"),
        "home_id": hid, "home_team": hname, "home_conference": hconf,
        "away_id": aid, "away_team": aname, "away_conference": aconf,
        "home_points": points("home"), "away_points": points("away"),
        "completed": done, "neutral_site": bool(c.get("neutralSite")),
        "conference_game": bool(c.get("conferenceCompetition")),
        "notes": notes[0] if notes else "",
    }


def season(year: int, *, live: bool = False, force: bool = False) -> dict:
    """``{"teams", "conferences", "games", "fcs_games"}`` in CFBD's shapes."""
    jobs = [(w, g) for g in (FBS, FCS) for w in WEEKS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda job: (job, _events(year, job[0], job[1], live=live, force=force)),
            jobs))

    games, fcs_games, teams = [], [], {}
    for (week, group), events in results:
        for e in events:
            g = _game(e, week)
            (games if group == FBS else fcs_games).append(g)
            if group != FBS:
                continue
            for x in e["competitions"][0]["competitors"]:
                t = x["team"]
                conf = FBS_CONFERENCES.get(int(t.get("conferenceId") or -1))
                if conf and int(t["id"]) not in teams:
                    color = t.get("color")
                    teams[int(t["id"])] = {
                        "id": int(t["id"]),
                        "school": t.get("location") or t.get("displayName"),
                        "abbreviation": t.get("abbreviation") or "",
                        "conference": conf[0], "division": None,
                        "color": f"#{color}" if color else None,
                        "logos": [t["logo"]] if t.get("logo") else [],
                        "classification": "fbs",
                    }
    if not games:
        raise ESPNSeasonError(f"ESPN has no FBS games for {year}")
    conferences = [{"name": n, "abbreviation": a, "classification": "fbs"}
                   for n, a in FBS_CONFERENCES.values()]
    return {"teams": sorted(teams.values(), key=lambda t: t["school"]),
            "conferences": conferences, "games": games, "fcs_games": fcs_games}
