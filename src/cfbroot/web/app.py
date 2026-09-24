"""Local web app: the rooting guide, and a sample season played out."""

from __future__ import annotations

import os
import secrets
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import DEFAULT_METRICS, METRIC_LABELS, METRIC_NAMES, SimConfig
from ..data.loader import default_year, load_season
from ..data.season import SeasonState
from ..model import provenance as model_provenance
from ..sim import build_guide, league_all, run_league
from ..sim.sample import sample_season, weekly_systems
from .pages import slug

HERE = Path(__file__).resolve().parent

# One run of this size serves every team, so the app does it once at
# start-up. It puts the error on a playoff probability at about 0.025
# percentage points.
DEFAULT_SIMS = int(os.environ.get("CFBROOT_SIMS") or 2_500_000)
SEED = 12345

app = FastAPI(title="cfbroot", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


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
        self.league = None            # LeagueResults, shared by every team
        self.job: Job | None = None
        self.payloads: dict[int, dict] = {}
        self.inputs = None            # kernel inputs, reused by sample seasons
        self.weekly = None            # a rating system per committee poll week
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
                job.status = "done"
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = f"{exc}\n{traceback.format_exc()}"

        threading.Thread(target=work, daemon=True, name="league").start()
        return job

    def payload(self, team_idx: int) -> dict:
        """One team's guide, built on first request and kept."""
        if team_idx not in self.payloads:
            s = self.season
            res = self.league.for_team(team_idx)
            # Score every remaining game for every metric. Week and metric are
            # filters the client applies without asking again.
            guide = build_guide(s, res, primary="make_playoff", week=None)
            self.payloads[team_idx] = _guide_payload(guide, res, s)
        return self.payloads[team_idx]


store = Store()


# JSON helpers

# What the page reads out of a game. The rest of what the guide works out,
# the p-values and the counts behind them, never reaches the screen, and
# sending it costs more than everything else on the page put together.
GAME_KEYS = ("home", "away", "home_idx", "away_idx", "week", "neutral",
             "p_home_win", "start_date", "broadcast")
SWING_KEYS = ("p_if_home", "p_if_away", "delta", "lo", "hi", "home",
              "sig_week", "sig_all", "reliable")


def _slim(games):
    out = []
    for g in games:
        row = {k: g[k] for k in GAME_KEYS if k in g}
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
             "start": g.get("start_date"), "tv": g.get("broadcast", "")}
            for g in sorted(s.games, key=lambda g: g["week"])
            if g["status"] == 0 and not g["is_ccg"]
        ],
        # Results so far, for the game list shown when hovering over a team.
        "played": [
            {"week": g["week"], "home": g["home_idx"], "away": g["away_idx"],
             "home_points": g["home_points"], "away_points": g["away_points"],
             "neutral": g["neutral"], "title_game": g["is_ccg"]}
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
    })


# Routes

def _asset_token() -> str:
    """Changes when the CSS or JS changes, so browsers don't use a stale copy."""
    stamps = []
    for name in ("static/app.js", "static/season.js", "static/app.css"):
        f = HERE / name
        stamps.append(str(int(f.stat().st_mtime)) if f.exists() else "0")
    return "-".join(stamps)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = html.replace("<!--HEAD-->", "<title>CFB Rooting Guide</title>")
    token = _asset_token()
    for name in ("app.css", "app.js", "season.js"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
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
    for name in ("app.css", "app.js", "season.js"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={token}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


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
