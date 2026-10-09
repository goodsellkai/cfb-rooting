"""Static pages for the exported site.

Every team gets its own address, so a search engine has something to index
and a link to a team lands on that team. The app takes over once its script
runs; what is written here is what a crawler and a cold visitor see first.
"""

from __future__ import annotations

import html
import json
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="minutes")


def _site() -> dict:
    return {"@type": "WebSite", "name": SITE_NAME, "url": SITE_URL + "/"}


def _publisher() -> dict:
    return {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL + "/",
            "logo": {"@type": "ImageObject",
                     "url": f"{SITE_URL}/static/icon-512.png"}}


def _page_ld(title, description, url) -> dict:
    return {"@context": "https://schema.org", "@type": "WebPage",
            "name": title, "description": description, "url": url,
            "dateModified": _now(), "isPartOf": _site(),
            "publisher": _publisher()}


def _crumbs(trail) -> dict:
    """``[(name, url), ...]`` from the front page down to this one."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": i, "name": name, "item": url}
                for i, (name, url) in enumerate(trail, 1)]}


def _faq_ld(pairs) -> dict:
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": q,
                 "acceptedAnswer": {"@type": "Answer", "text": a}}
                for q, a in pairs]}


def _faq_html(pairs) -> str:
    out = "<h2>Questions</h2>\n<dl class=\"faq\">\n"
    for q, a in pairs:
        out += (f"<dt>{html.escape(q)}</dt>\n"
                f"<dd>{a}</dd>\n")
    return out + "</dl>"


def _head(title, description, url, extra="", ld=None):
    t, d = html.escape(title), html.escape(description)
    card = f"{SITE_URL}/static/header/1.jpg"
    blocks = ld if ld is not None else [_page_ld(title, description, url)]
    scripts = "".join(
        '\n<script type="application/ld+json">'
        + json.dumps(b, separators=(",", ":")) + "</script>"
        for b in blocks)
    return f"""<title>{t}</title>
<meta name="description" content="{d}">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
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
<meta name="twitter:image" content="{card}">{scripts}{extra}"""


def home_questions(state, n_sims, race=()):
    week = state.current_week()
    leader = (f"{race[0][0]} at {_pct(race[0][1])}, then "
              + ", ".join(f"{t} at {_pct(o)}" for t, o in race[1:3])
              if race else "")
    return [
        ("Which games should I root for this week?",
         f"Pick your team and every week {week} game is sorted by how much "
         f"each result moves its playoff odds, worked out from "
         f"{_sims(n_sims)} simulated seasons. The game that matters most is "
         f"often one your team is not playing in."),
        ("Who is most likely to make the College Football Playoff?",
         f"{leader}." if leader else
         "The teams on the front page, in order of their odds."),
        ("How are these playoff odds calculated?",
         f"The rest of the season is played out {_sims(n_sims)} times. Each "
         f"simulated season is rated with Massey's model, ranked through a "
         f"model of the selection committee, and its bracket filled, so the "
         f"odds come from full seasons rather than a formula."),
        ("How often do the numbers update?",
         "Every half hour on Saturdays and daily the rest of the week, with "
         "live scores on the page in between."),
    ]


def home_head(state, n_sims, race=()):
    week = state.current_week()
    desc = (f"College Football Playoff odds for all 138 teams, and which "
            f"week {week} games move them. Every remaining {state.year} game "
            f"scored on {_sims(n_sims)} simulated seasons.")
    title = f"{SITE_NAME}: playoff odds and who to root for"
    page = _page_ld(title, desc, SITE_URL + "/")
    site = dict(_site())
    site["@context"] = "https://schema.org"
    site["description"] = desc
    return _head(title, desc, SITE_URL + "/",
                 ld=[site, page, _faq_ld(home_questions(state, n_sims, race))])


def record(state, idx):
    """``(wins, losses, conference wins, conference losses, results)``."""
    w = l = cw = cl = 0
    results = []
    for g in state.games:
        if not g["completed"] or g["is_ccg"]:
            continue
        if idx not in (g["home_idx"], g["away_idx"]):
            continue
        home = g["home_idx"] == idx
        mine = g["home_points"] if home else g["away_points"]
        theirs = g["away_points"] if home else g["home_points"]
        won = mine > theirs
        w, l = w + won, l + (not won)
        if g["conference_game"]:
            cw, cl = cw + won, cl + (not won)
        results.append({"week": g["week"], "won": won,
                        "opponent": g["away"] if home else g["home"],
                        "at": "" if home else "at ",
                        "score": f"{mine}-{theirs}"})
    results.sort(key=lambda r: r["week"])
    return w, l, cw, cl, results


def _link(school, fbs, depth=1):
    """A team's name, linked to its page when it has one."""
    name = html.escape(school)
    if school not in fbs:
        return name
    return f'<a href="{"../" * depth}{slug(school)}/">{name}</a>'


