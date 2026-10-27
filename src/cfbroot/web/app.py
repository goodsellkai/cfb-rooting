"""Local web app: the rooting guide, and a sample season played out."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import DEFAULT_METRICS, METRIC_LABELS, METRIC_NAMES, SimConfig
from ..data.loader import default_year, load_season
from ..data.season import (KernelInputs, SeasonState, before_week,
                           last_played_week)
from ..model import provenance as model_provenance
from ..sim import build_guide, league_all, run_league
from ..sim.engine import CCG_LOST, CCG_MISSED, CCG_WON, LeagueResults
from ..sim.sample import sample_season, weekly_systems
from .pages import slug

HERE = Path(__file__).resolve().parent

# One run of this size serves every team, so the app does it once at
# start-up. It puts the error on a playoff probability at about 0.025
# percentage points.
DEFAULT_SIMS = int(os.environ.get("CFBROOT_SIMS") or 2_500_000)
# The second run, with the week's results set aside, only has to value games
# that are already decided, so it is a tenth the size. Zero turns it off.
REPLAY_SIMS = int(os.environ.get("CFBROOT_REPLAY_SIMS") or 250_000)
SEED = 12345

app = FastAPI(title="cfbroot", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(StarletteHTTPException)
async def missing_page(request, exc):
    """An address the site does not have gets the same page the hosted site
    serves. The API keeps answering in JSON, which is what its callers read."""
    if exc.status_code != 404 or request.url.path.startswith("/api/"):
        return await http_exception_handler(request, exc)
    from . import pages
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("<!--HEAD-->", pages.not_found_head())
                .replace(pages.INTRO_BLOCK, pages.not_found("/")))
    html = html.replace('<script src="/static/app.js',
                        '<script>window.CFBROOT_INFO = true;</script>'
                        '<script src="/static/app.js')
    return HTMLResponse(html, status_code=404, headers={"Cache-Control": "no-store"})


# Server state

@dataclass
class Job:
    status: str = "running"       # running | done | error
    done: int = 0
    total: int = DEFAULT_SIMS
    started: float = field(default_factory=time.time)
    error: str | None = None


class Store:
    """The season, and the one set of simulations that serves every team.

    Which team is being asked about never changed how a season played out, so
    the simulation is run once at start-up and each team is a slice of it.
    Restarting the server re-pulls the data and simulates again.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.season: SeasonState | None = None
        self.year: int = default_year()
        self.loaded_at: float = 0.0
        self.league: LeagueResults | None = None   # shared by every team
        self.replay: LeagueResults | None = None   # the same, last week set aside
        self.replay_state: SeasonState | None = None
        self.replay_week = 0
        self.job: Job | None = None
        self.payloads: dict[int, dict] = {}
        self.inputs: KernelInputs | None = None    # reused by sample seasons
        self.weekly: dict | None = None            # a system per poll week
        self.sample_ready = threading.Event()   # set once warm-up has tried

    def get_season(self) -> SeasonState:
        with self.lock:
            if self.season is None:
                self.season = load_season(self.year)
                self.loaded_at = time.time()
            return self.season

    def start_league(self) -> Job:
        """Kick off the shared simulation, or hand back the one already going."""
        state = self.get_season()
        with self.lock:
            if self.job is not None and self.job.status != "error":
                return self.job
            job = self.job = Job()

        def work() -> None:
            try:
                cfg = SimConfig(n_sims=DEFAULT_SIMS, batch_size=50_000,
                                seed=SEED)

                def progress(done: int, total: int) -> None:
                    job.done, job.total = done, total

                league = run_league(state, cfg, progress=progress)
                with self.lock:
                    self.league = league
                self.run_replay(state)
                job.status = "done"
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = f"{exc}\n{traceback.format_exc()}"

        threading.Thread(target=work, daemon=True, name="league").start()
        return job

    def run_replay(self, state: SeasonState) -> None:
        """Value the games already played, on the same footing as the rest."""
        week = last_played_week(state)
        if not REPLAY_SIMS or not week:
            return
        back = before_week(state, week)
        if not back.remaining_games:
            return
        league = run_league(back, SimConfig(n_sims=REPLAY_SIMS,
                                            batch_size=50_000, seed=SEED + 1))
        with self.lock:
            self.replay_state, self.replay, self.replay_week = back, league, week

    def replay_payload(self, team_idx: int) -> dict:
        """What the week's finished games were worth to one team."""
        if self.replay is None:
            return {}
        back = self.replay_state
        res = self.replay.for_team(team_idx)
        guide = build_guide(back, res, primary="make_playoff", week=None)
        done = {g["game_id"]: g for g in self.season.games
                if g["completed"] and not g["is_ccg"]}

        def finished(entries):
            out = []
            for lev in entries:
                real = done.get(lev.game.get("game_id"))
                if real is None or real["week"] < self.replay_week:
                    continue
                row = lev.as_dict()
                row["home_points"] = real["home_points"]
                row["away_points"] = real["away_points"]
                out.append(row)
            return out

        games, own = finished(guide.games), finished(guide.own_games)
        if not games and not own:
            return {}
        # A game nobody lost in any season leaves a NaN behind, which no
        # amount of JSON will carry.
        return _clean({"week": self.replay_week, "n_sims": guide.n_sims,
                       "before": {k: v.p for k, v in guide.headline.items()},
                       "games": _slim(games, points=True),
                       "own_games": _slim(own, points=True)})

    def payload(self, team_idx: int) -> dict:
        """One team's guide, built on first request and kept."""
        if team_idx not in self.payloads:
            s = self.season
            res = self.league.for_team(team_idx)
            # Score every remaining game for every metric. Week and metric are
            # filters the client applies without asking again.
            guide = build_guide(s, res, primary="make_playoff", week=None)
            data = _guide_payload(guide, res, s)
            data["replay"] = self.replay_payload(team_idx)
            self.payloads[team_idx] = data
        return self.payloads[team_idx]


