"""Normalise raw API payloads into the arrays the simulator consumes."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from ..config import (MAX_CONF_SIZE, NO_CCG_CONFERENCES, POWER_CONFERENCES,
                      ModelParams)
from ..model import evaluate, provenance, win_probability

# status codes on the unified game table
TO_SIMULATE = 0
HOME_WON = 1
AWAY_WON = 2

CCG_PATTERN = re.compile(r"champion", re.IGNORECASE)
_STOPWORDS = re.compile(r"\b(university|univ|college|the|of)\b")


def normalise_name(name: str) -> str:
    """Collapse a school name to a form that matches across data sources."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _STOPWORDS.sub(" ", s.lower())
    return re.sub(r"[^a-z0-9]+", "", s)


@dataclass
class TeamInfo:
    idx: int
    school: str
    team_id: int | None = None
    abbreviation: str = ""
    conference: str | None = None
    conf_idx: int = -1
    division: str | None = None
    div_idx: int = -1
    is_fbs: bool = True
    rating: float = 0.0
    rating_source: str = "default"
    color: str | None = None
    logo: str | None = None
    espn_playoff_prob: float | None = None   # ESPN's own number, for comparison


@dataclass
class ConferenceInfo:
    idx: int
    name: str
    abbreviation: str = ""
    is_power: bool = False
    has_ccg: bool = True
    # Independents have no title to win, so they must not be eligible for the
    # highest-ranked-champion auto bid.
    crowns_champion: bool = True
    divisions: list[str] = field(default_factory=list)
    team_idxs: list[int] = field(default_factory=list)
    # A title game already played (or already locked in) overrides the
    # simulated one: (home_idx, away_idx, status).
    fixed_ccg: tuple[int, int, int] | None = None


@dataclass
class KernelInputs:
    """Flat arrays handed to the numba kernel. All indices are team indices."""

    rating: np.ndarray
    conf_id: np.ndarray
    div_id: np.ndarray
    is_fbs: np.ndarray
    exp_elite_wins: np.ndarray

    g_home: np.ndarray
    g_away: np.ndarray
    g_neutral: np.ndarray
    g_conf: np.ndarray
    g_pwin: np.ndarray
    g_status: np.ndarray

    remaining_idx: np.ndarray          # indices into g_* that get simulated

    conf_teams_ptr: np.ndarray         # CSR over conferences -> team indices
    conf_teams: np.ndarray
    conf_games_ptr: np.ndarray         # CSR over conferences -> game indices
    conf_games: np.ndarray
    conf_has_ccg: np.ndarray
    conf_crowns: np.ndarray
    conf_n_div: np.ndarray
    conf_is_power: np.ndarray
    conf_fixed_ccg: np.ndarray         # (n_conf, 3): home, away, status; -1 if none

    n_teams: int
    n_conf: int


