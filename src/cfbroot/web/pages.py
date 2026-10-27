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
from datetime import datetime, timedelta, timezone

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


# Who writes the pages, for the markup that asks for an author.
AUTHOR = {"@type": "Person", "name": "Kai Goodsell",
          "url": "https://github.com/goodsellkai"}


def _head(title, description, url, extra="", ld=None, card=None, card_alt="",
          index=True):
    t, d = html.escape(title), html.escape(description)
    # A drawn share card when the build made one; the header photo otherwise.
    size = (1200, 630) if card else (1280, 720)
    card = card or f"{SITE_URL}/static/header/1.jpg"
    alt = html.escape(card_alt or title)
    blocks = ld if ld is not None else [_page_ld(title, description, url)]
    robots = ('<meta name="robots" content="index, follow, max-snippet:-1, '
              f'max-image-preview:large">\n<link rel="canonical" href="{url}">'
              if index else '<meta name="robots" content="noindex">')
    scripts = "".join(
        '\n<script type="application/ld+json">'
        + json.dumps(b, separators=(",", ":")) + "</script>"
        for b in blocks)
    return f"""<title>{t}</title>
<meta name="description" content="{d}">
{robots}
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{card}">
<meta property="og:image:width" content="{size[0]}">
<meta property="og:image:height" content="{size[1]}">
<meta property="og:image:alt" content="{alt}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{card}">
<meta name="twitter:image:alt" content="{alt}">{scripts}{extra}"""


def home_questions(state, n_sims, race=()):
    week = state.current_week()
    leader = (f"{race[0][0]} at {_pct(race[0][1])}, then "
              + ", ".join(f"{t} at {_pct(o)}" for t, o in race[1:3])
              if race else "")
    return [
        ("Which games should I root for this week?",
         f"Pick your team to see every week {week} game ranked by its effect "
         f"on your team's playoff odds, from {_sims(n_sims)} simulated "
         f"seasons. The most important game is often one your team is not "
         f"playing in."),
        ("Who is most likely to make the College Football Playoff?",
         f"{leader}." if leader else
         "The teams on the front page, in order of their odds."),
        ("How are these playoff odds calculated?",
         f"The rest of the season is simulated {_sims(n_sims)} times. Each "
         f"simulated season is rated with Kenneth Massey's model, ranked by a "
         f"model of the selection committee and played through the 12-team "
         f"bracket."),
    ]


