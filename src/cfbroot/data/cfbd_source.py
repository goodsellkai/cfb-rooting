"""Fetch raw college football data from the CollegeFootballData.com API.

Everything is normalised to plain JSON-safe dicts here so the rest of the app
never touches an SDK object, and so the disk cache can hold the raw payloads.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
from typing import Any

from .. import config
from . import cache


class SourceError(RuntimeError):
    pass


# Refresh cadences. Results move fast on game days; schedules and ratings do not.
TTL_TEAMS = 7 * 24 * 3600
TTL_CONFERENCES = 7 * 24 * 3600
TTL_RATINGS = 12 * 3600
TTL_GAMES_LIVE = 10 * 60
TTL_GAMES_IDLE = 6 * 3600


# The generated models serialise by alias, so every payload comes back in
# camelCase ("homeId", "startTimeTBD"). Normalising to snake_case at this
# boundary means nothing downstream has to know or care which case it got.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _snake(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {_snake(k) if isinstance(k, str) else k: _jsonable(v)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, enum.Enum):
        return obj.value
    if hasattr(obj, "to_dict"):
        return _jsonable(obj.to_dict())
    return obj


def _dump(result) -> list[dict]:
    """Normalise an SDK response to a list of plain dicts.

    Most endpoints return a list of models; a few (``/info/usage``) return a
    single object, so wrap anything that is not already a sequence.
    """
    if result is None:
        return []
    if not isinstance(result, (list, tuple)):
        result = [result]
    out = []
    for m in result:
        d = m.to_dict() if hasattr(m, "to_dict") else dict(m)
        out.append(_jsonable(d))
    return out


class CFBDSource:
    """Thin, cached wrapper around the endpoints this app actually needs."""

    def __init__(self, year: int, api_key: str | None = None):
        self.year = year
        self._key = api_key or config.api_key()

    # -- internals -------------------------------------------------------

    def _client(self):
        import cfbd
        return cfbd.ApiClient(cfbd.Configuration(access_token=self._key))

    def _call(self, api_cls_name: str, method: str, **kwargs) -> list[dict]:
        import cfbd
        try:
            with self._client() as client:
                api = getattr(cfbd, api_cls_name)(client)
                result = getattr(api, method)(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any SDK/HTTP failure uniformly
            raise SourceError(f"{api_cls_name}.{method} failed: {exc}") from exc
        return _dump(result)

    # -- endpoints -------------------------------------------------------

    def teams(self, *, force: bool = False) -> list[dict]:
        return cache.get_or_fetch(
            "teams", {"year": self.year}, TTL_TEAMS,
            lambda: self._call("TeamsApi", "get_fbs_teams", year=self.year),
            force=force)

    def conferences(self, *, force: bool = False) -> list[dict]:
        return cache.get_or_fetch(
            "conferences", {"year": self.year}, TTL_CONFERENCES,
            lambda: self._call("ConferencesApi", "get_conferences"),
            force=force)

    def _games(self, season_type: str, ttl: float, force: bool) -> list[dict]:
        import cfbd

        return cache.get_or_fetch(
            "games", {"year": self.year, "season_type": season_type}, ttl,
            lambda: self._call("GamesApi", "get_games", year=self.year,
                               season_type=cfbd.SeasonType(season_type),
                               classification=cfbd.DivisionClassification.FBS),
            force=force)

    def games(self, *, live: bool = False, force: bool = False) -> list[dict]:
        """All regular-season games involving an FBS team."""
        return self._games("regular", TTL_GAMES_LIVE if live else TTL_GAMES_IDLE,
                           force)

    def postseason_games(self, *, live: bool = False, force: bool = False) -> list[dict]:
        return self._games("postseason",
                           TTL_GAMES_LIVE if live else TTL_GAMES_IDLE, force)

    def fpi(self, *, force: bool = False) -> list[dict]:
        return cache.get_or_fetch(
            "fpi", {"year": self.year}, TTL_RATINGS,
            lambda: self._call("RatingsApi", "get_fpi", year=self.year),
            force=force)

    def sp(self, *, force: bool = False) -> list[dict]:
        return cache.get_or_fetch(
            "sp", {"year": self.year}, TTL_RATINGS,
            lambda: self._call("RatingsApi", "get_sp", year=self.year),
            force=force)

    def check(self) -> dict:
        """Verify the key works and report the account's usage allowance."""
        info = self._call("InfoApi", "get_usage")
        return info[0] if info else {}


def espn_fpi(year: int, *, force: bool = False) -> list[dict]:
    """FPI straight from ESPN's public power-index endpoint.

    Used as a fallback when CFBD has not yet published FPI for the season --
    early in the year CFBD's mirror can lag ESPN by a few days.
    """
    import json
    import urllib.request

    def fetch() -> list[dict]:
        url = ("https://site.web.api.espn.com/apis/fitt/v3/sports/football/"
               f"college-football/powerindex?region=us&lang=en&contentorigin=espn"
               f"&season={year}&limit=400")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)

        rows = []
        for entry in payload.get("teams", []):
            team = entry.get("team", {})
            stats = entry.get("categories", []) or entry.get("stats", [])
            value = None
            for cat in stats:
                names = cat.get("names") or []
                vals = cat.get("values") or []
                if cat.get("name") == "fpi" and names and vals:
                    if "fpi" in names:
                        value = vals[names.index("fpi")]
                    else:
                        value = vals[0]
                    break
            if value is None:
                continue
            rows.append({
                "team": team.get("displayName") or team.get("name"),
                "abbreviation": team.get("abbreviation"),
                "espn_id": team.get("id"),
                "fpi": float(value),
            })
        return rows

    return cache.get_or_fetch("espn_fpi", {"year": year}, TTL_RATINGS, fetch, force=force)