store = Store()


# JSON helpers

# What the page reads out of a game. The rest of what the guide works out,
# the p-values and the counts behind them, never reaches the screen, and
# sending it costs more than everything else on the page put together.
GAME_KEYS = ("home", "away", "home_idx", "away_idx", "week", "neutral",
             "p_home_win", "start_date", "time_set", "broadcast")
SWING_KEYS = ("p_if_home", "p_if_away", "delta", "lo", "hi", "home",
              "sig_week", "sig_all", "reliable")


def _slim(games, points=False):
    out = []
    for g in games:
        keys = GAME_KEYS + (("home_points", "away_points") if points else ())
        row = {k: g[k] for k in keys if k in g}
        row["swings"] = {m: {k: s[k] for k in SWING_KEYS if k in s}
                         for m, s in g["swings"].items()}
        out.append(row)
    return out


def _clean(obj):
    """Make numpy scalars and NaNs safe for JSON.

    Probabilities are rounded to six places, which is far finer than anything
    shown and keeps a full-precision float from taking twenty characters.
    """
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not np.isfinite(f) else round(f, 6)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _season_payload(s: SeasonState) -> dict:
    played = len([g for g in s.games if g["status"] != 0 and not g["is_ccg"]])
    remaining = len(s.remaining_games)
    weeks = sorted({g["week"] for g in s.games if not g["is_ccg"]})
    diag = s.diagnostics
    return _clean({
        "year": s.year,
        "current_week": s.current_week(),
        "default_week": s.default_week(),
        "weeks": weeks,
        "games_played": played,
        "games_remaining": remaining,
        "rating_label": s.rating_label,
        "ratings_updated": s.ratings_updated,
        "carried_games": s.carried_games,
        "model": model_provenance(s.params),
        "diagnostics": diag.summary() if diag is not None else "",
        "notes": s.notes,
        "has_api_key": s.source != "demo",
        "source": s.source,
        "loaded_at": store.loaded_at,
        "params": s.params.to_dict(),
        "metrics": [{"key": m, "label": METRIC_LABELS[m]} for m in METRIC_NAMES],
        "default_metrics": DEFAULT_METRICS,
        "teams": [
            {"idx": t.idx, "name": t.school, "conference": t.conference,
             "rating": t.rating, "abbr": t.abbreviation, "slug": slug(t.school)}
            for t in sorted(s.fbs_teams, key=lambda t: t.school)
        ],
        # All teams, including non-FBS opponents, so every game can show logos.
        "team_index": {
            str(t.idx): {"name": t.school, "abbr": t.abbreviation,
                         "logo": t.logo, "color": t.color,
                         "conference": t.conference, "fbs": t.is_fbs,
                         "rating": t.rating}
            for t in s.teams
        },
        # The published polls, to show next to the model's own ranking.
        "polls": {k: {str(i): r for i, r in v.items()} for k, v in s.polls.items()},
        "poll_weeks": s.poll_weeks,
        # What is still to come, for the same card.
        "upcoming": [
            {"week": g["week"], "home": g["home_idx"], "away": g["away_idx"],
             "neutral": g["neutral"], "p_home": g.get("pwin_home"),
             "start": g.get("start_date"), "tv": g.get("broadcast", ""),
             "time_set": bool(g.get("time_set", True))}
            for g in sorted(s.games, key=lambda g: g["week"])
            if g["status"] == 0 and not g["is_ccg"]
        ],
        # Results so far, for the game list shown when hovering over a team.
        "played": [
            {"week": g["week"], "home": g["home_idx"], "away": g["away_idx"],
             "home_points": g["home_points"], "away_points": g["away_points"],
             "neutral": g["neutral"], "title_game": g["is_ccg"],
             "conference": bool(g["conference_game"])}
            for g in sorted(s.games, key=lambda g: g["week"])
            if g["status"] != 0
        ],
    })