def home_head(state, n_sims, race=(), card=None):
    week = state.current_week()
    desc = (f"College Football Playoff odds for all 138 teams, and which "
            f"week {week} games move them. Every remaining {state.year} game "
            f"scored on {_sims(n_sims)} simulated seasons.")
    title = (f"College Football Playoff odds {state.year} and who to root for "
             f"| {SITE_NAME}")
    page = _page_ld(title, desc, SITE_URL + "/")
    site = dict(_site())
    site["@context"] = "https://schema.org"
    site["description"] = desc
    app = {"@context": "https://schema.org", "@type": "WebApplication",
           "name": SITE_NAME, "url": SITE_URL + "/", "description": desc,
           "applicationCategory": "SportsApplication", "operatingSystem": "Any",
           "browserRequirements": "Requires JavaScript",
           "isAccessibleForFree": True, "author": AUTHOR,
           "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
    return _head(title, desc, SITE_URL + "/", card=card,
                 card_alt=f"Who to root for, week {week}",
                 ld=[site, page, app,
                     _faq_ld(home_questions(state, n_sims, race))])


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


# What a team needs: each game it has left with its odds after a win and
# after a loss, its title game, and its odds at each final record. Every
# measure has a phrase, so the app can say the same for any of them.
PHRASES = {
    "make_playoff": "makes the playoff",
    "win_conference": "wins the {c}",
    "make_conf_title_game": "reaches the {c} title game",
    "top4_seed": "earns a first-round bye",
    "reach_quarterfinal": "reaches the quarterfinals",
    "reach_semifinal": "reaches the semifinals",
    "reach_title_game": "reaches the national title game",
    "win_national_title": "wins the national title",
    "finish_ranked": "finishes ranked",
}
# A final record that comes up in fewer seasons than this is left out, and
# one needs this many seasons behind it before it decides what a team needs.
RECORD_FLOOR = 0.005
RECORD_SEASONS = 1000
GOALS = {
    "make_playoff": "make the playoff",
    "win_conference": "win the {c}",
    "make_conf_title_game": "reach the {c} title game",
    "top4_seed": "earn a first-round bye",
    "reach_quarterfinal": "reach the quarterfinals",
    "reach_semifinal": "reach the semifinals",
    "reach_title_game": "reach the national title game",
    "win_national_title": "win the national title",
    "finish_ranked": "finish ranked",
}


def conf_slug(conference: str) -> str:
    return slug(conference)


def has_conference(team) -> bool:
    c = team.conference or ""
    return bool(c) and "independent" not in c.lower()


def own_rows(team, payload, key="make_playoff"):
    """The team's games left, with its chance in each, the measure after a
    win and after a loss, and how far the game is expected to move it."""
    rows = []
    for g in sorted(payload.get("own_games") or (),
                    key=lambda g: (g["week"], g.get("start_date") or "")):
        s = (g.get("swings") or {}).get(key) or {}
        home = g["home"] == team.school
        win = s.get("p_if_home") if home else s.get("p_if_away")
        lose = s.get("p_if_away") if home else s.get("p_if_home")
        ph = g.get("p_home_win")
        if win is None or lose is None or ph is None:
            continue
        chance = ph if home else 1 - ph
        # too few seasons with the unlikely result to measure it
        sure = s.get("reliable") is not False
        rows.append({"week": g["week"], "home": home,
                     "where": "vs" if home or g.get("neutral") else "at",
                     "opponent": g["away"] if home else g["home"],
                     "chance": chance, "win": win, "lose": lose, "game": g,
                     "sure": sure,
                     # the gap between the results, weighted by how likely
                     # each is: the toughest game, not the easiest one to lose
                     "move": 2 * chance * (1 - chance) * abs(win - lose) if sure else 0.0})
    return rows


def record_rows(payload):
    """Final records worth showing, best first."""
    return [r for r in payload.get("by_record") or () if r["p"] >= RECORD_FLOOR]


def _record_at(payload, wins):
    for r in payload.get("by_record") or ():
        if r["wins"] == wins and r["n"] >= RECORD_SEASONS:
            return r
    return None


def needs_text(state, team, payload, key="make_playoff", name=None):
    """What the team needs: whether it can afford a loss, has to win out or
    needs help, the game that decides the most, its title game and its bowl
    chances where those are in doubt. ``name`` links an opponent."""
    name = name or html.escape
    school = html.escape(team.school)
    conf = html.escape(team.conference or "conference")
    phrase = PHRASES[key].format(c=conf)
    goal = GOALS[key].format(c=conf)
    games = own_rows(team, payload, key)
    left = len(payload.get("own_games") or ())
    w, _, _, _, _ = record(state, team.idx)
    out = []

    def rec(r):
        return f"{r['wins']}-{r['losses']}"

    def odds(r):
        return _pct(r["odds"][key])

    if left:
        r0, r1, r2 = (_record_at(payload, w + left - k) for k in range(3))
        o0, o1, o2 = (r["odds"][key] if r else None for r in (r0, r1, r2))
        if r2 and o2 >= 0.6:
            out += [f"{school} can lose twice and still {goal}.",
                    f"At {rec(r2)} it {phrase} in {odds(r2)}."]
        elif r1 and o1 >= 0.6:
            out += [f"{school} can afford one loss.",
                    f"At {rec(r1)} it {phrase} in {odds(r1)}"
                    + (f", at {rec(r2)} in {odds(r2)}." if r2 else ".")]
        elif r0 and o0 >= 0.5 and (not r1 or o1 < 0.25):
            out += [f"{school} has to win out.",
                    f"At {rec(r0)} it {phrase} in {odds(r0)}"
                    + (f", at {rec(r1)} in {odds(r1)}." if r1 else ".")]
        elif r0 and o0 >= 0.25:
            out.append(f"Winning out puts {school} at {odds(r0)}."
                       + (f" At {rec(r1)} it {phrase} in {odds(r1)}." if r1 else ""))
        elif r0 and o0 >= 0.005:
            out += [f"{school} needs help even if it wins out.",
                    f"At {rec(r0)} it {phrase} in {odds(r0)}."]
        elif r0:
            out.append(f"Even at {rec(r0)}, {school} {phrase} in {odds(r0)} of seasons.")

    sure = [r for r in games if r["move"] > 0]
    if sure and left > 1:
        big = max(sure, key=lambda r: r["move"])
        where = "at" if big["where"] == "at" else "against"
        out.append(f"The game that decides the most is {where} "
                   f"{name(big['opponent'])} in week {big['week']}: "
                   f"{_pct(big['win'])} with a win, {_pct(big['lose'])} with a loss.")
    elif sure:
        r = sure[0]
        where = "at" if r["where"] == "at" else "against"
        out.append(f"Its last game is {where} {name(r['opponent'])}: "
                   f"{_pct(r['win'])} with a win, {_pct(r['lose'])} with a loss.")

    tg = payload.get("title_game")
    if tg and key not in ("win_conference", "make_conf_title_game"):
        reach = tg["p"][0] + tg["p"][1]
        won, lost, missed = tg["odds"][key]
        if reach >= RECORD_FLOOR and won is not None and lost is not None:
            out.append(f"It reaches the {conf} title game in {_pct(reach)} of "
                       f"seasons. Winning it puts {school} at {_pct(won)} and "
                       f"losing it at {_pct(lost)}"
                       + (f"; missing it leaves {_pct(missed)}." if missed is not None
                          else "."))

    bowl = sum((payload.get("wins_distribution") or [])[6:])
    if 0.02 <= bowl <= 0.98:
        out.append(f"It reaches six wins and a bowl in {_pct(bowl)} of seasons.")
    return " ".join(out)


def _needs_html(state, team, payload, fbs):
    """The written version of the app's What X needs block."""
    school = html.escape(team.school)
    games = own_rows(team, payload)
    summary = needs_text(state, team, payload, name=lambda s: _link(s, fbs))
    out = f"<h2>What {school} needs</h2>\n"
    if summary:
        out += f"<p>{summary}</p>\n"
    if games:
        rows = "".join(
            f"<tr><td>Week {r['week']}</td>"
            f"<td>{r['where']} {_link(r['opponent'], fbs)}</td>"
            f"<td>{_pct(r['chance'])}</td>"
            f"<td>{_pct(r['win']) if r['sure'] or r['chance'] >= 0.5 else '-'}</td>"
            f"<td>{_pct(r['lose']) if r['sure'] or r['chance'] < 0.5 else '-'}</td></tr>\n"
            for r in games)
        out += ("<table><thead><tr><th>Week</th><th>Opponent</th>"
                "<th>Win probability</th><th>Playoff odds after a win</th>"
                "<th>After a loss</th></tr></thead>\n"
                f"<tbody>{rows}</tbody></table>\n")
    else:
        out += "<p>The regular season is over.</p>\n"
    recs = record_rows(payload)
    if games and len(recs) > 1:
        rows = "".join(
            f"<tr><td>{r['wins']}-{r['losses']}</td><td>{_pct(r['p'])}</td>"
            f"<td>{_pct(r['odds']['make_playoff'])}</td></tr>\n" for r in recs)
        out += (f"<h3>{school}'s playoff odds by final record</h3>\n"
                "<table><thead><tr><th>Regular season</th>"
                "<th>Chance of that record</th><th>Playoff odds</th></tr></thead>\n"
                f"<tbody>{rows}</tbody></table>\n")
    return out


def team_head(state, team, payload, week, card=None):
    h = payload["headline"]
    w, l, _, _, _ = record(state, team.idx)
    p = _pct(h["make_playoff"]["p"])
    url = f"{SITE_URL}/team/{slug(team.school)}/"
    title = f"{team.school} playoff chances {state.year}: what it needs, who to root for"
    desc = (f"Week {week}: {team.school} is {w}-{l} and makes the College "
            f"Football Playoff in {p} of {_sims(payload['n_sims'])} simulated "
            f"seasons. What it needs from here, and which games to root for.")
    faq = _faq_ld(team_questions(state, team, payload, week, plain=True))
    middle = ((team.conference, f"{SITE_URL}/conference/{conf_slug(team.conference)}/")
              if has_conference(team) else ("Teams", SITE_URL + "/teams/"))
    crumbs = _crumbs([(SITE_NAME, SITE_URL + "/"), middle, (team.school, url)])
    about = {"@type": "SportsTeam", "name": team.school,
             "sport": "American football", "url": url}
    if team.conference and "independent" not in team.conference.lower():
        about["memberOf"] = {"@type": "SportsOrganization",
                             "name": team.conference}
    # No games as events: Google reads those as events to attend and wants a
    # venue for each, which the build does not keep.
    page = _page_ld(title, desc, url)
    page["about"] = about
    # The writeup is an article that is rewritten every build, so it says
    # when, and who by. No first-published date: the site does not keep one.
    article = {"@context": "https://schema.org", "@type": "Article",
               "headline": title, "description": desc, "url": url,
               "mainEntityOfPage": url, "dateModified": _now(),
               "author": AUTHOR, "publisher": _publisher(),
               "about": {"@type": "SportsTeam", "name": team.school}}
    if card:
        article["image"] = [card]
    story = card_story(state, team, payload, week)
    return _head(title, desc, url, card=card, card_alt=story["alt"],
                 ld=[page, article, crumbs, faq])


def card_story(state, team, payload, week):
    """What a team's share card says, and the same in words for its alt text.

    One number, for the race the team is really in: the playoff, or failing a
    real chance of that, its conference title, then a place in the title
    game. A team with none of those gets its expected wins rather than a
    0.00%. Then this week's call for that same number: the game to root for,
    or failing one worth calling, the team's own game.
    """
    h = payload["headline"]
    conf = team.conference or ""
    indie = not conf or "independent" in conf.lower()
    races = [("make_playoff", ["to make the", "playoff"])]
    if not indie:
        races += [("win_conference", ["to win the", conf]),
                  ("make_conf_title_game", ["to reach the", f"{conf} title game"])]
    key, label = next(((k, lab) for k, lab in races if h[k]["p"] >= 0.005),
                      (None, ["wins expected", "this season"]))
    if key:
        number, unit = f"{100 * h[key]['p']:.2f}", "%"
    else:
        number, unit = f"{payload['expected_wins']:.1f}", ""

    w, l, cw, cl, _ = record(state, team.idx)
    rec = f"{w}-{l}" + (f" ({cw}-{cl} {conf})" if cw + cl and not indie else "")
    for kind, name in (("cfp", "CFP"), ("ap", "AP")):
        rank = state.polls.get(kind, {}).get(team.idx)
        if rank:
            rec += f", {name} No. {rank}"
            break

    games = [g for g in payload["games"] if key and g["week"] == week
             and g["swings"][key].get("sig_week")]
    games.sort(key=lambda g: -abs(g["swings"][key]["delta"]))
    own = [g for g in payload["own_games"] if g["week"] == week]
    if games:
        g = games[0]
        home = g["swings"][key]["home"]
        root, other = (g["home"], g["away"]) if home else (g["away"], g["home"])
        ri, oi = ((g["home_idx"], g["away_idx"]) if home
                  else (g["away_idx"], g["home_idx"]))
        line = [("t", f"Week {week}: root for "), ("logo", ri), ("hl", root),
                ("t", " over "), ("logo", oi), ("t", other)]
        said = f"Week {week}: root for {root} over {other}."
    elif own:
        g = own[0]
        at_home = g["home"] == team.school
        opp = g["away"] if at_home else g["home"]
        oi = g["away_idx"] if at_home else g["home_idx"]
        chance = g["p_home_win"] if at_home else 1 - g["p_home_win"]
        where = "vs " if at_home or g.get("neutral") else "at "
        line = [("t", f"Week {week}: {where}"), ("logo", oi),
                ("t", f"{opp}, {100 * chance:.0f}% to win")]
        said = f"Week {week}: {where}{opp}, {100 * chance:.0f}% to win."
    else:
        line = [("t", "Who to root for, week by week")]
        said = ""
    alt = f"{team.school}, {rec}: {number}{unit} {' '.join(label)}. {said}"
    return {"abbr": team.abbreviation or team.school[:3].upper(),
            "record": rec, "number": number, "unit": unit, "label": label,
            "line": line, "alt": alt.strip()}


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

    playoff = (f"{html.escape(school)} is {w}-{l} and makes the playoff in "
               f"{_pct(h['make_playoff']['p'])} of "
               f"{_sims(payload['n_sims'])} simulated seasons, with "
               f"{payload['expected_wins']:.1f} projected wins. It wins the "
               f"{html.escape(conf)} in {_pct(h['win_conference']['p'])} and "
               f"the national title in "
               f"{_pct(h['win_national_title']['p'], 2)}.")

    if roots:
        top = roots[0]
        rooting = (f"{name(top[0])} over {name(top[1])}, worth "
                   f"{_pct(top[2], 2)} in playoff odds")
        if len(roots) > 1:
            rooting += f", then {name(roots[1][0])} over {name(roots[1][1])}"
        rooting += "."
    else:
        rooting = (f"No other game this week has a significant effect on "
                   f"{html.escape(school)}'s playoff odds.")

    hardest = ""
    if own:
        game = min(own, key=lambda g: g["p_home_win"] if g["home"] == school
                   else 1 - g["p_home_win"])
        at_home = game["home"] == school
        mine = game["p_home_win"] if at_home else 1 - game["p_home_win"]
        other = game["away"] if at_home else game["home"]
        hardest = (f"{'Hosting' if at_home else 'At'} {name(other)} in "
                   f"week {game['week']}, with a {_pct(mine)} chance to win.")

    how = (f"The rest of the season is simulated {_sims(payload['n_sims'])} "
           "times. Each simulated season is rated, ranked by a model of the "
           "selection committee and played through the bracket. A game's "
           f"impact is the difference in {html.escape(school)}'s playoff odds "
           "between the simulations each team wins it.")

    out = [(f"Will {school} make the College Football Playoff?", playoff)]
    needs = needs_text(state, team, payload, name=name)
    if needs:
        out.append((f"What does {school} need to make the playoff?", needs))
    out.append((f"Who should {school} fans root for in week {week}?", rooting))
    if hardest:
        out.append((f"What is {school}'s toughest game left?", hardest))
    out.append((f"How are {school}'s playoff odds calculated?", how))
    if plain:
        out = [(q, re.sub(r"<[^>]+>", "", a)) for q, a in out]
    return out


def team_body(state, team, payload, week):
    """The line shown while the page is still loading."""
    h = payload["headline"]
    return f"""<h2>{html.escape(team.school)} playoff odds</h2>
<p>{html.escape(team.school)} makes the College Football Playoff in
{_pct(h["make_playoff"]["p"])} of {_sims(payload["n_sims"])} simulated
{state.year} seasons. Loading the week {week} guide&hellip;</p>"""


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
            f"season. It makes the College Football Playoff in "
            f"{_pct(h['make_playoff']['p'])} of {_sims(payload['n_sims'])} "
            f"simulated seasons"
            + (f" (ESPN's FPI: {_pct(espn)})" if espn is not None else "")
            + f", wins the {conf or 'conference'} in "
            f"{_pct(h['win_conference']['p'])}"
            + (f", earns a top-four seed and first-round bye in "
               f"{_pct(h['top4_seed']['p'])}"
               if h["top4_seed"]["p"] >= 0.001 else "")
            + f" and wins the national title in "
            f"{_pct(h['win_national_title']['p'], 2)}, with "
            f"{payload['expected_wins']:.1f} projected wins.</p>")
    if place:
        lead += (f"<p>That is the {_ordinal(place)}-best playoff chance among "
                 f"the {len(league)} FBS teams.")
        rank = payload.get("cfp_rank") or {}
        if rank.get("rank") and rank["rank"] <= 25:
            lead += (f" In the model's <a href=\"../../cfp-rankings/\">projected "
                     f"CFP rankings</a> after week {rank['week']}, it is No. "
                     f"{rank['rank']}.")
        lead += "</p>"

    roots = _root_lines(payload, week, fbs)
    if roots:
        lines = "\n".join(
            f"<li>{_link(a, fbs)} over {_link(b, fbs)}, worth "
            f"{_pct(d, 2)}</li>" for a, b, d in roots)
        rooting = (f"<h2>Who {school} fans should root for in week {week}</h2>\n"
                   f"<p>The week {week} games that move {school}'s playoff "
                   f"odds the most.</p>\n<ul>{lines}</ul>")
    else:
        rooting = (f"<h2>Who {school} fans should root for in week {week}</h2>\n"
                   f"<p>No other game this week has a significant effect on "
                   f"{school}'s playoff odds.</p>")

    schedule = _needs_html(state, team, payload, fbs)

    if results:
        played = "; ".join(
            f"week {r['week']}, {'beat' if r['won'] else 'lost to'} "
            f"{_link(r['opponent'], fbs)} {r['score']}" for r in results)
        so_far = f"<h2>{school}'s results</h2>\n<p>{played}.</p>"
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
                f"<tbody>{rows}</tbody></table>\n"
                f"<p><a href=\"../../conference/{conf_slug(team.conference)}/\">"
                f"The {conf} title race</a>: every team, the likeliest title "
                f"games and how ties are broken.</p>")

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
<a href="../../how-it-works/">Methodology</a></p>"""


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


def home_body(state, teams, n_sims, race=(), bubble=(), stakes=None,
              league=None):
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
<p class="lead">The game that matters most is often one your team is not
playing in.</p>
<p class="simstat"><b>{_sims(n_sims)}</b><span>simulated seasons behind every
number, each one rated, ranked the way the selection committee works and
played through the 12-team bracket. <a href="how-it-works/">Methodology</a></span></p>
<p class="cta"><a href="#team">Pick your team</a> or start with one of these.</p>
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
    <p class="foot"><a href="teams/">All {len(teams)} teams</a>
    <a href="cfp-rankings/">Projected CFP rankings</a></p>
  </div>
</div>
</div>

{_stakes_home(state, stakes)}
{_races_home(state, league)}
{_faq_html(home_questions(state, n_sims, race))}"""


