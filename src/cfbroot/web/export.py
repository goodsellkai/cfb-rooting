"""Write the whole app out as static files, for hosting without a server.

The simulation does not depend on which team is picked, so every team's guide
can be built ahead of time. The page then reads plain JSON files instead of
asking the server, and any static host can serve it.

    site/
      index.html
      static/app.css, static/app.js, static/season.js
      data/state.json            the season, as /api/state returns it
      data/team/<slug>.json      one team's guide, as /api/team/<name> does
      data/season/<n>.json       a simulated season, as /api/sample does
      team/<school>/index.html   that team's page, which the app then takes over
      conference/<name>/index.html   each conference's title race
      cfp-rankings/index.html    the committee's ranking as the model expects it
      card/<name>-<time>.jpg     the picture a shared link shows
      sitemap.xml, robots.txt, _headers
"""

from __future__ import annotations

import datetime as dt
import html as htmlesc
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from ..config import METRIC_NAMES, SimConfig
from ..sim import run_league
from ..sim.sample import committee_now, sample_season, weekly_systems
from . import cards, pages
from .app import (DEFAULT_SIMS, HERE, SEED, VERSIONED, _clean,
                  _season_payload, store)

# Seasons written out for the Sim a season tab, which has no server to make
# one on demand. This many carrying the season's results, and this many
# replayed from week 1.
SAMPLE_SEASONS = int(os.environ.get("CFBROOT_SAMPLES") or 200)

# Every file written here carries this, so a page left open across a rebuild
# can tell that the numbering it holds is no longer the one being served.
BUILD = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")

INTRO_BLOCK = pages.INTRO_BLOCK


def _band_style(color: str | None, photo: int = 3) -> str:
    """A team page's band in its colours and photo, by the same rules app.js
    uses, so the page does not open green and then change."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color or "")
    if not m:
        return f' data-photo="{photo}"'
    hexa = m.group(1)

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(hexa[i:i + 2], 16) / 255) for i in (0, 2, 4))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    light = 1.05 / (lum + 0.05) < (lum + 0.05) / 0.0587
    fg, mark = ("#0f1712", "#0f1712") if light else ("#ffffff", "var(--mark)")
    blend = "screen" if light else "multiply"
    return (f' data-photo="{photo}" style="--team:#{hexa};--team-fg:{fg};'
            f'--team-mark:{mark};--photo-blend:{blend}"')


def _team_tint(color: str | None) -> tuple[float, float] | None:
    """The hue and strength the page takes from a team's colour, in OKLCH:
    a tenth of its chroma, capped, and less for golds and yellows. The same
    sum as teamTint() in app.js."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color or "")
    if not m:
        return None

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(int(m.group(1)[i:i + 2], 16) / 255) for i in (0, 2, 4))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    mm = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    a = 1.9779984951 * l - 2.4285922050 * mm + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * mm - 0.8086757660 * s
    h = math.degrees(math.atan2(bb, a)) % 360
    c = min(math.hypot(a, bb) * 0.11, 0.013)
    if 65 <= h <= 115:
        c *= 0.55
    return h, c


def _write(path: Path, obj) -> int:
    data = json.dumps(obj, separators=(",", ":"), allow_nan=False)
    path.write_text(data, encoding="utf-8")
    return len(data)


def load_template(build: str) -> str:
    """The page shell, with the assets tagged so a browser fetches the new ones.

    A republish passes a fresh stamp even though the numbers have not moved:
    the stylesheet and script are what changed, and a held copy of either is
    what a visitor would otherwise keep seeing.
    """
    template = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    for name in VERSIONED:
        template = template.replace(f'/static/{name}"',
                                    f'/static/{name}?v={build}"')
    return template


