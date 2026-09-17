"""Local web app: pick your team, run the sims, read the rooting guide."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import (DEFAULT_METRICS, METRIC_LABELS, METRIC_NAMES, SimConfig,
                      has_api_key)
from ..data.loader import default_year, load_season
from ..data.season import SeasonState
from ..model import provenance as model_provenance
from ..sim import build_guide, league_all, run_league

HERE = Path(__file__).resolve().parent

# One run of this size serves every team, so the app does it once at start-up
# rather than per team. A million seasons puts the error on a playoff
# probability at about four hundredths of a percentage point.
DEFAULT_SIMS = 1_000_000

app = FastAPI(title="cfbroot", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# Server state

@dataclass
class Job:
    id: str
    team: str
    n_sims: int
    status: str = "running"       # running | done | error
    done: int = 0
    total: int = 0
    started: float = field(default_factory=time.time)
    finished: float | None = None
    result: dict | None = None
    error: str | None = None


class Store:
    """The season, and the one set of simulations that serves every team.

    Which team is being asked about never changed how a season played out, so
    the simulation is run once and each team is a slice of it. It only has to
    be redone when the data changes or the count does.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.season: SeasonState | None = None
        self.year: int = default_year()
        self.loaded_at: float = 0.0
        self.jobs: dict[str, Job] = {}
        self.league = None            # LeagueResults, shared by every team
        self.league_sims: int = 0
        self.league_seed: int = 0
        self.league_job: Job | None = None

    def get_season(self, *, force: bool = False, live: bool = False) -> SeasonState:
        with self.lock:
            if self.season is None or force:
                self.season = load_season(self.year, force=force, live=live)
                self.loaded_at = time.time()
                self.league = None
                self.league_job = None
            return self.season

    def usable(self, n_sims: int, seed: int) -> bool:
        return (self.league is not None and self.league_sims == n_sims
                and self.league_seed == seed)

    def start_league(self, state: SeasonState, n_sims: int, seed: int) -> Job:
        """Kick off the shared simulation, or hand back the one already running."""
        with self.lock:
            job = self.league_job
            if job is not None and job.status == "running"                     and job.n_sims == n_sims:
                return job
            job = Job(id=uuid.uuid4().hex[:12], team="all teams",
                      n_sims=n_sims, total=n_sims)
            self.jobs[job.id] = job
            self.league_job = job

        def work() -> None:
            try:
                cfg = SimConfig(n_sims=n_sims,
                                batch_size=min(50_000, n_sims), seed=seed)

                def progress(done: int, total: int) -> None:
                    job.done = done
                    job.total = total

                league = run_league(state, cfg, progress=progress)
                with self.lock:
                    self.league = league
                    self.league_sims = n_sims
                    self.league_seed = seed
                job.status = "done"
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = f"{exc}\n{traceback.format_exc()}"
            finally:
                job.finished = time.time()

        threading.Thread(target=work, daemon=True, name=f"sim-{job.id}").start()
        return job


store = Store()


# JSON helpers

def _clean(obj):
    """Make numpy scalars and NaNs safe for JSON."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not np.isfinite(f) else f
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
        "has_api_key": has_api_key(),
        "loaded_at": store.loaded_at,
        "params": s.params.to_dict(),
        "metrics": [{"key": m, "label": METRIC_LABELS[m]} for m in METRIC_NAMES],
        "default_metrics": DEFAULT_METRICS,
        "teams": [
            {"idx": t.idx, "name": t.school, "conference": t.conference,
             "rating": t.rating, "abbr": t.abbreviation}
            for t in sorted(s.fbs_teams, key=lambda t: t.school)
        ],
        # All teams, including non-FBS opponents, so every game can show logos.
        "team_index": {
            str(t.idx): {"name": t.school, "abbr": t.abbreviation,
                         "logo": t.logo, "color": t.color,
                         "conference": t.conference, "fbs": t.is_fbs}
            for t in s.teams
        },
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
        "own_games": [g.as_dict() for g in guide.own_games],
        "games": [g.as_dict() for g in guide.games],
        "league": league_all(s, res),
    })


# Routes

def _asset_token() -> str:
    """Changes when the CSS or JS changes, so browsers don't use a stale copy."""
    stamps = []
    for name in ("static/app.js", "static/app.css"):
        f = HERE / name
        stamps.append(str(int(f.stat().st_mtime)) if f.exists() else "0")
    return "-".join(stamps)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    token = _asset_token()
    html = (html.replace("/static/app.css", f"/static/app.css?v={token}")
                .replace("/static/app.js", f"/static/app.js?v={token}"))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def api_state():
    try:
        s = store.get_season()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(_season_payload(s))