def _root_lines(payload, week, fbs):
    """The games this week that move this team's playoff odds most."""
    games = [g for g in payload["games"]
             if g["week"] == week and g["swings"]["make_playoff"]["sig_week"]]
    games.sort(key=lambda g: -abs(g["swings"]["make_playoff"]["delta"]))
    out = []
    for g in games[:6]:
        sw = g["swings"]["make_playoff"]
        root = g["home"] if sw["home"] else g["away"]
        other = g["away"] if sw["home"] else g["home"]
        out.append((root, other, abs(sw["delta"])))
    return out


def team_head(state, team, payload, week):
    h = payload["headline"]
    w, l, _, _, _ = record(state, team.idx)
    p = _pct(h["make_playoff"]["p"])
    url = f"{SITE_URL}/team/{slug(team.school)}/"
    title = f"{team.school} playoff odds {state.year}: who to root for"
    desc = (f"{team.school} is {w}-{l} and makes the College Football Playoff "
            f"in {p} of {_sims(payload['n_sims'])} simulated {state.year} "
            f"seasons. Which week {week} games move those odds, and by how "
            f"much.")
    faq = _faq_ld(team_questions(state, team, payload, week, plain=True))
    crumbs = _crumbs([(SITE_NAME, SITE_URL + "/"),
                      ("Teams", SITE_URL + "/teams/"),
                      (team.school, url)])
    page = _page_ld(title, desc, url)
    page["about"] = {"@type": "SportsTeam", "name": team.school,
                     "sport": "College football"}
    return _head(title, desc, url, ld=[page, crumbs, faq])


def team_questions(state, team, payload, week, plain=False):
    """The questions a fan arrives with, and the model's answers."""
    h = payload["headline"]
    school = team.school
    w, l, _, _, _ = record(state, team.idx)
    conf = team.conference or "conference"
    fbs = {t.school for t in state.fbs_teams}
    roots = _root_lines(payload, week, fbs)
    own = sorted(payload["own_games"], key=lambda g: g["week"])

    def name(school_name):
        return html.escape(school_name) if plain else _link(school_name, fbs)

    playoff = (f"{html.escape(school)} is {w}-{l} and reaches the playoff in "
               f"{_pct(h['make_playoff']['p'])} of "
               f"{_sims(payload['n_sims'])} simulated seasons, with "
               f"{payload['expected_wins']:.1f} wins expected. It wins the "
               f"{html.escape(conf)} in {_pct(h['win_conference']['p'])} and "
               f"the national title in "
               f"{_pct(h['win_national_title']['p'], 2)}.")

    if roots:
        top = roots[0]
        rooting = (f"{name(top[0])} over {name(top[1])}, which is worth "
                   f"{_pct(top[2], 2)} of playoff odds")
        if len(roots) > 1:
            rooting += f", then {name(roots[1][0])} over {name(roots[1][1])}"
        rooting += ". Every other game this week moves the odds less than the "
        rooting += "simulations can separate from noise."
    else:
        rooting = (f"Nothing this week moves {html.escape(school)}'s odds "
                   f"enough for the simulations to call it.")

    hardest = ""
    if own:
        game = min(own, key=lambda g: g["p_home_win"] if g["home"] == school
                   else 1 - g["p_home_win"])
        at_home = game["home"] == school
        mine = game["p_home_win"] if at_home else 1 - game["p_home_win"]
        other = game["away"] if at_home else game["home"]
        hardest = (f"{'Hosting' if at_home else 'Visiting'} {name(other)} in "
                   f"week {game['week']}, where the model gives "
                   f"{html.escape(school)} {_pct(mine)}.")

    how = ("Every game left in the season is played out "
           f"{_sims(payload['n_sims'])} times. Each simulated season is rated "
           "and ranked the way the real one is, the bracket is filled, and a "
           "game's value is the difference in playoff odds between the "
           "seasons where it went one way and the seasons where it went the "
           "other.")

    out = [(f"Will {school} make the College Football Playoff?", playoff),
           (f"Who should {school} fans root for in week {week}?", rooting)]
    if hardest:
        out.append((f"What is {school}'s toughest game left?", hardest))
    out.append((f"How are {school}'s playoff odds worked out?", how))
    if plain:
        out = [(q, re.sub(r"<[^>]+>", "", a)) for q, a in out]
    return out