@dataclass
class SeasonState:
    year: int
    teams: list[TeamInfo]
    conferences: list[ConferenceInfo]
    games: list[dict]
    params: ModelParams
    diagnostics: object = None
    as_of: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    rating_label: str = "FPI"
    ratings_updated: str | None = None
    notes: list[str] = field(default_factory=list)

    # -- lookups ---------------------------------------------------------

    def team_by_name(self, name: str) -> TeamInfo | None:
        key = normalise_name(name)
        for t in self.teams:
            if normalise_name(t.school) == key or (
                    t.abbreviation and normalise_name(t.abbreviation) == key):
                return t
        hits = [t for t in self.teams
                if t.is_fbs and key and key in normalise_name(t.school)]
        return hits[0] if len(hits) == 1 else None

    def search_teams(self, query: str, limit: int = 12) -> list[TeamInfo]:
        key = normalise_name(query)
        fbs = [t for t in self.teams if t.is_fbs]
        if not key:
            return sorted(fbs, key=lambda t: t.school)[:limit]
        scored = []
        for t in fbs:
            n = normalise_name(t.school)
            if n == key:
                rank = 0
            elif n.startswith(key):
                rank = 1
            elif key in n:
                rank = 2
            elif t.abbreviation and key in normalise_name(t.abbreviation):
                rank = 3
            else:
                continue
            scored.append((rank, t.school, t))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [t for _, _, t in scored[:limit]]

    @property
    def fbs_teams(self) -> list[TeamInfo]:
        return [t for t in self.teams if t.is_fbs]

    @property
    def remaining_games(self) -> list[dict]:
        return [g for g in self.games if g["status"] == TO_SIMULATE and not g["is_ccg"]]

    @property
    def completed_games(self) -> list[dict]:
        return [g for g in self.games if g["status"] != TO_SIMULATE]

    def current_week(self) -> int:
        """The lowest week that still has an unplayed game."""
        pending = [g["week"] for g in self.games
                   if g["status"] == TO_SIMULATE and not g["is_ccg"]]
        return min(pending) if pending else max((g["week"] for g in self.games), default=0)

    def default_week(self) -> int:
        """The slate a user most likely means.

        ``current_week`` is the first week with any unplayed game, which on a
        Sunday can be a week that is 99% finished and has one Monday nighter
        left. The useful default is the first week that has actually not been
        played yet.
        """
        totals: dict[int, int] = {}
        pending: dict[int, int] = {}
        for g in self.games:
            if g["is_ccg"]:
                continue
            totals[g["week"]] = totals.get(g["week"], 0) + 1
            if g["status"] == TO_SIMULATE:
                pending[g["week"]] = pending.get(g["week"], 0) + 1
        for wk in sorted(pending):
            if pending[wk] >= 0.5 * totals.get(wk, 1):
                return wk
        return self.current_week()

    def games_in_week(self, week: int, only_remaining: bool = True) -> list[dict]:
        return [g for g in self.games
                if g["week"] == week and not g["is_ccg"]
                and (g["status"] == TO_SIMULATE or not only_remaining)]

    # -- kernel inputs ---------------------------------------------------

    def kernel_inputs(self) -> KernelInputs:
        p = self.params
        n_teams = len(self.teams)
        n_conf = len(self.conferences)

        rating = np.array([t.rating for t in self.teams], dtype=np.float64)
        conf_id = np.array([t.conf_idx for t in self.teams], dtype=np.int32)
        div_id = np.array([t.div_idx for t in self.teams], dtype=np.int32)
        is_fbs = np.array([t.is_fbs for t in self.teams], dtype=np.bool_)

        sim_games = [g for g in self.games if not g["is_ccg"]]
        n_g = len(sim_games)
        g_home = np.empty(n_g, dtype=np.int32)
        g_away = np.empty(n_g, dtype=np.int32)
        g_neutral = np.empty(n_g, dtype=np.bool_)
        g_conf = np.empty(n_g, dtype=np.bool_)
        g_status = np.empty(n_g, dtype=np.uint8)
        for i, g in enumerate(sim_games):
            h, a = g["home_idx"], g["away_idx"]
            g_home[i] = h
            g_away[i] = a
            g_neutral[i] = g["neutral"]
            g_status[i] = g["status"]
            g_conf[i] = bool(conf_id[h] >= 0 and conf_id[h] == conf_id[a]
                             and is_fbs[h] and is_fbs[a])
            g["sim_idx"] = i

        g_pwin = win_probability(rating[g_home], rating[g_away], g_neutral, p)
        g_pwin = np.ascontiguousarray(np.asarray(g_pwin, dtype=np.float64))
        for i, g in enumerate(sim_games):
            g["pwin_home"] = float(g_pwin[i])

        remaining_idx = np.flatnonzero(g_status == TO_SIMULATE).astype(np.int32)

        # Expected wins for a reference playoff-caliber team against each
        # team's schedule -- the resume half of the committee proxy.
        elite = np.full(n_g, p.elite_rating)
        p_elite_home = np.asarray(win_probability(elite, rating[g_away], g_neutral, p))
        p_elite_away = 1.0 - np.asarray(win_probability(rating[g_home], elite, g_neutral, p))
        exp_elite = np.zeros(n_teams, dtype=np.float64)
        np.add.at(exp_elite, g_home, p_elite_home)
        np.add.at(exp_elite, g_away, p_elite_away)

        # Shrink each team's schedule adjustment toward the FBS average, on a
        # per-game rate so that a 13-game schedule is handled correctly. Without
        # this, a team with a punishing schedule banks so much strength-of-record
        # credit that losses stop mattering.
        n_games = np.zeros(n_teams, dtype=np.float64)
        np.add.at(n_games, g_home, 1.0)
        np.add.at(n_games, g_away, 1.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            rate = np.where(n_games > 0, exp_elite / np.maximum(n_games, 1), 0.0)
        fbs_mask = is_fbs & (n_games > 0)
        mean_rate = float(rate[fbs_mask].mean()) if fbs_mask.any() else 0.0
        shrunk = mean_rate + p.resume_shrink * (rate - mean_rate)
        exp_elite = shrunk * n_games

        # CSR: teams per conference, and conference games per conference
        conf_teams_list = [c.team_idxs for c in self.conferences]
        biggest = max((len(c.team_idxs) for c in self.conferences), default=0)
        if biggest > MAX_CONF_SIZE:
            raise ValueError(
                f"conference with {biggest} members exceeds the kernel's "
                f"MAX_CONF_SIZE of {MAX_CONF_SIZE}")
        conf_teams_ptr = np.zeros(n_conf + 1, dtype=np.int32)
        conf_teams_ptr[1:] = np.cumsum([len(x) for x in conf_teams_list])
        conf_teams = np.array([i for x in conf_teams_list for i in x] or [0],
                              dtype=np.int32)[:conf_teams_ptr[-1] or None]
        if conf_teams_ptr[-1] == 0:
            conf_teams = np.zeros(0, dtype=np.int32)

        per_conf_games: list[list[int]] = [[] for _ in range(n_conf)]
        for i in range(n_g):
            if g_conf[i]:
                per_conf_games[conf_id[g_home[i]]].append(i)
        conf_games_ptr = np.zeros(n_conf + 1, dtype=np.int32)
        conf_games_ptr[1:] = np.cumsum([len(x) for x in per_conf_games])
        flat = [i for x in per_conf_games for i in x]
        conf_games = np.array(flat, dtype=np.int32) if flat else np.zeros(0, dtype=np.int32)

        conf_has_ccg = np.array([c.has_ccg for c in self.conferences], dtype=np.bool_)
        conf_crowns = np.array([c.crowns_champion for c in self.conferences],
                               dtype=np.bool_)
        conf_n_div = np.array([len(c.divisions) for c in self.conferences], dtype=np.int32)
        conf_is_power = np.array([c.is_power for c in self.conferences], dtype=np.bool_)
        conf_fixed = np.full((n_conf, 3), -1, dtype=np.int32)
        for c in self.conferences:
            if c.fixed_ccg is not None:
                conf_fixed[c.idx] = c.fixed_ccg

        return KernelInputs(
            rating=rating, conf_id=conf_id, div_id=div_id, is_fbs=is_fbs,
            exp_elite_wins=exp_elite,
            g_home=g_home, g_away=g_away, g_neutral=g_neutral, g_conf=g_conf,
            g_pwin=g_pwin, g_status=g_status,
            remaining_idx=remaining_idx,
            conf_teams_ptr=conf_teams_ptr, conf_teams=conf_teams,
            conf_games_ptr=conf_games_ptr, conf_games=conf_games,
            conf_has_ccg=conf_has_ccg, conf_crowns=conf_crowns, conf_n_div=conf_n_div,
            conf_is_power=conf_is_power, conf_fixed_ccg=conf_fixed,
            n_teams=n_teams, n_conf=n_conf,
        )


# ---------------------------------------------------------------------------
# building a SeasonState from raw payloads
# ---------------------------------------------------------------------------

def _pick_rating(team_id, name_key: str, fpi_by_id: dict, fpi_map: dict,
                 sp_map: dict) -> tuple[float, str]:
    """Prefer an exact id join; fall back to a normalised name."""
    if team_id is not None and int(team_id) in fpi_by_id:
        return float(fpi_by_id[int(team_id)]), "FPI"
    if name_key in fpi_map:
        return float(fpi_map[name_key]), "FPI"
    if name_key in sp_map:
        return float(sp_map[name_key]), "SP+"
    return float("nan"), "missing"


def build_season(year: int, teams_raw: list[dict], conferences_raw: list[dict],
                 games_raw: list[dict], fpi_raw: list[dict],
                 sp_raw: list[dict] | None = None,
                 params: ModelParams | None = None,
                 recalibrate: bool = True) -> SeasonState:
    # ``recalibrate`` now only controls whether fit diagnostics are computed;
    # the model parameters themselves are fixed.
    params = params or ModelParams()
    sp_raw = sp_raw or []
    notes: list[str] = []

    # CollegeFootballData uses ESPN's team ids, so ratings from either source
    # join on an exact integer key; names are only a fallback.
    fpi_by_id = {int(r["espn_id"]): r["fpi"] for r in fpi_raw
                 if r.get("fpi") is not None and r.get("espn_id") is not None}
    fpi_map = {normalise_name(r.get("team", "")): r["fpi"]
               for r in fpi_raw if r.get("fpi") is not None}
    sp_map = {normalise_name(r.get("team", "")): r["rating"]
              for r in sp_raw if r.get("rating") is not None}

    conf_meta = {c["name"]: c for c in conferences_raw if c.get("name")}

    teams: list[TeamInfo] = []
    by_id: dict[int, int] = {}
    by_name: dict[str, int] = {}
    conf_names: list[str] = []
    conf_index: dict[str, int] = {}

    def conf_idx_for(name: str | None) -> int:
        if not name:
            return -1
        if name not in conf_index:
            conf_index[name] = len(conf_names)
            conf_names.append(name)
        return conf_index[name]

    div_index: dict[tuple[int, str], int] = {}
    conf_divisions: dict[int, list[str]] = {}

    for t in teams_raw:
        school = t.get("school") or t.get("team") or ""
        if not school:
            continue
        idx = len(teams)
        cname = t.get("conference")
        ci = conf_idx_for(cname)
        division = t.get("division") or None
        di = -1
        if division and ci >= 0:
            key = (ci, division)
            if key not in div_index:
                conf_divisions.setdefault(ci, [])
                div_index[key] = len(conf_divisions[ci])
                conf_divisions[ci].append(division)
            di = div_index[key]
        name_key = normalise_name(school)
        rating, src = _pick_rating(t.get("id"), name_key, fpi_by_id, fpi_map,
                                   sp_map)
        teams.append(TeamInfo(
            idx=idx, school=school, team_id=t.get("id"),
            abbreviation=t.get("abbreviation") or "", conference=cname,
            conf_idx=ci, division=division, div_idx=di, is_fbs=True,
            rating=rating, rating_source=src, color=t.get("color"),
            logo=(t.get("logos") or [None])[0]))
        if t.get("id") is not None:
            by_id[int(t["id"])] = idx
        by_name[name_key] = idx

    # A team with no published rating gets the FBS median, so one missing row
    # never silently deletes a team from the field.
    rated = [t.rating for t in teams if np.isfinite(t.rating)]
    fill = float(np.median(rated)) if rated else 0.0
    missing = [t.school for t in teams if not np.isfinite(t.rating)]
    for t in teams:
        if not np.isfinite(t.rating):
            t.rating = fill
            t.rating_source = "imputed"
    if missing:
        notes.append(
            f"{len(missing)} FBS team(s) had no published rating and were set to the "
            f"FBS median: {', '.join(sorted(missing)[:6])}"
            + (" ..." if len(missing) > 6 else ""))

    def ensure_team(team_id, name, conference) -> int:
        """Register a non-FBS opponent on first sight."""
        if team_id is not None and int(team_id) in by_id:
            return by_id[int(team_id)]
        key = normalise_name(name or "")
        if key and key in by_name:
            return by_name[key]
        idx = len(teams)
        teams.append(TeamInfo(idx=idx, school=name or f"Unknown {idx}",
                              team_id=int(team_id) if team_id is not None else None,
                              conference=conference, conf_idx=-1, is_fbs=False,
                              rating=params.fcs_rating, rating_source="fcs-default"))
        if team_id is not None:
            by_id[int(team_id)] = idx
        if key:
            by_name[key] = idx
        return idx

    # -- games -----------------------------------------------------------
    games: list[dict] = []
    max_week = max((int(g.get("week") or 0) for g in games_raw), default=0)
    for g in games_raw:
        hi = ensure_team(g.get("home_id"), g.get("home_team"), g.get("home_conference"))
        ai = ensure_team(g.get("away_id"), g.get("away_team"), g.get("away_conference"))
        if not (teams[hi].is_fbs or teams[ai].is_fbs):
            continue
        hp, ap = g.get("home_points"), g.get("away_points")
        completed = bool(g.get("completed")) and hp is not None and ap is not None
        if completed and hp == ap:
            completed = False  # a tie is not a real outcome; treat as unplayed
        status = HOME_WON if (completed and hp > ap) else (
            AWAY_WON if completed else TO_SIMULATE)
        note_txt = g.get("notes") or ""
        # A conference title game is any game the feed labels a championship
        # between two FBS teams from the same conference. Matching on the
        # matchup rather than on the conference_game flag means a feed that
        # leaves that flag unset cannot smuggle a title game into the regular
        # schedule, where it would be counted twice.
        is_ccg = bool(
            CCG_PATTERN.search(note_txt)
            and teams[hi].is_fbs and teams[ai].is_fbs
            and teams[hi].conf_idx >= 0
            and teams[hi].conf_idx == teams[ai].conf_idx)
        games.append({
            "game_id": g.get("id"),
            "week": int(g.get("week") or max_week),
            "season_type": g.get("season_type", "regular"),
            "start_date": g.get("start_date"),
            "home_idx": hi, "away_idx": ai,
            "home": teams[hi].school, "away": teams[ai].school,
            "neutral": bool(g.get("neutral_site")),
            "conference_game": bool(g.get("conference_game")),
            "home_points": hp, "away_points": ap,
            "status": status, "completed": completed,
            "is_ccg": is_ccg, "notes": note_txt,
            "sim_idx": -1, "pwin_home": float("nan"),
        })

    # -- conferences -----------------------------------------------------
    conferences: list[ConferenceInfo] = []
    for i, name in enumerate(conf_names):
        meta = conf_meta.get(name, {})
        members = [t.idx for t in teams if t.conf_idx == i and t.is_fbs]
        independent = "independent" in name.lower()
        has_ccg = (len(members) >= 4 and name not in NO_CCG_CONFERENCES
                   and not independent)
        conferences.append(ConferenceInfo(
            idx=i, name=name, abbreviation=meta.get("abbreviation") or name,
            is_power=name in POWER_CONFERENCES, has_ccg=has_ccg,
            crowns_champion=not independent and len(members) >= 2,
            divisions=conf_divisions.get(i, []), team_idxs=members))

    # A title game already scheduled with both participants known (or already
    # played) overrides the simulated matchup.
    for g in games:
        if not g["is_ccg"]:
            continue
        ci = teams[g["home_idx"]].conf_idx
        if ci >= 0 and teams[g["away_idx"]].conf_idx == ci:
            conferences[ci].fixed_ccg = (g["home_idx"], g["away_idx"], g["status"])

    state = SeasonState(year=year, teams=teams, conferences=conferences, games=games,
                        params=params, rating_label="FPI", notes=notes)

    if recalibrate:
        played = [g for g in games
                  if g["status"] != TO_SIMULATE and not g["is_ccg"]
                  and teams[g["home_idx"]].is_fbs and teams[g["away_idx"]].is_fbs]
        state.diagnostics = evaluate(
            [teams[g["home_idx"]].rating for g in played],
            [teams[g["away_idx"]].rating for g in played],
            [g["neutral"] for g in played],
            [g["home_points"] - g["away_points"] for g in played],
            state.params)

    return state