def _guide_payload(guide, res, s: SeasonState) -> dict:
    return _clean({
        "team": guide.team,
        "n_sims": guide.n_sims,
        "week": guide.week,
        "primary": guide.primary,
        "elapsed": guide.elapsed,
        "used_numba": guide.used_numba,
        "n_tests": guide.n_tests,
        "fdr_q": guide.fdr_q,
        "alpha": guide.alpha,
        "resolution": guide.resolution,
        "expected_wins": guide.expected_wins,
        "notes": guide.notes,
        "headline": {k: {"p": v.p, "lo": v.lo, "hi": v.hi, "se": v.se,
                         "label": METRIC_LABELS[k]}
                     for k, v in guide.headline.items()},
        "wins_distribution": guide.wins_distribution,
        "seed_distribution": guide.seed_distribution,
        "own_games": _slim([g.as_dict() for g in guide.own_games]),
        "games": _slim([g.as_dict() for g in guide.games]),
        "league": league_all(s, res),
        "by_record": _by_record(res, s),
        "title_game": _title_game(res),
    })


def _by_record(res, s: SeasonState) -> list[dict]:
    """Every measure at each final regular-season record the team reaches,
    and how often it gets there, best record first. ``n`` is the seasons
    behind a row, so a record that hardly ever happens can be left out."""
    me = res.focus_idx
    games = sum(1 for g in s.games if not g["is_ccg"]
                and me in (g["home_idx"], g["away_idx"]))
    rows = []
    for w in range(res.wins_hist.size - 1, -1, -1):
        n = int(res.wins_hist[w])
        if not n:
            continue
        rows.append({"wins": w, "losses": max(games - w, 0), "n": n,
                     "p": n / max(res.n_sims, 1),
                     "odds": {m: float(res.wins_metric[w, i]) / n
                              for i, m in enumerate(res.metric_names)}})
    return rows


def _title_game(res) -> dict | None:
    """The conference title game three ways: in it and won, in it and lost,
    not in it. How often each happens, and every measure in each."""
    seasons = res.ccg_seasons
    if not seasons[CCG_WON] + seasons[CCG_LOST]:
        return None
    cols = (CCG_WON, CCG_LOST, CCG_MISSED)
    return {"p": [float(seasons[c]) / max(res.n_sims, 1) for c in cols],
            "n": [int(seasons[c]) for c in cols],
            "odds": {m: [float(res.ccg_counts[c, i]) / seasons[c] if seasons[c]
                         else None for c in cols]
                     for i, m in enumerate(res.metric_names)}}


# Routes

