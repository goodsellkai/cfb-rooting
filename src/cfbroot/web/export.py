"""Write the whole app out as static files, for hosting without a server.

The simulation does not depend on which team is picked, so every team's guide
can be built ahead of time. The page then reads plain JSON files instead of
asking the server, and any static host can serve it.

    site/
      index.html
      static/app.css, static/app.js, static/season.js
      data/state.json            the season, as /api/state returns it
      data/team/<idx>.json       one team's guide, as /api/team/<name> does
      data/samples/<k>.json      played-out seasons for the Sample season view,
                                 since a static host cannot simulate one on click
      data/samples_start/<k>.json  the same, replayed from week 1
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from ..config import SimConfig, api_key
from ..sim import run_league
from ..sim.sample import sample_season
from .app import DEFAULT_SIMS, HERE, SEED, _season_payload, store


def _write(path: Path, obj) -> int:
    data = json.dumps(obj, separators=(",", ":"), allow_nan=False)
    path.write_text(data, encoding="utf-8")
    return len(data)


N_SAMPLES = 20


def export(out: Path, year: int | None = None, n_sims: int = DEFAULT_SIMS,
           n_samples: int = N_SAMPLES, log=print) -> None:
    api_key()                 # a public site must not fall back to fake data
    if year:
        store.year = year
    state = store.get_season()
    log(f"{state.year} week {state.current_week()}: "
        f"{len(state.remaining_games)} games to simulate")

    t0 = time.perf_counter()

    def progress(done: int, total: int) -> None:
        log(f"  {done:>9,} / {total:,} seasons  "
            f"{time.perf_counter() - t0:6.1f}s")

    store.league = run_league(
        state, SimConfig(n_sims=n_sims, batch_size=50_000, seed=SEED),
        progress=progress)

    if out.exists():
        shutil.rmtree(out)
    (out / "data" / "team").mkdir(parents=True)
    (out / "data" / "samples").mkdir()
    (out / "data" / "samples_start").mkdir()
    shutil.copytree(HERE / "static", out / "static")

    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    html = (html.replace('href="/static/', 'href="static/')
                .replace('<script src="/static/app.js">',
                         '<script>window.CFBROOT_STATIC = true;</script>\n'
                         '<script src="/static/app.js">')
                .replace('src="/static/', 'src="static/'))
    (out / "index.html").write_text(html, encoding="utf-8")

    size = _write(out / "data" / "state.json", _season_payload(state))
    teams = state.fbs_teams
    for i, t in enumerate(teams, 1):
        size += _write(out / "data" / "team" / f"{t.idx}.json",
                       store.payload(t.idx))
        store.payloads.clear()          # keep memory flat
        if i % 25 == 0 or i == len(teams):
            log(f"  wrote {i} / {len(teams)} teams")
    for k in range(n_samples):
        size += _write(out / "data" / "samples" / f"{k}.json",
                       sample_season(state, seed=k + 1))
        size += _write(out / "data" / "samples_start" / f"{k}.json",
                       sample_season(state, seed=k + 1, from_start=True))
    size += _write(out / "data" / "samples.json", {"count": n_samples})
    log(f"  wrote {n_samples} sample seasons")
    log(f"{out}: {size / 1e6:.0f} MB in {time.perf_counter() - t0:.0f}s")
