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

from ..config import (DEFAULT_METRICS, METRIC_LABELS, METRIC_NAMES, ModelParams,
                      SimConfig, has_api_key)
from ..data.loader import default_year, load_season
from ..data.season import SeasonState
from ..model import provenance as model_provenance
from ..sim import build_guide, league_all, run

HERE = Path(__file__).resolve().parent

app = FastAPI(title="cfbroot", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ---------------------------------------------------------------------------
# server state
# ---------------------------------------------------------------------------

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
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.season: SeasonState | None = None
        self.year: int = default_year()
        self.loaded_at: float = 0.0
        self.jobs: dict[str, Job] = {}
        self.last_results = None
        self.last_team: str | None = None

    def get_season(self, *, force: bool = False, live: bool = False) -> SeasonState:
        with self.lock:
            if self.season is None or force:
                self.season = load_season(self.year, force=force, live=live)
                self.loaded_at = time.time()
            return self.season


store = Store()


# ---------------------------------------------------------------------------
# serialisation helpers
# ---------------------------------------------------------------------------

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
        # Every team the schedule can reference, non-FBS opponents included,
        # so the client can render a logo for either side of any game.
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


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def _asset_token() -> str:
    """A token that changes whenever the CSS or JS changes.

    Without this the browser happily keeps a cached app.js across an update,
    and the stale copy then breaks against a changed API payload.
    """
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
    """Re-pull scores and ratings. This is the after-a-day-of-games button."""
    try:
        s = store.get_season(force=True, live=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(_season_payload(s))


class RunRequest(BaseModel):
    team: str
    n_sims: int = 200_000
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

    def work() -> None:
        try:
            cfg = SimConfig(n_sims=n_sims, batch_size=min(25_000, n_sims),
                            seed=req.seed)

            def progress(done: int, total: int) -> None:
                job.done = done
                job.total = total

            res = run(s, team.idx, cfg, progress=progress)
            # Score every remaining game for every metric. Week and metric are
            # both display filters over this one result, so neither costs a
            # second simulation.
            guide = build_guide(s, res, primary=req.primary, week=None)
            store.last_results = res
            store.last_team = team.school
            job.result = _guide_payload(guide, res, s)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{exc}\n{traceback.format_exc()}"
        finally:
            job.finished = time.time()

    threading.Thread(target=work, daemon=True, name=f"sim-{job.id}").start()
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


def serve(host: str = "127.0.0.1", port: int = 8000, year: int | None = None,
          reload: bool = False) -> None:
    import uvicorn
    if year:
        store.year = year
    uvicorn.run(app, host=host, port=port, log_level="warning")