def teams_head(state, n_sims=None, card=None):
    desc = (f"Playoff odds and a rooting guide for all 138 FBS teams, "
            f"updated through week {state.current_week()} of the {state.year} "
            f"season.")
    url = f"{SITE_URL}/teams/"
    title = f"Playoff odds for every FBS team | {SITE_NAME}"
    sims = f"{_sims(n_sims)} simulated" if n_sims else "simulated"
    dataset = {"@context": "https://schema.org", "@type": "Dataset",
               "name": f"{state.year} College Football Playoff odds for every "
                       f"FBS team",
               "description": (
                   f"How often each FBS team reaches the College Football "
                   f"Playoff across {sims} {state.year} seasons, as of week "
                   f"{state.current_week()}, by conference. Each simulated "
                   f"season is rated and ranked by a model of the selection "
                   f"committee, which picks the field."),
               "url": url, "creator": AUTHOR, "dateModified": _now(),
               "temporalCoverage": str(state.year), "isAccessibleForFree": True,
               "keywords": ["college football", "College Football Playoff",
                            "playoff odds", "simulation", "FBS"],
               "variableMeasured": "Chance of making the College Football "
                                   "Playoff"}
    return _head(title, desc, url, card=card,
                 card_alt=f"Playoff odds for every team, week "
                          f"{state.current_week()}",
                 ld=[_page_ld(title, desc, url), dataset,
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
            odds = (_bar_cell(p, digits=2) if p is not None
                    else '<td class="num">-</td>')
            rows += (f'<tr><td><a href="../team/{slug(t.school)}/">{art}'
                     f'{html.escape(t.school)}</a></td>{odds}</tr>\n')
        head = (f'<a href="../conference/{conf_slug(conf)}/">{html.escape(conf)}</a>'
                if conf in conferences(teams) else html.escape(conf))
        out += (f'<section>\n<h2>{head}</h2>\n<table><thead><tr>'
                f'<th>Team</th><th class="num">Makes the playoff</th></tr></thead>\n'
                f'<tbody>{rows}</tbody></table>\n</section>\n')
    return f"""<h1>Playoff odds for every FBS team</h1>
<p class="foot">Updated after week {state.current_week() - 1} of the {state.year} season.</p>
<div class="confs">
{out}</div>
<p><a href="../">Back to the guide</a></p>"""


# The games that matter most
#
# Every simulated season fills the same twelve playoff places, so whatever
# one result adds to some teams' odds it takes from others: the swings across
# all teams sum to zero. Half the sum of their sizes is how much of the field
# changes hands between a game's two results. A game worth 0.40 moves four
# tenths of a playoff place from one set of teams to another.

class Stakes:
    """Each game left, with the places in play and who they go to, gathered
    from every team's guide as the build writes it. Playoff places by
    default; a conference's title game places with that measure and only its
    own teams added."""

    def __init__(self, metric: str = "make_playoff"):
        self.metric = metric
        self.games: dict = {}

    def add(self, school: str, guide: dict) -> None:
        for g in list(guide.get("games") or ()) + list(guide.get("own_games") or ()):
            s = (g.get("swings") or {}).get(self.metric) or {}
            ph, pa = s.get("p_if_home"), s.get("p_if_away")
            if ph is None or pa is None:
                continue
            row = self.games.setdefault((g["week"], g["home"], g["away"]),
                                        {"game": g, "spots": 0.0, "teams": []})
            row["spots"] += abs(ph - pa) / 2
            row["teams"].append((school, ph - pa))

    def week(self, week, expected=False) -> list:
        """That week's games, the one moving most of the field first.
        ``None`` for every week left. Each row's ``value`` is what is shown:
        the places that change hands between the two results, or with
        ``expected`` the places the game is expected to move, which is that
        times 2p(1-p). A result all but settled moves almost nothing however
        much its upset would."""
        rows = [r for (w, _, _), r in self.games.items()
                if week is None or w == week]
        for r in rows:
            r["teams"].sort(key=lambda t: -abs(t[1]))
            p = r["game"].get("p_home_win")
            p = 0.5 if p is None else p
            r["value"] = r["spots"] * 2 * p * (1 - p) if expected else r["spots"]
        return sorted(rows, key=lambda r: (-r["value"], r["game"]["week"],
                                           r["game"]["home"]))


def _eastern(iso: str) -> datetime:
    """A UTC time in US Eastern. Daylight time runs from the second Sunday in
    March to the first Sunday in November; a season crosses only the second.
    Worked out here because Windows has no time zone database to ask."""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))

    def sunday(month, nth):
        first = datetime(t.year, month, 1, tzinfo=timezone.utc)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))

    summer = (sunday(3, 2) + timedelta(hours=7) <= t
              < sunday(11, 1) + timedelta(hours=6))
    return t - timedelta(hours=4 if summer else 5)