def render(template: str, head: str, intro: str, depth: int,
           team: str | None = None, info: bool = False, writeup: str = "",
           color: str | None = None, photo: int = 3,
           base: str | None = None) -> str:
    """The app's page, with what a crawler reads written in. ``base`` replaces
    the relative path ``depth`` gives, for a page served at any depth."""
    base = "../" * depth if base is None else base
    boot = f'<script>window.CFBROOT_STATIC = true; window.CFBROOT_BASE = "{base}";'
    boot += f' window.CFBROOT_TEAM = "{team}";' if team else ""
    boot += " window.CFBROOT_INFO = true;" if info else ""
    boot += "</script>\n"
    # A team page names its team before the script runs.
    picker = 'placeholder="Pick your team"'
    if team:
        picker = f'value="{htmlesc.escape(team)}" {picker}'
    # The page's tint too, so it does not shift once the script runs.
    tint = _team_tint(color)
    root = (f'<html lang="en" class="tinted" style="--team-h:{tint[0]:.1f};'
            f'--team-c:{tint[1]:.4f}">' if tint else '<html lang="en">')
    html = (template.replace("<!--HEAD-->", head)
                    .replace('<html lang="en">', root)
                    .replace('placeholder="Pick your team"', picker)
                    .replace('<section id="band" class="band home" data-photo="3">',
                             '<section id="band" class="band"'
                             + _band_style(color, photo) + '>')
                    .replace("<!--WRITEUP-->", writeup)
                    .replace(INTRO_BLOCK, intro)
                    .replace('<script src="/static/app.js', boot + '<script src="/static/app.js')
                    .replace('href="/how-it-works/", ', f'href="{base}how-it-works/", ')
                    .replace('href="/how-it-works/"', f'href="{base}how-it-works/"')
                    .replace('href="/teams/"', f'href="{base}teams/"')
                    .replace('href="/this-week/"', f'href="{base}this-week/"')
                    .replace('href="/cfp-rankings/"', f'href="{base}cfp-rankings/"')
                    .replace('class="brand" href="/"',
                             f'class="brand" href="{base or "./"}"')
                    .replace('href="/static/', f'href="{base}static/')
                    .replace('src="/static/', f'src="{base}static/'))
    return html


def write_meta(out: Path, state, teams, n_sims: int, template: str) -> None:
    """Everything that is not a page and not a number."""
    (out / "robots.txt").write_text(pages.robots(), encoding="utf-8")
    (out / "llms.txt").write_text(
        pages.llms_txt(state, teams, n_sims), encoding="utf-8")
    (out / "_headers").write_text(pages.headers(), encoding="utf-8")
    shutil.copy(HERE / "static" / "favicon.ico", out / "favicon.ico")
    # Served for any address at any depth, so its paths start at the root
    # rather than at itself: relative ones broke the styles and the script
    # one folder down.
    root = urlparse(pages.SITE_URL).path.rstrip("/") + "/"
    (out / "404.html").write_text(
        render(template, pages.not_found_head(), pages.not_found(root), 0,
               info=True, base=root),
        encoding="utf-8")


def write_front(out: Path, state, teams, n_sims: int, standing,
                template: str, card: str | None = None, stakes=None,
                league=None, projection=None, pairs=None, conf_stakes=None,
                cards=None) -> None:
    """The home page, the week's biggest games, the conference races, the
    projected rankings, the full table, how it works and the sitemap.
    ``stakes`` and ``conf_stakes`` are gathered from every team's guide, so
    these go out after the team pages. ``league`` is any guide's table of
    every team, ``cards`` each page's share card by address."""
    (out / "index.html").write_text(
        render(template, pages.home_head(state, n_sims, standing[:10], card),
               pages.home_body(state, teams, n_sims,
                               standing[:10], standing[10:18], stakes,
                               league), 0),
        encoding="utf-8")
    week = out / "this-week"
    week.mkdir(exist_ok=True)
    (week / "index.html").write_text(
        render(template, pages.this_week_head(state, n_sims, card),
               pages.this_week_body(state, n_sims, stakes), 1, info=True),
        encoding="utf-8")
    everyone = out / "teams"
    everyone.mkdir(exist_ok=True)
    (everyone / "index.html").write_text(
        render(template, pages.teams_head(state, n_sims, card),
               pages.teams_body(state, teams, standing), 1, info=True),
        encoding="utf-8")
    how = out / "how-it-works"
    how.mkdir(exist_ok=True)
    (how / "index.html").write_text(
        render(template, pages.how_head(state, n_sims, card),
               pages.how_body(state, n_sims), 1, info=True),
        encoding="utf-8")
    ranked = out / "cfp-rankings"
    ranked.mkdir(exist_ok=True)
    (ranked / "index.html").write_text(
        render(template, pages.rankings_head(state, projection, n_sims, card),
               pages.rankings_body(state, projection, standing), 1, info=True),
        encoding="utf-8")
    for conf in pages.conferences(teams):
        rows = pages.conference_rows(state, conf, league)
        if not rows:
            continue
        where = out / "conference" / pages.conf_slug(conf)
        where.mkdir(parents=True, exist_ok=True)
        mine = (pairs or {}).get(conf) or []
        (where / "index.html").write_text(
            render(template,
                   pages.conference_head(state, conf, rows, n_sims, mine, card),
                   pages.conference_body(state, conf, rows, n_sims, mine,
                                         (conf_stakes or {}).get(conf)),
                   2, info=True),
            encoding="utf-8")
    cards = dict(cards or {})
    if card:
        cards.setdefault(pages.SITE_URL + "/", card)
    (out / "sitemap.xml").write_text(pages.sitemap(teams, cards), encoding="utf-8")