@app.post("/api/refresh")
def api_refresh():
    """Re-pull scores and ratings."""
    try:
        s = store.get_season(force=True, live=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(_season_payload(s))


class RunRequest(BaseModel):
    team: str
    n_sims: int = DEFAULT_SIMS
    week: int | None = None
    primary: str = "make_playoff"
    metrics: list[str] | None = None
    league_metric: str = "make_playoff"
    seed: int = 12345
    all_weeks: bool = False


@app.post("/api/run")
def api_run(req: RunRequest):
    try:
        s = store.get_season()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    team = s.team_by_name(req.team)
    if team is None:
        raise HTTPException(status_code=404, detail=f"unknown team: {req.team}")
    n_sims = int(np.clip(req.n_sims, 1_000, 5_000_000))

    job = Job(id=uuid.uuid4().hex[:12], team=team.school, n_sims=n_sims,
              total=n_sims)
    store.jobs[job.id] = job

    def serve_from(league) -> None:
        res = league.for_team(team.idx)
        # Score every remaining game for every metric. Week and metric are
        # filters the client applies without asking again.
        guide = build_guide(s, res, primary=req.primary, week=None)
        job.result = _guide_payload(guide, res, s)
        job.done = job.total
        job.status = "done"
        job.finished = time.time()

    # The simulations do not depend on the team, so if a run of this size is
    # already in hand this is just a slice of it.
    if store.usable(n_sims, req.seed):
        serve_from(store.league)
        return {"job_id": job.id}

    league_job = store.start_league(s, n_sims, req.seed)

    def wait() -> None:
        try:
            while league_job.status == "running":
                job.done, job.total = league_job.done, league_job.total
                time.sleep(0.1)
            if league_job.status == "error":
                job.status = "error"
                job.error = league_job.error
                job.finished = time.time()
                return
            serve_from(store.league)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{exc}\n{traceback.format_exc()}"
            job.finished = time.time()

    threading.Thread(target=wait, daemon=True, name=f"serve-{job.id}").start()
    return {"job_id": job.id}


@app.get("/api/job/{job_id}")
def api_job(job_id: str):
    job = store.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    payload = {
        "id": job.id, "status": job.status, "team": job.team,
        "done": job.done, "total": job.total,
        "elapsed": (job.finished or time.time()) - job.started,
    }
    if job.status == "done":
        payload["result"] = job.result
    if job.status == "error":
        payload["error"] = job.error
    return JSONResponse(_clean(payload))


@app.on_event("startup")
def _warm_up() -> None:
    """Start the shared simulation as the server comes up.

    It serves every team, so doing it now means the first team picked is
    already waiting rather than starting a run.
    """
    def work() -> None:
        try:
            store.start_league(store.get_season(), DEFAULT_SIMS, 12345)
        except Exception:  # noqa: BLE001
            pass          # no key, no network: the first request will say so

    threading.Thread(target=work, daemon=True, name="warm-up").start()


def serve(host: str = "127.0.0.1", port: int = 8000, year: int | None = None,
          reload: bool = False) -> None:
    import uvicorn
    if year:
        store.year = year
    uvicorn.run(app, host=host, port=port, log_level="warning")