def _kickoff(g) -> str:
    start = g.get("start_date")
    if not start:
        return ""
    et = _eastern(start)
    day = et.strftime("%a")
    if g.get("time_set") is False:
        return f"{day}, time TBA"
    hour = et.hour % 12 or 12
    when = f"{day} {hour}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'} ET"
    return f"{when}, {g['broadcast']}" if g.get("broadcast") else when


def _team_link(school, fbs, depth):
    """A team's name, linked to its page from a page ``depth`` folders down."""
    name = html.escape(school)
    if school not in fbs:
        return name
    return f'<a href="{"../" * depth}team/{slug(school)}/">{name}</a>'


def _kick_html(g) -> str:
    """The kickoff as Eastern time for a crawler, with the exact time on the
    element so the page can say it in the reader's own zone."""
    text = html.escape(_kickoff(g))
    if not g.get("start_date"):
        return '<span class="kick"></span>'
    tba = ' data-tba="1"' if g.get("time_set") is False else ""
    tv = f' data-tv="{html.escape(g["broadcast"])}"' if g.get("broadcast") else ""
    return (f'<time class="kick" datetime="{html.escape(g["start_date"])}"{tba}{tv}>'
            f'{text}</time>')


PLAYOFF_STAKE = "Playoff places that change hands between its two results"


def _stake_chip(name, d, logos, fbs, depth, metric="playoff"):
    """A team with something on a game: the gap between its odds after the
    two results, as a bar on a 0 to 100 point scale and as a number."""
    art = (f'<img src="{html.escape(logos[name])}" alt="" width="18" height="18">'
           if logos.get(name) else "")
    gap = 100 * abs(d)
    return (f'<span class="st" title="{html.escape(name)}: {gap:.1f} points of '
            f'{metric} odds ride on this game">{art}{_team_link(name, fbs, depth)}'
            f'<b>{gap:.1f}</b>'
            f'<span class="stb"><i style="width:{max(gap, 1.5):.1f}%"></i></span></span>')


def _stake_row(row, logos, fbs, best, depth, pos, detail, weeks=False,
               what=PLAYOFF_STAKE, metric="playoff"):
    g = row["game"]
    value = row.get("value", row["spots"])

    def side(name):
        art = (f'<img src="{html.escape(logos[name])}" alt="" width="22" height="22">'
               if logos.get(name) else '<span class="noart"></span>')
        return f'<span class="side">{art}{_team_link(name, fbs, depth)}</span>'

    where = "vs" if g.get("neutral") else "at"
    width = max(2, round(100 * value / best)) if best else 2
    most = ""
    if detail:
        chips = "".join(_stake_chip(name, d, logos, fbs, depth, metric)
                        for name, d in row["teams"][:3] if abs(d) >= 0.0005)
        if chips:
            most = f'<span class="most"><span class="mk">Most at stake</span>{chips}</span>'
    # A list across several weeks says which week each game is in; the
    # kickoff on its own names only a day.
    kick = (f'<span class="when"><span class="wk">Week {g["week"]}</span>'
            f'{_kick_html(g)}</span>' if weeks else _kick_html(g))
    return (f'<li><span class="pos">{pos}</span>'
            f'<span class="match">{side(g["away"])}<i>{where}</i>{side(g["home"])}</span>'
            f'{kick}'
            f'<span class="stake" title="{html.escape(what)}"><span class="barline">'
            f'<i style="width:{width}%"></i></span>'
            f'<b>{value:.2f}</b></span>{most}</li>')