# Files the page names with a version on them, so a browser holding last
# build's copy fetches the new one.
VERSIONED = ("app.css", "app.js", "season.js", "logo.svg", "favicon.ico",
             "icon-192.png", "apple-touch-icon.png", "site.webmanifest")


def _asset_token() -> str:
    """Changes when any versioned file changes, so browsers don't use a stale copy."""
    stamps = []
    for name in VERSIONED:
        f = HERE / "static" / name
        stamps.append(str(int(f.stat().st_mtime)) if f.exists() else "0")
    return "-".join(stamps)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = html.replace("<!--HEAD-->", "<title>CFB Rooting Guide</title>")
    token = _asset_token()
    for name in VERSIONED:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/team/{name}/", response_class=HTMLResponse)
def team_page(name: str) -> HTMLResponse:
    """A team's address, as the hosted site has one, opening on that team."""
    state = store.get_season()
    team = next((t for t in state.fbs_teams if slug(t.school) == name), None)
    if team is None:
        raise HTTPException(status_code=404, detail=f"unknown team: {name}")
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = html.replace("<!--HEAD-->", f"<title>{team.school} | CFB Rooting Guide</title>")
    html = html.replace('<script src="/static/app.js',
                        f'<script>window.CFBROOT_TEAM = {json.dumps(team.school)};</script>'
                        '<script src="/static/app.js')
    token = _asset_token()
    for asset in VERSIONED:
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/how-it-works/", response_class=HTMLResponse)
def how_it_works() -> HTMLResponse:
    from . import pages
    state = store.get_season()
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("<!--HEAD-->", pages.how_head(state, DEFAULT_SIMS))
                .replace(pages.INTRO_BLOCK, pages.how_body(state, DEFAULT_SIMS)))
    html = html.replace('<script src="/static/app.js',
                        '<script>window.CFBROOT_INFO = true;</script>'
                        '<script src="/static/app.js')
    token = _asset_token()
    for name in VERSIONED:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/teams/", response_class=HTMLResponse)
def teams_page() -> HTMLResponse:
    from . import pages
    state = store.get_season()
    odds = ()
    if store.league is not None:
        made = store.league.team_counts[:, METRIC_NAMES.index("make_playoff")]
        odds = list(zip(store.league.names, made / store.league.n_sims))
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("<!--HEAD-->", pages.teams_head(state))
                .replace(pages.INTRO_BLOCK,
                         pages.teams_body(state, state.fbs_teams, odds)))
    html = html.replace('<script src="/static/app.js',
                        '<script>window.CFBROOT_INFO = true;</script>'
                        '<script src="/static/app.js')
    token = _asset_token()
    for name in VERSIONED:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/this-week/", response_class=HTMLResponse)
def this_week_page() -> HTMLResponse:
    """The week's games by what they move, as the build writes it. The first
    request builds every team's guide, which takes a while."""
    from . import pages
    state = store.get_season()
    stakes = pages.Stakes()
    n_sims = DEFAULT_SIMS
    if store.league is not None:
        n_sims = store.league.n_sims
        for t in state.fbs_teams:
            stakes.add(t.school, store.payload(t.idx))
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("<!--HEAD-->", pages.this_week_head(state, n_sims))
                .replace(pages.INTRO_BLOCK, pages.this_week_body(state, n_sims, stakes)))
    html = html.replace('<script src="/static/app.js',
                        '<script>window.CFBROOT_INFO = true;</script>'
                        '<script src="/static/app.js')
    token = _asset_token()
    for name in VERSIONED:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _info_page(head: str, body: str) -> HTMLResponse:
    """A written page in the app's shell, as the build makes one."""
    from . import pages
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = (html.replace("<!--HEAD-->", head).replace(pages.INTRO_BLOCK, body)
                .replace('<script src="/static/app.js',
                         '<script>window.CFBROOT_INFO = true;</script>'
                         '<script src="/static/app.js'))
    token = _asset_token()
    for name in VERSIONED:
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/conference/{name}/", response_class=HTMLResponse)
def conference_page(name: str) -> HTMLResponse:
    """A conference's title race. The games that decide it come from every
    member's guide, so the first request builds those."""
    from . import pages
    state = store.get_season()
    conf = next((c for c in pages.conferences(state.fbs_teams)
                 if pages.conf_slug(c) == name), None)
    if conf is None:
        raise HTTPException(status_code=404, detail=f"unknown conference: {name}")
    league, pairs, n_sims = [], [], DEFAULT_SIMS
    stakes = pages.Stakes("make_conf_title_game")
    if store.league is not None:
        n_sims = store.league.n_sims
        league = league_all(state, store.league.for_team(state.fbs_teams[0].idx))
        pairs = pages.ccg_pairs(store.league, state).get(conf, [])
        for t in state.fbs_teams:
            if t.conference == conf:
                stakes.add(t.school, store.payload(t.idx))
    rows = pages.conference_rows(state, conf, league)
    return _info_page(pages.conference_head(state, conf, rows, n_sims, pairs),
                      pages.conference_body(state, conf, rows, n_sims, pairs, stakes))