def team_body(state, team, payload, week):
    """The line shown while the page is still loading."""
    h = payload["headline"]
    return f"""<h2>{html.escape(team.school)} playoff odds</h2>
<p>{html.escape(team.school)} reaches the College Football Playoff in
{_pct(h["make_playoff"]["p"])} of {_sims(payload["n_sims"])} simulated
{state.year} seasons. Working out which week {week} games move that
number&hellip;</p>"""


def team_writeup(state, team, payload, week):
    """The written page, which stays on screen once the app has loaded."""
    h = payload["headline"]
    school = html.escape(team.school)
    conf = html.escape(team.conference or "")
    fbs = {t.school for t in state.fbs_teams}
    w, l, cw, cl, results = record(state, team.idx)
    espn = (getattr(team, "espn_odds", None) or {}).get("make_playoff")
    league = sorted(payload["league"], key=lambda r: -r["p"]["make_playoff"])
    place = next((i for i, r in enumerate(league, 1)
                  if r["team"] == team.school), None)
    now = datetime.now(timezone.utc)
    day = f"{now:%B} {now.day}"

    lead = (f"<p>{school} is {w}-{l}"
            + (f" ({cw}-{cl} in the {conf})" if cw + cl else "")
            + f" through week {state.current_week() - 1} of the {state.year} "
            f"season. Across {_sims(payload['n_sims'])} simulated seasons it "
            f"reaches the College Football Playoff "
            f"{_pct(h['make_playoff']['p'])} of the time"
            + (f", where ESPN's FPI gives it {_pct(espn)}" if espn is not None
               else "")
            + f". It wins the {conf or 'conference'} in "
            f"{_pct(h['win_conference']['p'])}"
            + (f", takes a top-four seed and the first-round bye in "
               f"{_pct(h['top4_seed']['p'])}"
               if h["top4_seed"]["p"] >= 0.001 else "")
            + f" and wins the national title in "
            f"{_pct(h['win_national_title']['p'], 2)}, averaging "
            f"{payload['expected_wins']:.1f} wins.</p>")
    if place:
        lead += (f"<p>That is the {_ordinal(place)} best playoff chance of the "
                 f"{len(league)} teams in the sport this week.</p>")

    roots = _root_lines(payload, week, fbs)
    if roots:
        lines = "\n".join(
            f"<li>{_link(a, fbs)} over {_link(b, fbs)}, worth "
            f"{_pct(d, 2)}</li>" for a, b, d in roots)
        rooting = (f"<h2>Who {school} fans should root for in week {week}</h2>\n"
                   f"<p>These are the week {week} games whose result moves "
                   f"{school}'s playoff odds by more than the simulations can "
                   f"put down to chance, biggest first.</p>\n<ul>{lines}</ul>")
    else:
        rooting = (f"<h2>Who {school} fans should root for in week {week}</h2>\n"
                   f"<p>No other game this week moves {school}'s odds enough "
                   f"for the simulations to call it. The games that matter are "
                   f"its own.</p>")

    own = sorted(payload["own_games"], key=lambda g: g["week"])
    if own:
        rows = ""
        for g in own:
            at_home = g["home"] == team.school
            mine = g["p_home_win"] if at_home else 1 - g["p_home_win"]
            other = g["away"] if at_home else g["home"]
            where = "at" if not (at_home or g["neutral"]) else "vs"
            rows += (f"<tr><td>Week {g['week']}</td>"
                     f"<td>{where} {_link(other, fbs)}</td>"
                     f"<td>{_pct(mine)}</td></tr>\n")
        schedule = (f"<h2>{school}'s remaining schedule</h2>\n"
                    f"<table><thead><tr><th>Week</th><th>Opponent</th>"
                    f"<th>Win probability</th></tr></thead>\n"
                    f"<tbody>{rows}</tbody></table>")
    else:
        schedule = f"<h2>{school}'s remaining schedule</h2>\n<p>The regular season is over.</p>"

    if results:
        played = "; ".join(
            f"week {r['week']}, {'beat' if r['won'] else 'lost to'} "
            f"{_link(r['opponent'], fbs)} {r['score']}" for r in results)
        so_far = f"<h2>How {school} got here</h2>\n<p>{played}.</p>"
    else:
        so_far = ""

    mates = [r for r in payload["league"]
             if r["conference"] == team.conference and team.conference]
    race = ""
    if len(mates) > 1:
        mates.sort(key=lambda r: -r["p"]["win_conference"])
        rows = ""
        for r in mates[:10]:
            mark = ' class="mine"' if r["team"] == team.school else ""
            rows += (f"<tr{mark}><td>{_link(r['team'], fbs)}</td>"
                     f"<td>{_pct(r['p']['win_conference'])}</td>"
                     f"<td>{_pct(r['p']['make_playoff'])}</td></tr>\n")
        race = (f"<h2>The {conf} race</h2>\n"
                f"<table><thead><tr><th>Team</th><th>Wins the {conf}</th>"
                f"<th>Makes the playoff</th></tr></thead>\n"
                f"<tbody>{rows}</tbody></table>")

    faq = _faq_html(team_questions(state, team, payload, week))
    return f"""<h1>{school} playoff odds and who to root for</h1>
<p class="foot">Updated {day}, after week {state.current_week() - 1}.</p>
{lead}
{rooting}
{schedule}
{so_far}
{race}
{faq}
<p><a href="../../teams/">All {len(league)} teams</a> &middot;
<a href="../../how-it-works/">How these numbers are worked out</a></p>"""


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _team_row(school, logo, odds=None, best=1.0, pos=None):
    art = (f'<img src="{html.escape(logo)}" alt="" width="22" height="22">'
           if logo else '<span class="noart"></span>')
    bar = ""
    if odds is not None:
        width = max(2, round(100 * odds / best)) if best else 2
        bar = (f'<span class="odds"><span class="barline">'
               f'<i style="width:{width}%"></i></span>'
               f'<b>{_pct(odds, 2)}</b></span>')
    rank = f'<span class="pos">{pos}</span>' if pos else ""
    return (f'<li>{rank}{art}<a href="team/{slug(school)}/">'
            f'{html.escape(school)}</a>{bar}</li>')