def _stakes_list(rows, state, depth, detail, weeks=False, what=PLAYOFF_STAKE,
                 metric="playoff"):
    logos = {t.school: t.logo for t in state.teams}
    fbs = {t.school for t in state.fbs_teams}
    best = max((r.get("value", r["spots"]) for r in rows), default=1.0)
    return "\n".join(_stake_row(r, logos, fbs, best, depth, i, detail, weeks, what,
                                metric)
                     for i, r in enumerate(rows, 1))


def _stakes_head(value, when="Kickoff") -> str:
    """The column names over a ranked list of games."""
    return (f'<div class="stakehead" aria-hidden="true"><span></span><span>Game</span>'
            f'<span>{when}</span><span class="sv">{html.escape(value)}</span></div>')


def _stakes_home(state, stakes) -> str:
    """The week's five biggest games, under the race on the front page."""
    week = state.default_week() or state.current_week()
    rows = stakes.week(week)[:5] if stakes else []
    if not rows:
        return ""
    return f"""<section class="stakes-home">
<h2>The games that matter most, week {week}</h2>
{_stakes_head("Playoff spots in play")}
<ol class="stakes">
{_stakes_list(rows, state, 0, False)}
</ol>
<p class="foot"><a href="this-week/">Every week {week} game, ranked</a></p>
</section>"""


def this_week_head(state, n_sims, card=None):
    week = state.default_week() or state.current_week()
    title = (f"Week {week} college football games that matter most for the "
             f"playoff | {SITE_NAME}")
    desc = (f"Every week {week} game, ranked by how much it moves the "
            f"{state.year} College Football Playoff race across "
            f"{_sims(n_sims)} simulated seasons, and which teams have the "
            f"most at stake in each.")
    url = f"{SITE_URL}/this-week/"
    return _head(title, desc, url, card=card,
                 card_alt=f"The games that matter most, week {week}",
                 ld=[_page_ld(title, desc, url),
                     _crumbs([(SITE_NAME, SITE_URL + "/"),
                              (f"Week {week}", url)])])


def this_week_body(state, n_sims, stakes):
    week = state.default_week() or state.current_week()
    rows = stakes.week(week) if stakes else []
    # A game that moves almost nothing is listed, but not given a bar to
    # compare against the ones that matter.
    listed = _stakes_list(rows, state, 1, True) if rows else ""
    body = (f'{_stakes_head("Playoff spots in play")}\n'
            f'<ol class="stakes detail">\n{listed}\n</ol>' if rows
            else f"<p>No games left in week {week}.</p>")
    return f"""<h1>The games that matter most for the playoff, week {week}</h1>
<p class="lead">Every week {week} game, ranked by how much of the 12-team field
moves between its two results, across {_sims(n_sims)} simulated seasons.</p>
{body}
<p><a href="../">Back to the guide</a></p>"""


def how_head(state, n_sims, card=None):
    desc = (f"How the playoff odds are made: the rest of the season played "
            f"{_sims(n_sims)} times, every season rated and ranked the way the "
            f"selection committee works, and every game's effect tested.")
    url = f"{SITE_URL}/how-it-works/"
    title = f"Methodology: how the playoff odds are calculated | {SITE_NAME}"
    return _head(title, desc, url, card=card,
                 card_alt=f"{SITE_NAME}: College Football Playoff odds",
                 ld=[_page_ld(title, desc, url),
                     _crumbs([(SITE_NAME, SITE_URL + "/"),
                              ("Methodology", url)])])


def how_body(state, n_sims):
    from ..config import CCG_LOSS_SHARE, ModelParams
    p = ModelParams()
    quarter = {0.25: "a quarter", 0.5: "half"}.get(CCG_LOSS_SHARE,
                                                  f"{CCG_LOSS_SHARE:.0%}")
    return f"""<h1>Methodology</h1>
<p class="lead">Every number on this site comes from playing the rest of the
{state.year} season {_sims(n_sims)} times. Each time, every remaining game is
played, the teams are ranked the way the selection committee ranks them, and
the 12-team bracket is played to a champion. A team's playoff odds are the
share of those seasons in which it made the field.</p>

<dl class="facts">
<div><dt>Simulated seasons</dt><dd>{_sims(n_sims)}</dd></div>
<div><dt>Scoring spread</dt><dd>{p.sigma:.1f} points</dd></div>
<div><dt>Home-field edge</dt><dd>{p.hfa:.2f} points</dd></div>
<div><dt>Playoff field</dt><dd>12 teams</dd></div>
</dl>

<h2>One simulated season</h2>
<ol class="steps">
<li><h3>The games</h3>
<p>Each game gets a score. The expected margin is the gap between the two
teams' ratings in ESPN's Football Power Index, plus {p.hfa:.2f} points for
the home team, a figure taken from closing betting lines. The simulated
margin lands around that the way real ones do, with a spread of
{p.sigma:.1f} points.</p>
<p>A team's strength is set once for the whole simulated season. A team that
plays above its rating in October tends to do it in November too, and the
simulation keeps that.</p>
<p>FPI is published once a day. When a game ends before the next update,
both teams' ratings move by the amount FPI itself would move them, so a
Saturday result counts on Saturday.</p></li>
<li><h3>The rating</h3>
<p>When the regular season is over, the simulated season is rated from
scratch with Kenneth Massey's method: a fit of every score, then an
adjustment for wins and losses against the schedule each team played. Margin
of victory counts, with diminishing returns past a comfortable win.</p></li>
<li><h3>The committee</h3>
<p>The committee does not sort teams by one rating, and neither does the model.
Starting from the rating, wins over good teams earn credit, losses to weak
teams cost, a team stays above a close rival it beat, and conference
champions move up. A conference title game loss counts for {quarter} of a
regular-season loss. Each simulated season also draws its own version of the
committee's judgment.</p></li>
<li><h3>The field</h3>
<p>Standings are settled with each conference's published tiebreakers and
the title games are played. The field follows the {state.year} rules: the
four power conference champions, the highest-ranked Group of Six team, Notre
Dame if it is in the top 12, and at-large teams for the rest. Seeds 1 to 4
get byes.</p></li>
<li><h3>The bracket</h3>
<p>Seeds 5 to 8 host the first round, and every later game is at a neutral
site. A national champion here has won the bracket: three games for a team
with a bye, four for everyone else.</p></li>
</ol>

<h2>What a game is worth</h2>
<p>Every remaining game is played in every simulated season, so one game's
effect can be read straight from the results. Split the seasons by who won
that game and compare a team's odds in the two halves. Everything else that
happened is shared between the halves, so the difference belongs to that
game alone.</p>
<p>A team's guide weighs several hundred games at once. Each difference is
tested, and the whole set is corrected together for false discoveries. A
game is marked significant when its effect survives that correction.</p>

<h2>Data</h2>
<p>Schedules, scores and live games come from ESPN, and team ratings from
ESPN's Football Power Index. Rankings come from the AP poll and the College
Football Playoff committee.</p>

<p>The code is on <a href="https://github.com/goodsellkai/cfb-rooting">GitHub</a>.</p>
<p><a href="../">Back to the guide</a></p>"""


def not_found_head() -> str:
    return _head(f"Page not found | {SITE_NAME}",
                 "That address is not part of this site.", SITE_URL + "/",
                 ld=[], index=False)


def not_found(base: str = "/") -> str:
    """The page for any address the site does not have. Its links are from
    the site's root, since it is served at whatever depth was asked for. A
    mistyped team address gets the nearest team offered by the script."""
    return f"""<h1>Page not found</h1>
<p class="lead">This page does not exist or has moved.</p>
<p id="didyoumean" class="didyoumean" hidden></p>
<ul class="lost">
<li><a href="{base}">Pick your team</a> and see which games to root for</li>
<li><a href="{base}this-week/">The games that matter most this week</a></li>
<li><a href="{base}teams/">Playoff odds for every team</a></li>
<li><a href="{base}cfp-rankings/">Projected CFP rankings</a></li>
<li><a href="{base}how-it-works/">Methodology</a></li>
</ul>"""