def conference_stakes(teams) -> dict:
    """A tally per conference of the title game places each game moves."""
    return {c: pages.Stakes("make_conf_title_game")
            for c in pages.conferences(teams)}


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
    # The committee's ranking on the results so far, and the weekly rating
    # systems it shares with the sample seasons.
    ki = state.kernel_inputs()
    weekly = weekly_systems(state, ki)
    projection = committee_now(state, ki, weekly)

    log(f"  valuing the week's finished games "
        f"({time.perf_counter() - t0:.0f}s in)")
    try:
        store.run_replay(state)
    except Exception as exc:          # noqa: BLE001
        log(f"  that run failed ({exc}); publishing without those numbers")

    # Every simulated season fills the bracket, so the playoff probabilities
    # have to add up to its size. A season loaded wrong shows up here rather
    # than on the site.
    made = store.league.team_counts[:, METRIC_NAMES.index("make_playoff")]
    total = made.sum() / n_sims
    if abs(total - state.params.playoff_size) > 0.01:
        raise SystemExit(f"playoff probabilities add to {total:.2f}, not "
                         f"{state.params.playoff_size}: not publishing")

    if out.exists():
        shutil.rmtree(out)
    (out / "data" / "team").mkdir(parents=True)
    shutil.copytree(HERE / "static", out / "static")

    template = load_template(str(int(time.time())))

    def page(*a, **k):
        return render(template, *a, **k)

    odds = made / n_sims
    # Name breaks a tie, so two teams with the same odds do not swap
    # places between builds, and a republish puts them in that order
    # too.
    standing = sorted(zip(store.league.names, odds),
                      key=lambda r: (-r[1], r[0]))
    shares = cards.Cards(out, state, log)
    site_card = shares.site(state.current_week(), standing)
    write_meta(out, state, state.fbs_teams, n_sims, template)
    stakes = pages.Stakes()

    payload = _season_payload(state)
    payload["sample_seasons"] = SAMPLE_SEASONS
    payload["build"] = BUILD
    # What the conference and ranking pages read that no team's guide holds,
    # so a republish can write them again.
    payload["projection"] = projection
    payload["ccg_pairs"] = _clean(pages.ccg_pairs(store.league, state))
    size = _write(out / "data" / "state.json", payload)
    teams = state.fbs_teams
    week = state.default_week() or state.current_week()
    rank_of = {idx: n for n, idx in enumerate((projection or {}).get("order") or [], 1)}
    conf_stakes = conference_stakes(teams)
    league = None
    team_cards = {}
    for i, t in enumerate(teams, 1):
        guide = store.payload(t.idx)
        guide["build"] = BUILD
        if t.idx in rank_of:
            guide["cfp_rank"] = {"week": projection["week"], "rank": rank_of[t.idx]}
        stakes.add(t.school, guide)
        if t.conference in conf_stakes:
            conf_stakes[t.conference].add(t.school, guide)
        league = league or guide["league"]
        # Named for the team rather than its number, which is only stable
        # within one build.
        size += _write(out / "data" / "team" / f"{pages.slug(t.school)}.json",
                       guide)
        team_dir = out / "team" / pages.slug(t.school)
        team_dir.mkdir(parents=True)
        card = shares.team(state, t, guide, week)
        if card:
            team_cards[f"{pages.SITE_URL}/team/{pages.slug(t.school)}/"] = card
        (team_dir / "index.html").write_text(
            page(pages.team_head(state, t, guide, week, card),
                 pages.team_body(state, t, guide, week), 2, t.school,
                 writeup=pages.team_writeup(state, t, guide, week),
                 color=t.color, photo=t.idx % 4 + 1),
            encoding="utf-8")
        store.payloads.clear()          # keep memory flat
        if i % 25 == 0 or i == len(teams):
            log(f"  wrote {i} / {len(teams)} teams")
    write_front(out, state, teams, n_sims, standing, template, site_card, stakes,
                league, projection, payload["ccg_pairs"], conf_stakes, team_cards)

    (out / "data" / "season").mkdir()
    (out / "data" / "season" / "fresh").mkdir()
    for i in range(SAMPLE_SEASONS):
        for fresh in (False, True):
            blob = sample_season(state, SEED + i, from_start=fresh, ki=ki,
                                 weekly=weekly)
            blob["build"] = BUILD
            name = f"fresh/{i}.json" if fresh else f"{i}.json"
            size += _write(out / "data" / "season" / name, blob)
        if (i + 1) % 50 == 0:
            log(f"  wrote {i + 1} / {SAMPLE_SEASONS} simulated seasons")
    log(f"{out}: {size / 1e6:.0f} MB in {time.perf_counter() - t0:.0f}s")
