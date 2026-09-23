"""Write the whole app out as static files, for hosting without a server.

The simulation does not depend on which team is picked, so every team's guide
can be built ahead of time. The page then reads plain JSON files instead of
asking the server, and any static host can serve it.

    site/
      index.html
      static/app.css, static/app.js, static/season.js
      data/state.json            the season, as /api/state returns it
      data/team/<idx>.json       one team's guide, as /api/team/<name> does
      data/season/<n>.json       a simulated season, as /api/sample does
      team/<school>/index.html   that team's page, which the app then takes over
      sitemap.xml, robots.txt, _headers
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from ..config import SimConfig
from ..sim import run_league
from ..sim.sample import sample_season, weekly_systems
from . import pages
from .app import DEFAULT_SIMS, HERE, SEED, _season_payload, store

# Seasons written out for the Sim a season tab, which has no server to make
# one on demand.
SAMPLE_SEASONS = int(os.environ.get("CFBROOT_SAMPLES") or 200)

# What each page shows before its script runs.
INTRO_BLOCK = "<!--INTRO--><h2>Choose a team</h2><!--/INTRO-->"


def _write(path: Path, obj) -> int:
    data = json.dumps(obj, separators=(",", ":"), allow_nan=False)
    path.write_text(data, encoding="utf-8")
    return len(data)


def export(out: Path, year: int | None = None, n_sims: int = DEFAULT_SIMS,
           log=print) -> None:
    if year:
        store.year = year
    state = store.get_season()
    if state.source == "demo":
        raise SystemExit("No real season could be loaded; a public site must "
                         "not fall back to fake data.")
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
    shutil.copytree(HERE / "static", out / "static")

    template = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    # Tag the assets with the build, so a browser holding last build's copy
    # of the script or stylesheet fetches the new one.
    build = str(int(time.time()))
    for name in ("app.css", "app.js", "season.js"):
        template = template.replace(f'/static/{name}"', f'/static/{name}?v={build}"')

    def page(head: str, intro: str, depth: int, team: str | None = None) -> str:
        """The app's page, with what a crawler reads written in."""
        base = "../" * depth
        boot = f'<script>window.CFBROOT_STATIC = true; window.CFBROOT_BASE = "{base}";'
        boot += f' window.CFBROOT_TEAM = "{team}";' if team else ""
        boot += "</script>\n"
        html = (template.replace("<!--HEAD-->", head)
                        .replace(INTRO_BLOCK, intro)
                        .replace('<script src="/static/app.js', boot + '<script src="/static/app.js')
                        .replace('href="/static/', f'href="{base}static/')
                        .replace('src="/static/', f'src="{base}static/'))
        return html

    (out / "index.html").write_text(
        page(pages.home_head(state, n_sims),
             pages.home_body(state, state.fbs_teams, n_sims), 0),
        encoding="utf-8")
    (out / "robots.txt").write_text(pages.robots(), encoding="utf-8")
    (out / "sitemap.xml").write_text(pages.sitemap(state.fbs_teams), encoding="utf-8")

    payload = _season_payload(state)
    payload["sample_seasons"] = SAMPLE_SEASONS
    size = _write(out / "data" / "state.json", payload)
    teams = state.fbs_teams
    week = state.default_week() or state.current_week()
    for i, t in enumerate(teams, 1):
        guide = store.payload(t.idx)
        size += _write(out / "data" / "team" / f"{t.idx}.json", guide)
        team_dir = out / "team" / pages.slug(t.school)
        team_dir.mkdir(parents=True)
        (team_dir / "index.html").write_text(
            page(pages.team_head(state, t, guide),
                 pages.team_body(state, t, guide, week), 2, t.school),
            encoding="utf-8")
        store.payloads.clear()          # keep memory flat
        if i % 25 == 0 or i == len(teams):
            log(f"  wrote {i} / {len(teams)} teams")

    (out / "data" / "season").mkdir()
    ki = state.kernel_inputs()
    weekly = weekly_systems(state, ki)
    for i in range(SAMPLE_SEASONS):
        size += _write(out / "data" / "season" / f"{i}.json",
                       sample_season(state, SEED + i, ki=ki, weekly=weekly))
        if (i + 1) % 50 == 0:
            log(f"  wrote {i + 1} / {SAMPLE_SEASONS} simulated seasons")
    log(f"{out}: {size / 1e6:.0f} MB in {time.perf_counter() - t0:.0f}s")