def home_body(state, teams, n_sims, race=(), bubble=()):
    week = state.current_week()
    logos = {t.school: t.logo for t in teams}
    best = race[0][1] if race else 1.0
    top = "\n".join(_team_row(s, logos.get(s), o, best, i)
                    for i, (s, o) in enumerate(race, 1))
    edge = "\n".join(_team_row(s, logos.get(s), o, best)
                     for s, o in bubble)
    return f"""<div class="hero">
<div>
<h1>Root for the right team</h1>
<p class="lead">Your team has games left, and so does everyone chasing the
same playoff spot. Pick a team and every game left in the {state.year} season
is sorted by how much each result moves that team's odds, out of
{_sims(n_sims)} simulated seasons.</p>
<p class="lead">Some weeks the game that matters most does not involve your
team at all.</p>
<p>Each simulated season plays out every game left, rates the teams on the
results the way the real ones are rated, ranks them the way the selection
committee does, and fills the bracket.
<a href="how-it-works/">How that works</a>.</p>
<p class="cta"><a href="#team">Pick your team</a> or take one of these.</p>
</div>

<div class="boards">
  <div>
    <h2>The race, week {week}</h2>
    <ol class="race">
{top}
    </ol>
  </div>
  <div>
    <h2>On the bubble</h2>
    <ol class="race">
{edge}
    </ol>
    <p class="foot"><a href="teams/">All {len(teams)} teams</a></p>
  </div>
</div>
</div>

{_faq_html(home_questions(state, n_sims, race))}"""


