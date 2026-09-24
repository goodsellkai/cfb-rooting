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
# What a page shows before its script runs, which the intro replaces.
INTRO_BLOCK = "<!--INTRO--><h2>Choose a team</h2><!--/INTRO-->"
# An unset variable arrives as an empty string from the build, which would
# leave every address relative: legal for a link, useless in a sitemap.
SITE_URL = (os.environ.get("CFBROOT_SITE_URL")
            or "https://goodsellkai.github.io/cfb-rooting").rstrip("/")


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
    card = f"{SITE_URL}/static/header/1.jpg"
    return f"""<title>{t}</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{card}">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="720">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{card}">
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"WebSite","name":"{SITE_NAME}",
"url":"{SITE_URL}/","description":"{d}"}}
</script>{extra}"""


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
<h2>How it works</h2>
<p>Every unplayed game is simulated from ESPN's FPI, and each simulated
season is rated with Massey's model, ranked the way the selection committee
ranks, and run through the playoff's bid rules and each conference's
tiebreakers. Splitting those seasons by who won a given game is what tells
you the game is worth, say, four points of playoff odds.
<a href="how-it-works/">The longer version</a>.</p>
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


def how_head(state, n_sims):
    desc = (f"How the rooting guide works: {_sims(n_sims)} simulated seasons, "
            f"a rating fitted to every one of them, a model of the selection "
            f"committee, and a test for which swings are real.")
    return _head(f"How this works | {SITE_NAME}", desc, f"{SITE_URL}/how-it-works/")


def how_body(state, n_sims):
    return f"""<h1>How this works</h1>
<p>The short version: the rest of the {state.year} season is played out
{_sims(n_sims)} times, and your team's odds are counted in the seasons where
one game went one way against the seasons where it went the other. The
difference is what that game is worth to you.</p>

<h2>Playing out a season</h2>
<p>Every game still to come gets a score. The margin is drawn around what
ESPN's FPI says the gap between the teams is, plus home field, with the
spread of real results around that prediction: about 14 points. Ratings are
not exactly right, and their errors last, so each simulated season also draws
one error per team and keeps it all year. A team the ratings overrate is
overrated in September and in November, which is what makes a whole season
plausible rather than a string of independent coin flips.</p>

<h2>Rating what happened</h2>
<p>A simulated season is a full set of results, so it gets rated from scratch
the way the real one would be, with Kenneth Massey's model. Two stages: a
maximum likelihood fit that asks which set of ratings makes the scoreboard
most likely, then a Bayesian correction that reads each team's wins and
losses against the quality of who it played. Margin counts, but less than the
scoreboard suggests, because the committee does not reward blowouts the way a
pure margin model does.</p>

<h2>Ranking, championships and the bracket</h2>
<p>A rating is not a ranking. On top of it the model adds the committee's own
variability, a bump for beating good teams and for losing only to good ones,
a head-to-head rule, and the boost a conference champion gets. Conference
races are then settled by each league's own published tiebreakers, the
champions are crowned, and the playoff field is picked under this season's
rules: the four power conference champions, the best team from the other six
conferences, Notre Dame if it is ranked in the top 12, and at-large bids for
the rest. The bracket is played out, so "win the national title" means
winning four games, not being ranked first.</p>

<h2>Turning that into a rooting guide</h2>
<p>Because every unplayed game is simulated independently, one run answers
every question. Split the seasons by who won a given game and compare your
team's odds across the two halves, and you have that game's effect without
simulating anything twice. Both halves share every other game's outcome, so
the comparison is cleaner than running two separate simulations would be.</p>

<h2>Why some games are marked as not significant</h2>
<p>A guide compares hundreds of games at once, and at that many comparisons
some differences look real by chance alone. Each difference gets a confidence
interval and a p-value, and the whole set goes through a false discovery rate
procedure, so what is shown as significant is what survives the multiple
comparisons rather than what happened to look big. Games that do not survive
are still listed, just marked plainly.</p>

<h2>How well it does</h2>
<p>Tested against every real playoff committee poll from 2023 through 2025,
the model's ranking sits about 2.9 places from the committee's on average,
and its playoff field contains 23 of the 28 teams the committee actually
picked. Where the two disagree, it is usually the same way: the model rates
teams with fewer losses from weaker leagues higher than the committee does.
Settings were tuned on those seasons and checked by leaving one season out.</p>

<h2>What it is not</h2>
<p>The odds are the output of a model, not a prediction anyone should bet on.
It does not know about injuries, suspensions, weather, or a quarterback
changing everything in October. The scores update every hour on game days,
and the numbers move with them.</p>

<p>The whole thing is open source:
<a href="https://github.com/goodsellkai/cfb-rooting">github.com/goodsellkai/cfb-rooting</a>.</p>
<p><a href="../">Back to the guide</a></p>"""


def not_found_head() -> str:
    return _head(f"Page not found | {SITE_NAME}",
                 "That address is not part of this site.", SITE_URL + "/")


def not_found() -> str:
    return f"""<h1>Page not found</h1>
<p>That address is not part of this site.</p>
<p><a href="/">Go to {SITE_NAME}</a></p>"""


def sitemap(teams) -> str:
    day = datetime.now(timezone.utc).date().isoformat()
    urls = ([SITE_URL + "/", SITE_URL + "/how-it-works/"]
            + [f"{SITE_URL}/team/{slug(t.school)}/" for t in teams])
    body = "\n".join(
        f"  <url><loc>{u}</loc><lastmod>{day}</lastmod></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


def headers() -> str:
    """How long each kind of file may be held, for hosts that read _headers."""
    # One rule per path, since a host that matches several applies them all.
    return """/data/*
  Cache-Control: public, max-age=600
/static/header/*
  Cache-Control: public, max-age=604800
/static/*
  Cache-Control: public, max-age=86400
/
  Cache-Control: public, max-age=300
/team/*
  Cache-Control: public, max-age=300
"""


def robots() -> str:
    # The data files are for the page, not for crawlers: they are hundreds of
    # megabytes and hold nothing a search result would show.
    return ("User-agent: *\n"
            "Allow: /\n"
            "Disallow: /data/\n"
            f"Sitemap: {SITE_URL}/sitemap.xml\n")