def sitemap(teams, cards=None) -> str:
    """Every page, and the share card that goes with it where there is one.
    ``cards`` maps an address to its card."""
    cards = cards or {}
    day = datetime.now(timezone.utc).date().isoformat()
    urls = ([SITE_URL + "/", SITE_URL + "/this-week/", SITE_URL + "/cfp-rankings/",
             SITE_URL + "/how-it-works/", SITE_URL + "/teams/"]
            + [f"{SITE_URL}/conference/{conf_slug(c)}/" for c in conferences(teams)]
            + [f"{SITE_URL}/team/{slug(t.school)}/" for t in teams])

    def entry(u):
        art = (f"<image:image><image:loc>{html.escape(cards[u])}</image:loc>"
               f"</image:image>" if cards.get(u) else "")
        return f"  <url><loc>{u}</loc><lastmod>{day}</lastmod>{art}</url>"

    body = "\n".join(entry(u) for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
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
/card/*
  Cache-Control: public, max-age=604800, immutable
/static/*
  Cache-Control: public, max-age=86400
/
  Cache-Control: public, max-age=300
/team/*
  Cache-Control: public, max-age=300
/conference/*
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
        "most.",
        "",
        "## Pages",
        "",
        f"- [Home]({SITE_URL}/): the playoff race this week",
        f"- [This week]({SITE_URL}/this-week/): the week's games ranked by "
        f"their effect on the playoff race",
        f"- [Projected CFP rankings]({SITE_URL}/cfp-rankings/): the "
        f"committee's top 25 as the model expects it, and the field if the "
        f"season ended today",
        f"- [Methodology]({SITE_URL}/how-it-works/): the simulation, the "
        f"rating, the committee model and the bracket",
        f"- [Every team]({SITE_URL}/teams/): links to all {len(teams)} team "
        f"pages",
        "",
        "## Conferences",
        "",
    ]
    for c in conferences(teams):
        lines.append(f"- [{c} title race]({SITE_URL}/conference/{conf_slug(c)}/): "
                     f"title game and championship odds, likeliest matchups, "
                     f"tiebreakers")
    lines += ["", "## Teams", ""]
    for t in sorted(teams, key=lambda t: t.school):
        lines.append(f"- [{t.school} playoff odds]"
                     f"({SITE_URL}/team/{slug(t.school)}/): "
                     f"{t.conference or 'Independent'}")
    return "\n".join(lines) + "\n"


# Conference pages
#
# One page per conference: who wins it and who reaches the title game, the
# pairings that game is likeliest to have, the games left that move the race
# most and the order ties are broken in.

def conferences(teams) -> list[str]:
    """Conferences with a page, which is every one with a title to win."""
    seen = sorted({t.conference for t in teams if has_conference(t)})
    return [c for c in seen if sum(t.conference == c for t in teams) >= 2]


# The tiebreak steps, as each conference's own policy words them.
_STEP_TEXT = {
    1: "Head-to-head results among the tied teams",
    2: "Record against all common conference opponents",
    3: ("Record against common conference opponents, starting with the "
        "highest-placed and working down the standings"),
    4: "Combined conference winning percentage of conference opponents",
    5: "Total wins",
    6: "Record within the division",
}
_RATING_STEP = {
    "SEC": "Capped scoring margin in conference games",
    "Big Ten": "SportSource Analytics' team rating score",
    "ACC": "SportSource Analytics' team success ranking",
}


def _steps_html(conf, steps) -> str:
    items = []
    for code in steps:
        if code == 7:
            items.append(_RATING_STEP.get(conf, "The conference's ranking metric"))
        elif code in _STEP_TEXT:
            items.append(_STEP_TEXT[code])
    return "<ol class=\"tiebreak\">" + "".join(f"<li>{html.escape(i)}</li>"
                                               for i in items) + "</ol>"


def _tiebreak_html(conf, year) -> str:
    from ..data.conference_rules import (HOSTED, RESTART, UNEQUAL,
                                         title_rules)
    rules = title_rules(conf, year)
    if rules.divisions:
        who = (f"The {' and '.join(rules.divisions)} division winners meet in "
               f"the title game.")
    else:
        who = "The top two teams in the conference standings meet in the title game."
    where = (" It is played at the higher seed's stadium." if rules.flags & HOSTED
             else " It is played at a neutral site.")
    out = f"<p>{html.escape(who + where)}</p>\n"
    if tuple(rules.two) == tuple(rules.multi):
        out += "<p>Ties are broken in this order:</p>\n" + _steps_html(conf, rules.two)
    else:
        out += ("<p>Two tied teams:</p>\n" + _steps_html(conf, rules.two)
                + "\n<p>Three or more:</p>\n" + _steps_html(conf, rules.multi))
    notes = []
    if rules.flags & RESTART:
        notes.append("When a tie of three or more is cut down, the teams left "
                     "start again from the first step.")
    if rules.flags & UNEQUAL:
        notes.append("Teams that play different numbers of conference games "
                     "can tie when they have as many conference wins or losses.")
    if notes:
        out += f"\n<p>{html.escape(' '.join(notes))}</p>"
    return out


def conference_rows(state, conf, league):
    """The conference's teams, with their records and odds, title favourite
    first."""
    rows = []
    for r in league or ():
        if r["conference"] != conf:
            continue
        w, l, cw, cl, _ = record(state, r["idx"])
        rows.append({"idx": r["idx"], "team": r["team"], "p": r["p"],
                     "record": f"{w}-{l}", "conf_record": f"{cw}-{cl}",
                     "conf_pct": cw / max(cw + cl, 1)})
    rows.sort(key=lambda r: (-r["p"]["win_conference"],
                             -r["p"]["make_conf_title_game"], r["team"]))
    return rows


def conference_questions(state, conf, rows, pairs):
    names = {t.idx: t.school for t in state.teams}
    year = state.year
    out = []
    if rows:
        lead = rows[0]
        rest = " and ".join(f"{r['team']} ({_pct(r['p']['win_conference'])})"
                            for r in rows[1:3])
        out.append((f"Who is favored to win the {conf} in {year}?",
                     f"{lead['team']} wins the {conf} in "
                     f"{_pct(lead['p']['win_conference'])} of simulated seasons"
                     + (f", ahead of {rest}." if rest else ".")))
    if pairs:
        a, b = names.get(pairs[0]["a"], "?"), names.get(pairs[0]["b"], "?")
        reach = {r["idx"]: r["p"]["make_conf_title_game"] for r in rows}
        out.append((f"Who will play in the {conf} championship game?",
                    f"The likeliest pairing is {a} against {b}, in "
                    f"{_pct(pairs[0]['p'])} of seasons. {a} reaches the game in "
                    f"{_pct(reach.get(pairs[0]['a']))} and {b} in "
                    f"{_pct(reach.get(pairs[0]['b']))}."))
    if rows:
        expect = sum(r["p"]["make_playoff"] for r in rows)
        best = sorted(rows, key=lambda r: -r["p"]["make_playoff"])[:3]
        out.append((f"How many {conf} teams will make the College Football Playoff?",
                    f"{expect:.1f} on average across the simulated seasons. "
                    + ", ".join(f"{r['team']} makes it in {_pct(r['p']['make_playoff'])}"
                                for r in best) + "."))
    return out


def conference_head(state, conf, rows, n_sims, pairs=None, card=None):
    url = f"{SITE_URL}/conference/{conf_slug(conf)}/"
    title = f"{conf} title race {state.year}: championship odds and tiebreakers"
    lead = (f"{rows[0]['team']} wins it in {_pct(rows[0]['p']['win_conference'])}. "
            if rows else "")
    desc = (f"{lead}Every {conf} team's chance to reach the title game and win "
            f"the conference, the likeliest matchups and how ties are broken, "
            f"from {_sims(n_sims)} simulated seasons.")
    page = _page_ld(title, desc, url)
    page["about"] = {"@type": "SportsOrganization", "name": conf,
                     "sport": "American football"}
    return _head(title, desc, url, card=card, card_alt=f"The {conf} title race",
                 ld=[page,
                     _crumbs([(SITE_NAME, SITE_URL + "/"),
                              ("Teams", SITE_URL + "/teams/"), (conf, url)]),
                     _faq_ld(conference_questions(state, conf, rows, pairs or []))])


def _pair_html(pair, logos, colors, names, fbs, abbrs=None):
    a, b = pair["a"], pair["b"]
    wa = pair["a_wins"]
    abbrs = abbrs or {}

    def side(idx, right):
        art = (f'<img src="{html.escape(logos[idx])}" alt="" width="22" height="22">'
               if logos.get(idx) else '<span class="noart"></span>')
        # A phone has room for the short name only.
        short = html.escape(abbrs.get(idx) or names[idx])
        nm = (f'<a href="../../team/{slug(names[idx])}/"><span class="nm-full">'
              f'{html.escape(names[idx])}</span><span class="nm-abbr">{short}</span></a>'
              if names[idx] in fbs else html.escape(names[idx]))
        return (f'<span class="pr-side{" r" if right else ""}">'
                + (f"{nm}{art}" if right else f"{art}{nm}") + "</span>")

    ca = colors.get(a) or "var(--ink-2)"
    cb = colors.get(b) or "var(--rule)"
    return (f'<li><span class="often"><b>{_pct(pair["p"])}</b> of seasons</span>'
            f'{side(a, False)}<b class="pr-p">{_pct(wa, 0)}</b>'
            f'<span class="split" title="{html.escape(names[a])} wins '
            f'{_pct(wa, 0)} of these games">'
            f'<i style="width:{100 * wa:.1f}%;background:{html.escape(ca)}"></i>'
            f'<i style="background:{html.escape(cb)}"></i></span>'
            f'<b class="pr-p">{_pct(1 - wa, 0)}</b>{side(b, True)}</li>')


def _bar_cell(p, strong=False, digits=1) -> str:
    """A chance as a short bar on a 0 to 100 scale, then the number."""
    v = _pct(p, digits)
    inner = f"<b>{v}</b>" if strong else v
    return (f'<td class="num bc{" strong" if strong else ""}"><span class="bcb" aria-hidden="true">'
            f'<i style="width:{100 * float(p or 0):.1f}%"></i></span>'
            f'<span class="bcv">{inner}</span></td>')


def _title_share(rows, conf, by_idx, fbs) -> str:
    """Every simulated season's champion as one bar: a slice per team in its
    own colour, as wide as its chance, the long tail folded into one."""
    shown = [r for r in rows if r["p"]["win_conference"] >= 0.02][:6]
    rest = max(0.0, 1.0 - sum(r["p"]["win_conference"] for r in shown))
    segs, key = "", ""
    for r in shown:
        t = by_idx[r["idx"]]
        p = r["p"]["win_conference"]
        color = html.escape(t.color or "var(--ink-2)")
        art = (f'<img src="{html.escape(t.logo)}" alt="" width="20" height="20">'
               if t.logo and p >= 0.07 else "")
        segs += (f'<span class="ts-seg" style="flex-grow:{p:.4f};--c:{color}" '
                 f'title="{html.escape(t.school)} {_pct(p)}">{art}</span>')
        key += (f'<li><i style="--c:{color}"></i>{_team_link(t.school, fbs, 2)}'
                f'<b>{_pct(p)}</b></li>')
    if rest >= 0.001:
        segs += (f'<span class="ts-seg rest" style="flex-grow:{rest:.4f}" '
                 f'title="Everyone else {_pct(rest)}"></span>')
        key += f'<li><i class="rest"></i>Everyone else<b>{_pct(rest)}</b></li>'
    said = ", ".join(f"{by_idx[r['idx']].school} {_pct(r['p']['win_conference'])}"
                     for r in shown)
    return (f'<figure class="titleshare"><figcaption>Who wins the '
            f'{html.escape(conf)}</figcaption><div class="ts-bar" role="img" '
            f'aria-label="Who wins the {html.escape(conf)}: {html.escape(said)}">'
            f'{segs}</div><ol class="ts-key">{key}</ol></figure>')


def conference_body(state, conf, rows, n_sims, pairs=None, stakes=None):
    fbs = {t.school for t in state.fbs_teams}
    by_idx = {t.idx: t for t in state.teams}
    names = {i: t.school for i, t in by_idx.items()}
    logos = {i: t.logo for i, t in by_idx.items() if t.logo}
    colors = {i: t.color for i, t in by_idx.items() if t.color}
    abbrs = {i: getattr(t, "abbreviation", "") for i, t in by_idx.items()}
    now = datetime.now(timezone.utc)
    pairs = [x for x in pairs or () if x["p"] >= RECORD_FLOOR][:6]
    share = _title_share(rows, conf, by_idx, fbs) if rows else ""

    body = ""
    for r in rows:
        art = (f'<img src="{html.escape(logos[r["idx"]])}" alt="" width="20" '
               f'height="20">' if logos.get(r["idx"]) else "")
        p = r["p"]
        body += (f'<tr><td><a href="../../team/{slug(r["team"])}/">{art}'
                 f'{html.escape(r["team"])}</a></td>'
                 f'<td class="num">{r["conf_record"]}</td>'
                 f'<td class="num ovr">{r["record"]}</td>'
                 f'{_bar_cell(p["make_conf_title_game"])}'
                 f'{_bar_cell(p["win_conference"], True)}'
                 f'{_bar_cell(p["make_playoff"])}</tr>\n')
    table = (f'<div class="tablewrap"><table class="confrace"><thead><tr>'
             f'<th>Team</th><th class="num">Conf</th><th class="num ovr">Overall</th>'
             f'<th class="num">Title game</th><th class="num">Wins {html.escape(conf)}</th>'
             f'<th class="num">Playoff</th></tr></thead>\n<tbody>{body}</tbody></table></div>')

    matchups = ""
    if pairs:
        matchups = ("<h2>Likeliest title games</h2>\n<ol class=\"pairs\">"
                    + "".join(_pair_html(x, logos, colors, names, fbs, abbrs)
                              for x in pairs)
                    + "</ol>")

    deciders = ""
    games = [r for r in (stakes.week(None, expected=True) if stakes else [])
             if r["value"] >= 0.005][:8]
    if games:
        what = "Title game places this game is expected to move"
        deciders = (f"<h2>The games that decide the race</h2>\n"
                    f"{_stakes_head('Title game places in play', 'When')}\n"
                    f"<ol class=\"stakes detail weeks\">\n"
                    f"{_stakes_list(games, state, 2, True, weeks=True, what=what, metric='title game')}\n</ol>")

    others = " ".join(f'<a href="../{conf_slug(c)}/">{html.escape(c)}</a>'
                      for c in conferences(state.fbs_teams) if c != conf)
    return f"""<h1>The {html.escape(conf)} title race</h1>
<p class="foot">Updated {now:%B} {now.day}, after week {state.current_week() - 1},
from {_sims(n_sims)} simulated seasons.</p>
{share}
{table}
{matchups}
{deciders}
<h2>How the {html.escape(conf)} breaks ties</h2>
{_tiebreak_html(conf, state.year)}
{_faq_html(conference_questions(state, conf, rows, pairs))}
<p class="otherconfs">Other conferences: {others}</p>
<p><a href="../../">Back to the guide</a></p>"""


def ccg_pairs(league, state) -> dict:
    """The title game pairings each conference's simulations produced, as the
    site publishes them: the likeliest first, with how often each side wins."""
    names = league.names
    by_name = {t.school: t.idx for t in state.teams}
    out = {}
    for conf, d in league.ccg_pairs.items():
        cols, tog, beat = d["cols"], d["together"], d["beat"]
        rows = []
        for i in range(cols.size):
            for j in range(i + 1, cols.size):
                n = int(tog[i, j])
                if not n:
                    continue
                a, b = names[cols[i]], names[cols[j]]
                wa = beat[i, j] / n
                # The more likely winner of the pairing goes first.
                if wa < 0.5:
                    a, b, wa = b, a, 1 - wa
                rows.append({"a": by_name[a], "b": by_name[b],
                             "p": n / league.n_sims, "a_wins": float(wa)})
        rows.sort(key=lambda r: -r["p"])
        out[conf] = rows[:10]
    return out


def _races_home(state, league) -> str:
    """Each conference's favourite, under the week's games on the front page."""
    if not league:
        return ""
    logos = {t.idx: t.logo for t in state.teams}
    colors = {t.idx: t.color for t in state.teams if t.color}
    items = ""
    for c in conferences(state.fbs_teams):
        rows = sorted((r for r in league if r["conference"] == c),
                      key=lambda r: -r["p"]["win_conference"])
        if not rows:
            continue
        top = rows[0]
        art = (f'<img src="{html.escape(logos[top["idx"]])}" alt="" width="20" '
               f'height="20">' if logos.get(top["idx"]) else '<span class="noart"></span>')
        # Who wins it, a slice per contender in its own colour.
        strip = "".join(
            f'<i style="flex-grow:{r["p"]["win_conference"]:.4f};'
            f'--c:{html.escape(colors.get(r["idx"]) or "var(--ink-2)")}"></i>'
            for r in rows[:5] if r["p"]["win_conference"] >= 0.02)
        rest = 1 - sum(r["p"]["win_conference"] for r in rows[:5]
                       if r["p"]["win_conference"] >= 0.02)
        if rest >= 0.005:
            strip += f'<i class="rest" style="flex-grow:{rest:.4f}"></i>'
        items += (f'<li><a href="conference/{conf_slug(c)}/">'
                  f'<span class="cn">{html.escape(c)}</span>'
                  f'<span class="fav">{art}{html.escape(top["team"])}'
                  f'<b>{_pct(top["p"]["win_conference"], 0)}</b></span>'
                  f'<span class="strip" aria-hidden="true">{strip}</span></a></li>\n')
    if not items:
        return ""
    return f"""<section class="races-home">
<h2>Conference title races</h2>
<ul class="races">
{items}</ul>
</section>"""


# Projected rankings
#
# The committee's top 25 as the model has it on the results so far. It is
# the ranking a sample season shows for its weeks already played, with the
# real scores and no committee noise.

def rankings_head(state, projection, n_sims, card=None):
    week = (projection or {}).get("week") or state.current_week() - 1
    url = f"{SITE_URL}/cfp-rankings/"
    title = f"Projected CFP rankings after week {week} ({state.year}) | {SITE_NAME}"
    desc = (f"The College Football Playoff committee's top 25 as the model "
            f"expects it after week {week}, the field if the season ended "
            f"today and each team's playoff odds from {_sims(n_sims)} "
            f"simulated seasons.")
    return _head(title, desc, url, card=card,
                 card_alt=f"Projected CFP rankings, week {week}",
                 ld=[_page_ld(title, desc, url),
                     _crumbs([(SITE_NAME, SITE_URL + "/"),
                              ("Projected CFP rankings", url)])])


def _field_html(projection, by_idx, fbs) -> str:
    field = projection.get("field") or []
    if len(field) != 12:
        return ""
    seed = {f["seed"]: f for f in field}

    def team(f, depth=1):
        t = by_idx[f["team"]]
        art = (f'<img src="{html.escape(t.logo)}" alt="" width="20" height="20">'
               if t.logo else '<span class="noart"></span>')
        how = f["how"]
        if how.endswith(" champion"):
            how = how[:-len(" champion")] + " leader"
        elif how.startswith("highest ranked Group of Six"):
            how = "best Group of Six"
        elif how == "ranked in the top 12":
            how = "top 12"
        else:
            how = ""
        note = f'<small>{html.escape(how)}</small>' if how else ""
        return (f'<span class="ft"><b class="sd">{f["seed"]}</b>{art}'
                f'{_team_link(t.school, fbs, depth)}{note}</span>')

    # Bracket order, top to bottom: each bye waits for the winner of the
    # first-round game it is drawn against, which the higher seed hosts.
    pods = "".join(
        f'<li class="pod"><span class="pod-game">{team(seed[a])}{team(seed[b])}</span>'
        f'<span class="pod-line" aria-hidden="true"></span>'
        f'<span class="pod-bye">{team(seed[bye])}</span></li>'
        for bye, a, b in ((1, 8, 9), (4, 5, 12), (3, 6, 11), (2, 7, 10)))
    out_ = "".join(
        f'<li><img src="{html.escape(by_idx[i].logo)}" alt="" width="20" height="20">'
        f'{_team_link(by_idx[i].school, fbs, 1)}</li>' if by_idx[i].logo
        else f'<li>{_team_link(by_idx[i].school, fbs, 1)}</li>'
        for i in projection.get("out") or [] if i in by_idx)
    return f"""<section class="fieldnow">
<h2>If the season ended today</h2>
<div class="br-head" aria-hidden="true"><span>First round, at the higher seed</span>
<span>First-round byes</span></div>
<ol class="pods">{pods}</ol>
""" + (f'<h3>First teams out</h3>\n<ul class="outs">{out_}</ul>\n' if out_ else "") + "</section>"


def rankings_body(state, projection, odds=()):
    if not projection or not projection.get("order"):
        return """<h1>Projected CFP rankings</h1>
<p class="lead">The projection returns once enough of the season has been
played for the committee to rank teams.</p>
<p><a href="../">Back to the guide</a></p>"""
    by_idx = {t.idx: t for t in state.teams}
    fbs = {t.school for t in state.fbs_teams}
    chance = dict(odds)
    week = projection["week"]
    now = datetime.now(timezone.utc)
    order = [i for i in projection["order"] if i in by_idx][:25]

    rows = ""
    for n, i in enumerate(order, 1):
        t = by_idx[i]
        w, l, _, _, _ = record(state, i)
        art = (f'<img src="{html.escape(t.logo)}" alt="" width="22" height="22">'
               if t.logo else '<span class="noart"></span>')
        p = chance.get(t.school)
        odds = (_bar_cell(p, True) if p is not None
                else '<td class="num"><b>-</b></td>')
        rows += (f'<tr><td class="rk">{n}</td>'
                 f'<td><a href="../team/{slug(t.school)}/">{art}{html.escape(t.school)}</a></td>'
                 f'<td class="num">{w}-{l}</td>{odds}</tr>\n')
    head = ('<tr><th class="rk">Rank</th><th>Team</th><th class="num">Record</th>'
            '<th class="num">Final playoff odds</th></tr>')

    return f"""<h1>Projected CFP rankings</h1>
<p class="foot">The committee's top 25 as the model expects it after week {week} of
the {state.year} season. Updated {now:%B} {now.day}.</p>
<div class="rankwrap">
<div class="tablewrap"><table class="rank25"><thead>{head}</thead>
<tbody>{rows}</tbody></table></div>
{_field_html(projection, by_idx, fbs)}
</div>
<p><a href="../teams/">Playoff odds for every team</a> &middot;
<a href="../how-it-works/">Methodology</a></p>"""
