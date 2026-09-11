"""One entry point for getting a ready-to-simulate season."""

from __future__ import annotations

import datetime as dt

from ..config import ModelParams, has_api_key
from . import cache
from .cfbd_source import CFBDSource, SourceError
from .espn_source import ESPNError, fetch_fpi
from .season import SeasonState, build_season


def default_year(today: dt.date | None = None) -> int:
    """Default season. Before July, that's the previous calendar year."""
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def load_season(year: int | None = None, *, live: bool = False,
                force: bool = False, params: ModelParams | None = None,
                synthetic: bool = False) -> SeasonState:
    """Build a SeasonState from CFBD, or from fake data if there is no key.

    ``live=True`` caches game results for 10 minutes instead of 6 hours.
    """
    year = year or default_year()

    if synthetic or (not has_api_key() and _synthetic_allowed()):
        import os

        from .synthetic import synthetic_season
        through = int(os.environ.get("CFBROOT_DEMO_WEEK", "6"))
        state = synthetic_season(year=year, played_through=through, params=params)
        state.notes.insert(0, "Demo data: no CFBD API key found, so this is a "
                              "fake season played through week "
                              f"{through}. Add CFBD_API_KEY to .env for real data.")
        return state

    src = CFBDSource(year)
    teams = src.teams(force=force)
    conferences = src.conferences(force=force)
    games = src.games(live=live, force=force)

    notes: list[str] = []
    ratings_updated = None
    espn_extra: dict = {}

    # Get FPI from ESPN first. CFBD's copy can be days behind.
    fpi: list[dict] = []
    try:
        payload = fetch_fpi(year, force=force)
        fpi = payload["rows"]
        ratings_updated = payload.get("last_updated")
        espn_extra = {int(r["espn_id"]): r for r in fpi if r.get("espn_id")}
    except ESPNError as exc:
        notes.append(f"ESPN's power index was unavailable ({exc}); "
                     "fell back to CollegeFootballData's FPI mirror, "
                     "which can be several days stale.")

    if not fpi:
        fpi = src.fpi(force=force)

    sp = []
    if not fpi:
        sp = src.sp(force=force)
        if not sp:
            raise SourceError(
                "No ratings available from ESPN, CFBD FPI or SP+ for "
                f"{year}. Cannot build a season.")

    state = build_season(year=year, teams_raw=teams, conferences_raw=conferences,
                         games_raw=games, fpi_raw=fpi, sp_raw=sp, params=params)
    state.rating_label = "FPI" if fpi else "SP+"
    state.ratings_updated = ratings_updated
    state.notes = notes + state.notes
    # Keep ESPN's own playoff odds to show next to ours.
    for t in state.teams:
        row = espn_extra.get(int(t.team_id)) if t.team_id is not None else None
        if row:
            t.espn_playoff_prob = row.get("espn_playoff_prob")

    age = cache.cache_age("games", {"year": year, "season_type": "regular"})
    if age is not None and age > 3600:
        state.notes.append(f"Scores are from a cache written {age / 3600:.1f} hours "
                           "ago. Refresh to pull the latest results.")
    return state


def _synthetic_allowed() -> bool:
    import os
    return os.environ.get("CFBROOT_ALLOW_DEMO", "1") not in ("0", "false", "False")
