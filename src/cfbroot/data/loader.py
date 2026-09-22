"""One entry point for getting a ready-to-simulate season."""

from __future__ import annotations

import datetime as dt

from ..config import ModelParams, has_api_key
from . import cache
from .cfbd_source import CFBDSource, SourceError
from . import espn_season
from .espn_source import ESPNError, fetch_fpi
from .season import SeasonState, build_season


def default_year(today: dt.date | None = None) -> int:
    """Default season. Before July, that's the previous calendar year."""
    today = today or dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def load_season(year: int | None = None, *, live: bool = False,
                force: bool = False, params: ModelParams | None = None,
                synthetic: bool = False) -> SeasonState:
    """Build a SeasonState from ESPN, falling back to CFBD, then to fake data.

    ``live=True`` keeps a week still being played for 10 minutes instead of
    3 hours.
    """
    year = year or default_year()
    if synthetic:
        return _demo(year, params, "Demo data")

    notes: list[str] = []
    source, src, fcs = "ESPN", None, None
    try:
        data = espn_season.season(year, live=live, force=force)
        teams, conferences, games = data["teams"], data["conferences"], data["games"]
        fcs = data["fcs_games"]
    except Exception as exc:  # noqa: BLE001
        if not has_api_key():
            if _synthetic_allowed():
                return _demo(year, params, f"ESPN was unavailable ({exc}) and "
                                           "there is no CFBD API key, so this is "
                                           "demo data")
            raise
        notes.append(f"ESPN's scoreboard was unavailable ({exc}), so the games "
                     "come from CollegeFootballData.")
        source, src = "CFBD", CFBDSource(year)
        teams = src.teams(force=force)
        conferences = src.conferences(force=force)
        games = src.games(live=live, force=force)
    games = drop_cancelled(games)

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

    if not fpi and has_api_key():
        src = src or CFBDSource(year)
        fpi = src.fpi(force=force)

    sp = []
    if not fpi and has_api_key():
        sp = src.sp(force=force)
        if not sp:
            raise SourceError(
                "No ratings available from ESPN, CFBD FPI or SP+ for "
                f"{year}. Cannot build a season.")

    state = build_season(year=year, teams_raw=teams, conferences_raw=conferences,
                         games_raw=games, fpi_raw=fpi, sp_raw=sp, params=params)
    state.source = source
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
        state.fcs_games = fcs if fcs is not None else src.fcs_games(force=force)
    except Exception as exc:  # noqa: BLE001
        state.notes.append(
            f"FCS schedules were unavailable ({exc}), so every non-FBS "
            "opponent shares one rating.")
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


def _demo(year: int, params, why: str) -> SeasonState:
    import os

    from .synthetic import synthetic_season
    through = int(os.environ.get("CFBROOT_DEMO_WEEK", "6"))
    state = synthetic_season(year=year, played_through=through, params=params)
    state.source = "demo"
    state.notes.insert(0, f"{why}: a fake season played through week {through}.")
    return state


def _synthetic_allowed() -> bool:
    import os
    return os.environ.get("CFBROOT_ALLOW_DEMO", "1") not in ("0", "false", "False")
