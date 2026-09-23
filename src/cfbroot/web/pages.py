"""Static pages for the exported site.

Every team gets its own address, so a search engine has something to index
and a link to a team lands on that team. The app takes over once its script
runs; what is written here is what a crawler and a cold visitor see first.
"""

from __future__ import annotations

import html
import os
import re
import unicodedata
from datetime import datetime, timezone

SITE_NAME = "CFB Rooting Guide"
SITE_URL = os.environ.get(
    "CFBROOT_SITE_URL", "https://goodsellkai.github.io/cfb-rooting").rstrip("/")


def slug(school: str) -> str:
    """A team's piece of the address: "Texas A&M" becomes texas-am."""
    s = unicodedata.normalize("NFKD", school).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "team"


def _sims(n: int) -> str:
    return f"{n / 1e6:g} million" if n >= 1_000_000 else f"{n:,}"


def _pct(x, digits=1):
    return "-" if x is None else f"{100 * float(x):.{digits}f}%"


def _head(title, description, url, extra=""):
    t, d = html.escape(title), html.escape(description)
    return f"""<title>{t}</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary_large_image">{extra}"""


def home_head(state, n_sims):
    week = state.current_week()
    desc = (f"Which games this week help your team reach the College Football "
            f"Playoff. Every remaining {state.year} game scored on "
            f"{_sims(n_sims)} simulated seasons, updated through week {week}.")
    return _head(f"{SITE_NAME}: who to root for this week", desc, SITE_URL + "/")


def team_head(state, team, payload):
    week = state.current_week()
    p = _pct(payload["headline"]["make_playoff"]["p"])
    title = f"{team.school} playoff odds and who to root for | {SITE_NAME}"
    desc = (f"{team.school} makes the College Football Playoff in {p} of "
            f"{_sims(payload['n_sims'])} simulated {state.year} seasons. See "
            f"which week {week} games move those odds most, and by how much.")
    return _head(title, desc, f"{SITE_URL}/team/{slug(team.school)}/")


def home_body(state, teams, n_sims):
    week = state.current_week()
    links = "\n".join(
        f'<li><a href="team/{slug(t.school)}/">{html.escape(t.school)}</a></li>'
        for t in teams)
    return f"""<h1>Who should your team root for?</h1>
<p>Pick a team and every remaining game of the {state.year} season is scored by
how much each result moves that team's odds of reaching the College Football
Playoff, winning its conference, or winning the national title. The numbers
come from simulating the rest of the season {_sims(n_sims)} times, through
week {week}.</p>
<h2>Every team</h2>
<ul class="teamlinks">
{links}
</ul>"""


def team_body(state, team, payload, week):
    h = payload["headline"]
    games = [g for g in payload["games"]
             if g["week"] == week and g["swings"]["make_playoff"]["sig_week"]]
    games.sort(key=lambda g: -abs(g["swings"]["make_playoff"]["delta"]))
    rows = ""
    for g in games[:5]:
        s = g["swings"]["make_playoff"]
        root = g["home"] if s["home"] else g["away"]
        other = g["away"] if s["home"] else g["home"]
        rows += (f"<li>Root for {html.escape(root)} over "
                 f"{html.escape(other)}, worth {_pct(abs(s['delta']), 2)}</li>\n")
    rest = ("<ul>\n" + rows + "</ul>") if rows else (
        "<p>No game this week moves the odds enough to call.</p>")
    return f"""<h1>Who should {html.escape(team.school)} fans root for?</h1>
<p>{html.escape(team.school)} reaches the College Football Playoff in
{_pct(h["make_playoff"]["p"])} of {_sims(payload["n_sims"])} simulated
{state.year} seasons,
wins the {html.escape(team.conference or "conference")} in
{_pct(h["win_conference"]["p"])} and the national title in
{_pct(h["win_national_title"]["p"], 2)}, with {payload["expected_wins"]:.1f}
wins expected.</p>
<h2>Week {week}</h2>
{rest}
<p><a href="../../">All teams</a></p>"""


def sitemap(teams) -> str:
    day = datetime.now(timezone.utc).date().isoformat()
    urls = [SITE_URL + "/"] + [f"{SITE_URL}/team/{slug(t.school)}/" for t in teams]
    body = "\n".join(
        f"  <url><loc>{u}</loc><lastmod>{day}</lastmod></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


def robots() -> str:
    # The data files are for the page, not for crawlers: they are hundreds of
    # megabytes and hold nothing a search result would show.
    return ("User-agent: *\n"
            "Allow: /\n"
            "Disallow: /data/\n"
            f"Sitemap: {SITE_URL}/sitemap.xml\n")