def teams_head(state):
    desc = (f"Playoff odds and a rooting guide for all 138 FBS teams, "
            f"updated through week {state.current_week()} of the {state.year} "
            f"season.")
    url = f"{SITE_URL}/teams/"
    title = f"Playoff odds for every FBS team | {SITE_NAME}"
    return _head(title, desc, url,
                 ld=[_page_ld(title, desc, url),
                     _crumbs([(SITE_NAME, SITE_URL + "/"), ("Teams", url)])])


def teams_body(state, teams, odds=()):
    """Every team, by conference, with what the simulations give it."""
    chance = dict(odds)
    by_conf: dict[str, list] = {}
    for t in teams:
        by_conf.setdefault(t.conference or "Independent", []).append(t)
    out = ""
    for conf in sorted(by_conf):
        rows = ""
        for t in sorted(by_conf[conf],
                        key=lambda t: (-chance.get(t.school, 0), t.school)):
            art = (f'<img src="{html.escape(t.logo)}" alt="" width="20" '
                   f'height="20">' if t.logo else "")
            p = chance.get(t.school)
            rows += (f'<tr><td><a href="../team/{slug(t.school)}/">{art}'
                     f'{html.escape(t.school)}</a></td>'
                     f'<td class="num">{_pct(p, 2) if p is not None else "-"}</td></tr>\n')
        out += (f'<section>\n<h2>{html.escape(conf)}</h2>\n<table><thead><tr>'
                f'<th>Team</th><th class="num">Makes the playoff</th></tr></thead>\n'
                f'<tbody>{rows}</tbody></table>\n</section>\n')
    return f"""<h1>Playoff odds for every FBS team</h1>
<p class="lead">All {len(teams)} teams, by conference, with how often each one
reaches the College Football Playoff across the simulations. Each team's page
has its conference and title odds, its remaining schedule and the games
elsewhere that move its number most.</p>
<div class="confs">
{out}</div>
<p><a href="../">Back to the guide</a></p>"""


def how_head(state, n_sims):
    desc = (f"How the rooting guide works: {_sims(n_sims)} simulated seasons, "
            f"a rating fitted to every one of them, a model of the selection "
            f"committee, and a test for which swings are real.")
    url = f"{SITE_URL}/how-it-works/"
    title = f"How the playoff odds are worked out | {SITE_NAME}"
    return _head(title, desc, url,
                 ld=[_page_ld(title, desc, url),
                     _crumbs([(SITE_NAME, SITE_URL + "/"),
                              ("How this works", url)])])


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
<p>FPI comes out once a day, so a Saturday result is not in it until Sunday
morning. Until it is, every finished game the ratings have not seen moves
both teams by the share of the surprise FPI itself would move them, worked
out from three seasons of its weekly ratings. A team that wins in the
afternoon is simulated on Saturday night at close to the strength it is
about to be given, rather than the one it had on Friday.</p>

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

