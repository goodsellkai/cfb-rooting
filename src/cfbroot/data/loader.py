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
    games = drop_cancelled(src.games(live=live, force=force))

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

    # Rate the FCS opponents from their own schedules, once. They barely move
    # between simulated seasons, so the simulator can treat them as known
    # instead of averaging them all into one rating, which is worth about four
    # places of rank per FBS team.
    # The AP and committee polls, when they exist. The committee's first is
    # in November, so early in the season there is only the AP's.
    from .espn_source import AP_POLL, CFP_POLL, latest_poll, preseason_poll

    by_espn = {int(t.team_id): t.idx for t in state.teams if t.team_id is not None}
    for name, poll in (("ap", AP_POLL), ("cfp", CFP_POLL)):
        try:
            week, ranks = latest_poll(year, poll, state.current_week(),
                                       force=force)
        except Exception:  # noqa: BLE001
            continue      # a poll nobody published yet, or ESPN is down
        ranked = {by_espn[int(tid)]: rank for tid, rank in ranks.items()
                  if int(tid) in by_espn}
        if ranked:
            state.polls[name] = ranked
            state.poll_weeks[name] = week

    try:
        # What people thought before the season, which steadies the first few
        # weeks of a simulated season's own rankings.
        pre = {by_espn[int(tid)]: rank
               for tid, rank in preseason_poll(year, force=force).items()
               if int(tid) in by_espn}
        if pre:
            state.polls["ap_preseason"] = pre
    except Exception:  # noqa: BLE001
        pass

    try:
        state.fcs_games = src.fcs_games(force=force)
    except Exception as exc:  # noqa: BLE001
        state.notes.append(
            f"FCS schedules were unavailable ({exc}), so every non-FBS "
            "opponent shares one rating.")

    age = cache.cache_age("games", {"year": year, "season_type": "regular"})
    if age is not None and age > 3600:
        state.notes.append(f"Scores are from a cache written {age / 3600:.1f} hours "
                           "ago. Restart the app to pull the latest results.")
    return state


def drop_cancelled(games: list[dict], now: dt.datetime | None = None,
                   grace: dt.timedelta = dt.timedelta(days=2)) -> list[dict]:
    """Leave out games that were due more than ``grace`` ago and never played.

    Those were cancelled, like Liberty at App State after Hurricane Helene in
    2024. Kept, the simulator would keep playing them every season.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for g in games:
        start = g.get("start_date")
        if not g.get("completed") and start:
            when = dt.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            if when < now - grace:
                continue
        out.append(g)
    return out


def _synthetic_allowed() -> bool:
    import os
    return os.environ.get("CFBROOT_ALLOW_DEMO", "1") not in ("0", "false", "False")
