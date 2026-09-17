"""CollegeFootballData.com API access.

Responses are converted to plain dicts so they can be cached as JSON.
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


# How long each kind of data stays cached.
TTL_TEAMS = 7 * 24 * 3600
TTL_CONFERENCES = 7 * 24 * 3600
TTL_RATINGS = 12 * 3600
TTL_GAMES_LIVE = 10 * 60
TTL_GAMES_IDLE = 6 * 3600


# The SDK returns camelCase keys (homeId). They are converted to snake_case here.
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
    """Convert an SDK response (a list of models or a single model) to dicts."""
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
    """Cached access to the CFBD endpoints the app uses."""

    def __init__(self, year: int, api_key: str | None = None):
        self.year = year
        self._key = api_key or config.api_key()

    def _client(self):
        import cfbd
        return cfbd.ApiClient(cfbd.Configuration(access_token=self._key))

    def _call(self, api_cls_name: str, method: str, **kwargs) -> list[dict]:
        import cfbd
        try:
            with self._client() as client:
                api = getattr(cfbd, api_cls_name)(client)
                result = getattr(api, method)(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"{api_cls_name}.{method} failed: {exc}") from exc
        return _dump(result)

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
        """All regular season games involving an FBS team."""
        return self._games("regular", TTL_GAMES_LIVE if live else TTL_GAMES_IDLE,
                           force)

    def postseason_games(self, *, live: bool = False, force: bool = False) -> list[dict]:
        return self._games("postseason",
                           TTL_GAMES_LIVE if live else TTL_GAMES_IDLE, force)

    def fcs_games(self, *, live: bool = False, force: bool = False) -> list[dict]:
        """Games between FCS teams.

        The Massey rating needs these. It rates all of Division I at once, and
        an FCS team judged only on its one trip to an FBS stadium would drag
        that result around. These games never enter the simulation.
        """
        ttl = TTL_GAMES_LIVE if live else TTL_GAMES_IDLE
        out = []
        for season_type in ("regular", "postseason"):
            out += self._fcs(season_type, ttl, force)
        return out

    def _fcs(self, season_type: str, ttl: float, force: bool) -> list[dict]:
        import cfbd

        return cache.get_or_fetch(
            "fcs_games", {"year": self.year, "season_type": season_type}, ttl,
            lambda: self._call("GamesApi", "get_games", year=self.year,
                               season_type=cfbd.SeasonType(season_type),
                               classification=cfbd.DivisionClassification.FCS),
            force=force)

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
        """Check the API key and return usage info."""
        info = self._call("InfoApi", "get_usage")
        return info[0] if info else {}