<h2>What it keeps</h2>
<p>No accounts, no sign in, no cookies. The team you picked and which tab you
were on are remembered by your own browser and never leave it. Visits are
counted by Cloudflare\'s analytics, which does not use cookies and does not
identify anyone. Nothing is asked for and nothing is stored anywhere else.</p>

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
    urls = ([SITE_URL + "/", SITE_URL + "/how-it-works/", SITE_URL + "/teams/"]
            + [f"{SITE_URL}/team/{slug(t.school)}/" for t in teams])
    body = "\n".join(
        f"  <url><loc>{u}</loc><lastmod>{day}</lastmod></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


def headers() -> str:
    """How long each kind of file may be held, for hosts that read _headers.

    The security headers are the ones a site with no accounts and no cookies
    can usefully set: where images, styles and scripts may come from, who may
    frame it, and https from here on. Scripts are the page's own, except for
    the few lines the build writes into the head, which is what the inline
    allowance covers.

    Cloudflare injects its analytics beacon into the served HTML, so the two
    cloudflareinsights origins have to be allowed or the page blocks it and
    every visit goes uncounted.
    """
    # One rule per path, since a host that matches several applies them all.
    return """/*
  Content-Security-Policy: default-src \'self\'; img-src \'self\' data: https://a.espncdn.com https://*.espncdn.com; style-src \'self\' \'unsafe-inline\'; script-src \'self\' \'unsafe-inline\' https://static.cloudflareinsights.com; connect-src \'self\' https://site.api.espn.com https://cloudflareinsights.com; frame-ancestors \'none\'; base-uri \'self\'; form-action \'none\'
  Strict-Transport-Security: max-age=31536000
  X-Frame-Options: DENY
/data/*
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
    # megabytes and hold nothing a search result would show. The assistants
    # are named rather than left to the wildcard so there is no doubt they
    # may read and quote the pages.
    bots = ("Googlebot", "Google-Extended", "Bingbot", "OAI-SearchBot",
            "GPTBot", "ChatGPT-User", "ClaudeBot", "Claude-User",
            "PerplexityBot", "Perplexity-User", "Applebot", "Applebot-Extended")
    named = "".join(f"User-agent: {bot}\nAllow: /\nDisallow: /data/\n\n"
                    for bot in bots)
    return ("User-agent: *\n"
            "Allow: /\n"
            "Disallow: /data/\n\n"
            + named
            + f"Sitemap: {SITE_URL}/sitemap.xml\n")


def llms_txt(state, teams, n_sims) -> str:
    """A plain text map of the site, for assistants that look for one."""
    week = state.current_week()
    lines = [
        f"# {SITE_NAME}",
        "",
        f"> Playoff odds and a weekly rooting guide for all {len(teams)} FBS "
        f"college football teams, from {_sims(n_sims)} simulations of the rest "
        f"of the {state.year} season. Updated through week {week - 1}.",
        "",
        "Each team page carries that team's odds of making the College "
        "Football Playoff, winning its conference and winning the national "
        "title, its remaining schedule with a win probability for every game, "
        "and the games elsewhere in the country whose result moves its odds "
        "most. The numbers are rebuilt through the day on game days.",
        "",
        "## Pages",
        "",
        f"- [Home]({SITE_URL}/): the playoff race this week",
        f"- [How it works]({SITE_URL}/how-it-works/): the simulation, the "
        f"rating, the committee model and the bracket",
        f"- [Every team]({SITE_URL}/teams/): links to all {len(teams)} team "
        f"pages",
        "",
        "## Teams",
        "",
    ]
    for t in sorted(teams, key=lambda t: t.school):
        lines.append(f"- [{t.school} playoff odds]"
                     f"({SITE_URL}/team/{slug(t.school)}/): "
                     f"{t.conference or 'Independent'}")
    return "\n".join(lines) + "\n"