@app.get("/cfp-rankings/", response_class=HTMLResponse)
def rankings_page() -> HTMLResponse:
    from . import pages
    from ..sim.sample import committee_now
    state = store.get_season()
    if store.inputs is None:
        store.inputs = state.kernel_inputs()
    projection = committee_now(state, store.inputs, store.weekly)
    odds, n_sims = (), DEFAULT_SIMS
    if store.league is not None:
        n_sims = store.league.n_sims
        made = store.league.team_counts[:, METRIC_NAMES.index("make_playoff")]
        odds = list(zip(store.league.names, made / n_sims))
    return _info_page(pages.rankings_head(state, projection, n_sims),
                      pages.rankings_body(state, projection, odds))


@app.get("/api/state")
def api_state():
    try:
        s = store.get_season()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(_season_payload(s))


@app.get("/api/team/{name}")
def api_team(name: str):
    """A team's rooting guide, or how far along the shared simulation is."""
    try:
        s = store.get_season()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    team = s.team_by_name(name)
    if team is None or not team.is_fbs:
        raise HTTPException(status_code=404, detail=f"unknown team: {name}")

    job = store.start_league()
    if job.status == "error":
        raise HTTPException(status_code=500, detail=job.error.split("\n")[0])
    if store.league is None:
        return JSONResponse(
            {"status": "running", "done": job.done, "total": job.total,
             "elapsed": time.time() - job.started}, status_code=202)
    return JSONResponse(store.payload(team.idx))


@app.get("/api/sample")
def api_sample(seed: int | None = None, from_start: bool = False):
    """One simulated season, kept in full, for the Sample season view."""
    try:
        s = store.get_season()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if seed is None:
        seed = secrets.randbelow(1_000_000_000)
    # Warm-up is already building these; waiting for it beats building a
    # second copy alongside it.
    store.sample_ready.wait(timeout=120)
    if store.inputs is None or store.weekly is None:
        # Both at once, and only once: a request arriving while these are
        # being built would otherwise find the inputs ready and the weekly
        # rankings missing, and come back without them.
        with store.lock:
            if store.inputs is None or store.weekly is None:
                ki = s.kernel_inputs()
                store.inputs, store.weekly = ki, weekly_systems(s, ki)
    return JSONResponse(sample_season(s, seed, from_start=from_start,
                                      ki=store.inputs, weekly=store.weekly))


@app.on_event("startup")
def _warm_up() -> None:
    """Start the shared simulation as the server comes up.

    The sample season's weekly rankings need a rating system per week, which
    takes a few seconds to lay out. They are built first, before the long run
    takes every core, so the first click does not wait on them.
    """
    def work() -> None:
        try:
            season = store.get_season()
            ki = season.kernel_inputs()
            weekly = weekly_systems(season, ki)
            with store.lock:
                store.inputs, store.weekly = ki, weekly
        except Exception:  # noqa: BLE001
            pass          # no network: the first request will say so
        finally:
            store.sample_ready.set()
        try:
            store.start_league()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=work, daemon=True, name="warm-up").start()


def serve(host: str = "127.0.0.1", port: int = 8000, year: int | None = None,
          reload: bool = False) -> None:
    import uvicorn
    if year:
        store.year = year
    uvicorn.run(app, host=host, port=port, log_level="warning")
