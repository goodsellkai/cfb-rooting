"""ESPN power index (FPI).

This is the main ratings source. CFBD's copy of FPI can be several days behind.
CFBD uses ESPN team ids, so ratings are matched to teams by id.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import cache

POWERINDEX_URL = (
    "https://site.web.api.espn.com/apis/fitt/v3/sports/football/college-football"
    "/powerindex?region=us&lang=en&contentorigin=espn&season={year}"
    "&limit={limit}&page={page}"
)
TTL = 3 * 3600
_UA = {"User-Agent": "Mozilla/5.0 (compatible; cfbroot/0.1)"}


class ESPNError(RuntimeError):
    pass


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise ESPNError(f"ESPN power index request failed: {exc}") from exc


def _category_names(payload: dict) -> dict[str, list[str]]:
    """Map each category to its stat names.

    Team values are positional. The names are listed once at the top level.
    """
    out = {}
    for cat in payload.get("categories") or []:
        name = cat.get("name")
        names = cat.get("names")
        if name and names:
            out[name] = list(names)
    return out


def _extract(entry: dict, names: dict[str, list[str]]) -> dict | None:
    team = entry.get("team") or {}
    tid = team.get("id")
    if tid is None:
        return None
    row = {
        "espn_id": int(tid),
        "team": team.get("shortDisplayName") or team.get("nickname")
                or team.get("displayName"),
        "display_name": team.get("displayName"),
        "abbreviation": team.get("abbreviation"),
    }
    for cat in entry.get("categories") or []:
        cname = cat.get("name")
        labels = cat.get("names") or names.get(cname) or []
        values = cat.get("values") or []
        for label, value in zip(labels, values):
            if cname == "fpi" and label == "fpi":
                row["fpi"] = float(value)
            elif cname == "fpi" and label == "probmakeplayoffs":
                row["espn_playoff_prob"] = float(value) / 100.0
            elif cname == "fpi" and label == "probwinconf":
                row["espn_conf_prob"] = float(value) / 100.0
            elif cname == "fpi" and label == "probwintitle":
                row["espn_title_prob"] = float(value) / 100.0
            elif cname == "fpi" and label == "fpirank":
                row["fpi_rank"] = int(value)
    return row if "fpi" in row else None


def fetch_fpi(year: int, *, force: bool = False) -> dict:
    """Return ``{"last_updated": str|None, "rows": [...]}`` for the season."""

    def go() -> dict:
        rows: list[dict] = []
        page = 1
        last_updated = None
        names: dict[str, list[str]] = {}
        while page <= 20:
            payload = _get(POWERINDEX_URL.format(year=year, limit=200, page=page))
            if page == 1:
                names = _category_names(payload)
                last_updated = payload.get("lastUpdated")
            for entry in payload.get("teams") or []:
                row = _extract(entry, names)
                if row:
                    rows.append(row)
            pages = int((payload.get("pagination") or {}).get("pages") or 1)
            if page >= pages:
                break
            page += 1
        if not rows:
            raise ESPNError("ESPN returned no FPI rows")
        return {"last_updated": last_updated, "rows": rows}

    return cache.get_or_fetch("espn_fpi", {"year": year}, TTL, go, force=force)


# The playoff committee's weekly rankings

POLL_URL = ("https://sports.core.api.espn.com/v2/sports/football/leagues/"
            "college-football/seasons/{year}/types/2/weeks/{week}/rankings/{poll}"
            "?lang=en&region=us")
TTL_POLLS = 6 * 3600
AP_POLL = 1              # ESPN's id for the AP Top 25
CFP_POLL = 21            # and for the Playoff Committee Rankings


def fetch_polls(year: int, poll: int = CFP_POLL, *, force: bool = False) -> dict:
    """Every poll of one kind published that season, by week.

    ``{week: {espn team id: rank}}``. ESPN publishes the same polls CFBD does
    and does not meter the calls, so this costs none of the season's API
    quota. A poll that has not started yet, the committee's before November,
    simply has no weeks.
    """
    def go() -> dict:
        out: dict[str, dict[str, int]] = {}
        for week in range(1, 21):
            try:
                d = _get(POLL_URL.format(year=year, week=week, poll=poll))
            except ESPNError:
                continue
            ranks = {}
            for r in d.get("ranks") or []:
                ref = (r.get("team") or {}).get("$ref", "")
                tid = ref.split("teams/")[-1].split("?")[0]
                if tid.isdigit() and r.get("current"):
                    ranks[tid] = int(r["current"])
            if ranks:
                out[str(week)] = ranks
        return out

    return cache.get_or_fetch("espn_poll", {"year": year, "poll": poll},
                              TTL_POLLS, go, force=force)


def fetch_committee_polls(year: int, *, force: bool = False) -> dict:
    """Every Playoff Committee Rankings poll of a season, by week."""
    out = fetch_polls(year, CFP_POLL, force=force)
    if not out:
        raise ESPNError(f"ESPN has no committee polls for {year}")
    return out


def latest_poll(year: int, poll: int, from_week: int = 20, *,
                force: bool = False) -> tuple[int, dict]:
    """The newest poll of that kind: (week, {espn team id: rank}).

    Searches back from ``from_week`` and stops at the first week that has one,
    so it costs a request or two rather than a sweep of the season.
    """
    def go() -> dict:
        for week in range(max(from_week, 1), 0, -1):
            try:
                d = _get(POLL_URL.format(year=year, week=week, poll=poll))
            except ESPNError:
                continue
            ranks = {}
            for r in d.get("ranks") or []:
                ref = (r.get("team") or {}).get("$ref", "")
                tid = ref.split("teams/")[-1].split("?")[0]
                if tid.isdigit() and r.get("current"):
                    ranks[tid] = int(r["current"])
            if ranks:
                return {"week": week, "ranks": ranks}
        return {"week": 0, "ranks": {}}

    blob = cache.get_or_fetch("espn_latest_poll", {"year": year, "poll": poll},
                              TTL_POLLS, go, force=force)
    return int(blob["week"]), blob["ranks"]
