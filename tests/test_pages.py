"""The written pages a crawler reads, built from a fabricated season."""

import dataclasses
import html
import json
import re

import pytest

from cfbroot.config import SimConfig
from cfbroot.sim import build_guide, league_all, run, run_league
from cfbroot.web import clinch, pages
from cfbroot.web.app import _by_record, _title_game


@pytest.fixture(scope="module")
def season(midseason):
    return midseason


@pytest.fixture(scope="module")
def payload(season):
    team = season.fbs_teams[0].school
    res = run(season, team, SimConfig(n_sims=4000, batch_size=2000))
    # Every week, as the build makes a team's guide; pages pick the week.
    guide = build_guide(season, res, primary="make_playoff", week=None)
    league = run_league(season, SimConfig(n_sims=4000, batch_size=2000))
    return {
        "team": team,
        "n_sims": guide.n_sims,
        "expected_wins": guide.expected_wins,
        "headline": {k: {"p": v.p, "lo": v.lo, "hi": v.hi}
                     for k, v in guide.headline.items()},
        "own_games": [g.as_dict() for g in guide.own_games],
        "games": [g.as_dict() for g in guide.games],
        "league": league_all(season, league),
        "by_record": _by_record(res, season),
        "title_game": _title_game(res),
    }


def blocks(head):
    return [json.loads(m) for m in
            re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                       head, re.S)]


def text(html_str):
    """What a reader sees: tags gone, entities back to characters."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", html_str)))


def test_a_team_page_says_what_the_team_needs(season, payload):
    team = season.fbs_teams[0]
    week = season.current_week()
    body = pages.team_writeup(season, team, payload, week)
    said = text(body)

    assert team.school in said
    assert "playoff odds" in said
    assert f"What {team.school} needs" in said
    assert "after a win" in said.lower()
    assert "{" not in body and "}" not in body     # no unfilled format fields
    # links reach other teams and the hubs, and stay inside the site
    hrefs = re.findall(r'href="([^"]+)"', body)
    assert any(h.startswith("../") and h.endswith("/") for h in hrefs)
    assert "../../teams/" in hrefs and "../../how-it-works/" in hrefs
    assert not any(h.startswith("http") for h in hrefs)


def test_a_team_page_carries_its_structured_data(season, payload):
    team = season.fbs_teams[0]
    head = pages.team_head(season, team, payload, season.current_week())
    kinds = {b["@type"] for b in blocks(head)}
    assert kinds == {"WebPage", "Article", "BreadcrumbList", "FAQPage"}
    assert f"<title>{team.school} playoff chances" in head
    assert "what it needs" in head
    assert 'name="robots"' in head


def test_a_team_page_marks_up_no_events(season, payload):
    """Google reads a SportsEvent as an event to attend and flags each one
    without a venue, which the build does not keep."""
    team = season.fbs_teams[0]
    head = pages.team_head(season, team, payload, season.current_week())
    assert "SportsEvent" not in head
    page = next(b for b in blocks(head) if b["@type"] == "WebPage")
    assert page["about"]["@type"] == "SportsTeam"


def test_a_card_replaces_the_header_photo(season, payload):
    team = season.fbs_teams[0]
    week = season.current_week()
    plain = pages.team_head(season, team, payload, week)
    assert 'content="1280"' in plain
    card = "https://cfbroot.com/card/x-202610091200.jpg"
    head = pages.team_head(season, team, payload, week, card)
    assert f'<meta property="og:image" content="{card}">' in head
    assert f'<meta name="twitter:image" content="{card}">' in head
    assert 'content="1200"' in head and 'content="630"' in head
    article = next(b for b in blocks(head) if b["@type"] == "Article")
    assert article["image"] == [card]


def test_the_card_says_one_number_and_one_call(season, payload):
    team = season.fbs_teams[0]
    week = season.current_week()
    story = pages.card_story(season, team, payload, week)
    assert story["number"] + story["unit"] in story["alt"]
    if story["label"][1] == "playoff":
        p = payload["headline"]["make_playoff"]["p"]
        assert story["number"] == f"{100 * p:.2f}"
    kinds = [k for k, _ in story["line"]]
    assert set(kinds) <= {"t", "hl", "logo"}
    # The side to root for is the one in yellow, and it is a real game.
    if "hl" in kinds:
        root = next(v for k, v in story["line"] if k == "hl")
        assert any(root in (g["home"], g["away"]) for g in payload["games"]
                   if g["week"] == week)


def test_a_team_out_of_every_race_gets_its_wins(season, payload):
    team = season.fbs_teams[0]
    nothing = dict(payload, headline={k: dict(v, p=0.0) for k, v
                                      in payload["headline"].items()})
    story = pages.card_story(season, team, nothing, season.current_week())
    assert story["unit"] == ""
    assert story["number"] == f"{payload['expected_wins']:.1f}"
    assert story["label"] == ["wins expected", "this season"]


def test_a_card_draws(season, payload, tmp_path):
    from PIL import Image
    from cfbroot.web import cards

    class NoLogos(cards.Logos):
        def get(self, url):
            return None

    team = season.fbs_teams[0]
    week = season.current_week()
    story = pages.card_story(season, team, payload, week)
    path = tmp_path / "card.jpg"
    cards.draw_team(story, team, "#bf5700", NoLogos(), {}, path)
    with Image.open(path) as img:
        assert img.size == (1200, 630)
    cards.draw_site(week, [("Alpha", 0.9)], {}, NoLogos(), tmp_path / "s.jpg")
    assert (tmp_path / "s.jpg").stat().st_size > 10_000


def test_the_table_of_every_team_is_a_dataset(season):
    head = pages.teams_head(season, 4000)
    data = next(b for b in blocks(head) if b["@type"] == "Dataset")
    assert str(season.year) in data["name"]
    assert data["isAccessibleForFree"] is True


def test_the_questions_asked_are_the_ones_answered(season, payload):
    """What the markup promises has to be on the page."""
    team = season.fbs_teams[0]
    week = season.current_week()
    head = pages.team_head(season, team, payload, week)
    faq = next(b for b in blocks(head) if b["@type"] == "FAQPage")
    said = text(pages.team_writeup(season, team, payload, week))
    for entry in faq["mainEntity"]:
        assert entry["name"] in said
        first = entry["acceptedAnswer"]["text"].split(",")[0]
        assert first[:40] in said


def test_the_record_matches_the_games_played(season):
    team = season.fbs_teams[0]
    w, l, cw, cl, results = pages.record(season, team.idx)
    played = [g for g in season.games
              if g["completed"] and not g["is_ccg"]
              and team.idx in (g["home_idx"], g["away_idx"])]
    assert w + l == len(played) == len(results)
    assert cw <= w and cl <= l


def test_the_home_page_answers_its_own_questions(season):
    race = [(t.school, 0.9 - 0.1 * i) for i, t in enumerate(season.fbs_teams[:3])]
    head = pages.home_head(season, 2_500_000, race)
    body = pages.home_body(season, season.fbs_teams, 2_500_000, race, race)
    faq = next(b for b in blocks(head) if b["@type"] == "FAQPage")
    said = text(body)
    for entry in faq["mainEntity"]:
        assert entry["name"] in said
    assert "{" not in body and "}" not in body


def test_every_team_is_listed_and_linked(season):
    teams = season.fbs_teams
    body = pages.teams_body(season, teams, [(t.school, 0.5) for t in teams])
    for t in teams:
        assert f'href="../team/{pages.slug(t.school)}/"' in body
    lines = pages.llms_txt(season, teams, 2_500_000)
    assert lines.count("/team/") == len(teams)
    assert lines.startswith("# ")


def test_robots_lets_the_assistants_in_and_keeps_them_out_of_the_data():
    txt = pages.robots()
    assert "User-agent: GPTBot" in txt and "User-agent: Googlebot" in txt
    assert txt.count("Disallow: /data/") >= 2
    assert "Sitemap: " in txt


def test_csp_lets_the_analytics_beacon_through():
    """Cloudflare injects its beacon, and a CSP that blocks it counts nobody.

    This happened: the header shipped without these two origins and the site
    recorded zero visits for four days while serving normally.
    """
    csp = next(line for line in pages.headers().splitlines()
               if "Content-Security-Policy" in line)
    assert "https://static.cloudflareinsights.com" in csp
    assert "https://cloudflareinsights.com" in csp


def _stakes_guide(game_rows):
    """A guide cut down to what Stakes reads."""
    return {"games": [{"week": w, "home": h, "away": a, "neutral": False,
                       "start_date": "2026-10-17T19:30Z", "time_set": True,
                       "broadcast": "FOX",
                       "swings": {"make_playoff": {"p_if_home": ph, "p_if_away": pa}}}
                      for w, h, a, ph, pa in game_rows],
            "own_games": []}


def test_stakes_count_the_playoff_places_that_change_hands():
    """Half the sum of every team's swing: the field is a fixed size, so what
    one result gives some teams it takes from others."""
    s = pages.Stakes()
    s.add("Alpha", _stakes_guide([(7, "Beta", "Gamma", 0.70, 0.30),
                                  (7, "Delta", "Echo", 0.52, 0.50)]))
    s.add("Zeta", _stakes_guide([(7, "Beta", "Gamma", 0.10, 0.50),
                                 (7, "Delta", "Echo", 0.49, 0.51)]))
    rows = s.week(7)
    assert [r["game"]["home"] for r in rows] == ["Beta", "Delta"]
    assert abs(rows[0]["spots"] - 0.40) < 1e-9          # (0.40 + 0.40) / 2
    assert rows[0]["teams"][0][0] in ("Alpha", "Zeta")
    assert s.week(8) == []


def test_the_week_page_ranks_the_games_and_links_the_teams(season, payload):
    s = pages.Stakes()
    for t in season.fbs_teams[:3]:
        s.add(t.school, payload)
    week = season.default_week() or season.current_week()
    rows = s.week(week)
    body = pages.this_week_body(season, 2_500_000, s)
    head = pages.this_week_head(season, 2_500_000)
    assert f"week {week}" in body
    assert f"<title>Week {week} college football games" in head
    if rows:
        assert body.count("<li>") == len(rows)
        assert 'href="../team/' in body
        assert "Playoff spots expected to move" in body and 'class="st"' in body
        home = pages.home_body(season, season.fbs_teams, 2_500_000, stakes=s)
        assert 'href="this-week/"' in home
        assert home.count('class="stake"') == min(5, len(rows))


def test_kickoffs_are_given_in_eastern_time():
    assert pages._kickoff({"start_date": "2026-10-17T19:30Z", "broadcast": "FOX"}) \
        == "Sat 3:30 PM ET, FOX"
    # After the clocks go back on the first Sunday of November.
    assert pages._kickoff({"start_date": "2026-11-07T20:00Z"}) == "Sat 3:00 PM ET"
    assert pages._kickoff({"start_date": "2026-11-07T05:00Z", "time_set": False}) \
        == "Sat, time TBA"


def test_the_missing_page_works_at_any_depth():
    """It is served for any address, so every path in it starts at the root.
    Relative ones once left it unstyled and without its script one folder
    down."""
    from cfbroot.web.export import load_template, render
    head = pages.not_found_head()
    assert 'content="noindex"' in head and 'rel="canonical"' not in head
    html = render(load_template("t"), head, pages.not_found("/"), 0,
                  info=True, base="/")
    assert 'href="/static/app.css' in html and 'src="/static/app.js' in html
    assert 'href="static/' not in html and 'src="static/' not in html
    assert 'class="brand" href="/"' in html
    assert 'href="/this-week/"' in html and 'href="/teams/"' in html
    assert "window.CFBROOT_INFO = true" in html
    assert 'id="didyoumean"' in html


def test_what_a_team_needs_reads_its_records_and_games(season, payload):
    team = season.fbs_teams[0]
    said = text(pages.needs_text(season, team, payload))
    recs = pages.record_rows(payload)
    assert recs and recs == sorted(recs, key=lambda r: -r["wins"])
    assert "The game that decides the most is" in said
    assert sum(r["p"] for r in payload["by_record"]) == pytest.approx(1.0)
    # every row is the team's own game, with the two results either side of now
    for r in pages.own_rows(team, payload):
        assert r["opponent"] != team.school
        assert 0.0 <= min(r["win"], r["lose"]) <= max(r["win"], r["lose"]) <= 1.0


def test_the_questions_include_what_the_team_needs(season, payload):
    team = season.fbs_teams[0]
    qs = [q for q, _ in pages.team_questions(season, team, payload,
                                             season.current_week())]
    assert f"What does {team.school} need to make the playoff?" in qs


def test_a_conference_page_has_its_race_matchups_and_tiebreakers(season, payload):
    conf = "SEC"
    rows = pages.conference_rows(season, conf, payload["league"])
    assert rows and all(r["team"] in {t.school for t in season.fbs_teams} for r in rows)
    odds = [r["p"]["win_conference"] for r in rows]
    assert odds == sorted(odds, reverse=True)
    a, b = rows[0]["idx"], rows[1]["idx"]
    pairs = [{"a": a, "b": b, "p": 0.4, "a_wins": 0.6}]
    stakes = pages.Stakes("make_conf_title_game")
    for t in season.fbs_teams:
        if t.conference == conf:
            stakes.add(t.school, payload)
    body = pages.conference_body(season, conf, rows, payload["n_sims"], pairs, stakes)
    said = text(body)
    assert f"The {conf} title race" in said
    assert "Likeliest title games" in said and "40.0% of seasons" in said
    # who wins it, as one bar with a slice for the favourite and a key under it
    assert "Who wins the SEC" in said and 'class="ts-seg"' in body
    assert f"{rows[0]['team']}" in text(body.split('class="ts-key"')[1].split("</ol>")[0])
    assert "How the SEC breaks ties" in said and "Head-to-head" in said
    assert "{" not in body and "}" not in body
    head = pages.conference_head(season, conf, rows, payload["n_sims"], pairs)
    kinds = {b["@type"] for b in blocks(head)}
    assert {"WebPage", "BreadcrumbList", "FAQPage"} <= kinds


def test_the_title_game_pairings_are_published_likeliest_first(season):
    league = run_league(season, SimConfig(n_sims=3000, batch_size=1500))
    pairs = pages.ccg_pairs(league, season)
    assert set(pairs) == set(league.ccg_pairs)
    for rows in pairs.values():
        assert rows == sorted(rows, key=lambda r: -r["p"])
        assert all(r["a_wins"] >= 0.5 and r["a"] != r["b"] for r in rows)
        assert sum(r["p"] for r in rows) <= 1.0 + 1e-9


def test_the_rankings_page_lists_25_and_the_field(season):
    from cfbroot.sim.sample import committee_now
    proj = committee_now(season)
    body = pages.rankings_body(season, proj, [])
    said = text(body)
    assert "Projected CFP rankings" in said
    assert body.count('<td class="rk">') == 25
    assert "If the season ended today" in said and "First-round byes" in said
    # the field as the bracket's first two rounds: a bye beside each game
    assert body.count('<li class="pod">') == 4 and body.count('class="sd"') == 12
    assert "{" not in body and "}" not in body
    # with nothing to show it says so rather than failing
    assert "returns once" in text(pages.rankings_body(season, None))


def test_the_rankings_page_shows_the_committee_once_it_ranks(season):
    """Once the committee publishes, its order is the page's, and the field if
    the season ended today is picked from it."""
    from cfbroot.sim.sample import committee_now
    proj = committee_now(season)
    order = list(proj["order"][:25])
    order[0], order[1] = order[1], order[0]
    saved = dict(season.polls), dict(season.poll_weeks)
    try:
        season.polls["cfp"] = {i: k + 1 for k, i in enumerate(order)}
        season.poll_weeks["cfp"] = 10
        body = pages.rankings_body(season, proj, [])
        head = pages.rankings_head(season, proj, 2_500_000)
    finally:
        season.polls.clear(), season.polls.update(saved[0])
        season.poll_weeks.clear(), season.poll_weeks.update(saved[1])
    names = {t.idx: t.school for t in season.teams}
    said = text(body)
    assert said.strip().startswith("CFP rankings") and "selection committee's top 25" in said
    first, second = names[order[0]], names[order[1]]
    assert said.index(first) < said.index(second)
    assert body.count('<li class="pod">') == 4
    assert "<title>CFP rankings, week 10" in head
    assert 'class="mv' not in body


def test_the_rankings_page_moves_ahead_of_the_committee_after_a_week_of_games(season):
    """Once games have finished since the committee ranked, the page is its
    next ranking as the model expects it, with each team's move from the last."""
    from cfbroot.sim.sample import committee_now
    proj = dict(committee_now(season), since=10)
    order = list(proj["order"][:25])
    was = [order[1], order[0]] + order[2:24]      # two swapped, one not ranked
    saved = dict(season.polls), dict(season.poll_weeks)
    try:
        season.polls["cfp"] = {i: k + 1 for k, i in enumerate(was)}
        season.poll_weeks["cfp"] = 10
        body = pages.rankings_body(season, proj, [])
        head = pages.rankings_head(season, proj, 2_500_000)
        home = pages._field_home(season, proj)
    finally:
        season.polls.clear(), season.polls.update(saved[0])
        season.poll_weeks.clear(), season.poll_weeks.update(saved[1])
    names = {t.idx: t.school for t in season.teams}
    said = text(body)
    assert said.strip().startswith("Projected CFP rankings")
    assert "next top 25" in said and "against its last ranking" in said
    assert said.index(names[order[0]]) < said.index(names[order[1]])
    assert body.count('<td class="rk">') == 25 and body.count('<td class="mv') == 25
    assert body.count('class="mv up"') == 1 and body.count('class="mv down"') == 1
    assert body.count('<td class="mv">new</td>') == 1
    assert "<title>Projected CFP rankings after week" in head
    assert ">Projected CFP rankings</a>" in home


def test_after_the_title_games_the_field_has_champions(season):
    from cfbroot.sim.sample import committee_now
    proj = dict(committee_now(season), since=14, final=True)
    saved = dict(season.polls), dict(season.poll_weeks)
    try:
        season.polls["cfp"] = {i: k + 1 for k, i in enumerate(proj["order"][:25])}
        season.poll_weeks["cfp"] = 14
        ahead = text(pages.rankings_body(season, proj, []))
        out = text(pages.rankings_body(season, dict(proj, since=None), []))
    finally:
        season.polls.clear(), season.polls.update(saved[0])
        season.poll_weeks.clear(), season.poll_weeks.update(saved[1])
    assert "final top 25 as the model expects it after the title games" in ahead
    assert "The projected playoff field" in ahead and "champion" in ahead
    assert "leader" not in ahead and "If the season ended today" not in ahead
    # and once the committee has picked it, it is the field
    assert out.strip().startswith("CFP rankings") and "The playoff field" in out
    assert "projected" not in out.lower()


def test_the_sitemap_gives_a_card_where_there_is_one_and_no_false_date():
    home, how = pages.SITE_URL + "/", pages.SITE_URL + "/how-it-works/"
    xml = pages.sitemap([home, how], {home: "https://example.invalid/c.jpg"})
    assert xml.count("<url>") == 2
    assert xml.count("<image:loc>") == 1 and "xmlns:image=" in xml
    # The methodology's text does not change with each build's numbers.
    assert xml.count("<lastmod>") == 1


def _front(season, league, out):
    """The pages that go out last, and the addresses the sitemap gave them."""
    from cfbroot.web.export import load_template, write_front

    teams = season.fbs_teams
    write_front(out, season, teams, 1000, [(t.school, 0.5) for t in teams],
                load_template("t", teams), stakes=pages.Stakes(), league=league)
    return re.findall(r"<loc>(.*?)</loc>", (out / "sitemap.xml").read_text("utf-8"))


def test_the_sitemap_lists_the_pages_the_build_wrote(season, payload, tmp_path):
    for folder in ("team/some-team", "data/team", "static/x"):
        (tmp_path / folder).mkdir(parents=True)
        (tmp_path / folder / "index.html").write_text("x", encoding="utf-8")
    listed = _front(season, payload["league"], tmp_path)
    assert listed[0] == pages.SITE_URL + "/"
    assert pages.SITE_URL + "/team/some-team/" in listed
    for page in ("this-week", "cfp-rankings", "how-it-works", "teams"):
        assert f"{pages.SITE_URL}/{page}/" in listed
    assert any("/conference/" in u for u in listed)
    assert not any("/data/" in u or "/static/" in u for u in listed)
    # Each address is a file, and each page bar those two folders has an address.
    root = len(pages.SITE_URL) + 1
    assert all((tmp_path / u[root:] / "index.html").exists() for u in listed)
    assert len(listed) == len(list(tmp_path.rglob("index.html"))) - 2


def test_the_sitemap_leaves_out_a_page_the_build_skipped(season, tmp_path):
    # A conference with no rows gets no page, so it must not get an address.
    assert not any("/conference/" in u for u in _front(season, [], tmp_path))


def test_an_independent_is_not_given_a_title_race(season, payload):
    # Notre Dame and UConn share a label, not a conference with a page.
    team = next(t for t in season.fbs_teams if t.school == payload["team"])
    alone = dataclasses.replace(team, conference="FBS Independents")
    league = [dict(r, conference="FBS Independents") for r in payload["league"]]
    body = pages.team_writeup(season, alone, {**payload, "league": league},
                              season.default_week() or season.current_week())
    assert "conference/fbs-independents" not in body and "race</h2>" not in body


def _own(team, opp, chance, win, lose, week):
    """One of the team's games as a guide carries it, at home."""
    return {"home": team.school, "away": opp, "home_idx": team.idx, "away_idx": -1,
            "week": week, "neutral": False, "p_home_win": chance,
            "swings": {"make_playoff": {"p_if_home": win, "p_if_away": lose,
                                        "reliable": True}}}


def test_the_deciding_game_is_the_toughest_not_the_easiest(season):
    """A loss to a weak team would cost a contender the most, but it almost
    never happens; the game that decides the season is the close one."""
    team = season.fbs_teams[0]
    w = pages.record(season, team.idx)[0]
    payload = {
        "own_games": [_own(team, "Easy State", 0.95, 0.90, 0.30, 10),
                      _own(team, "Big Rival", 0.50, 0.95, 0.60, 11)],
        "by_record": [
            {"wins": w + 2, "losses": 0, "n": 50_000, "p": 0.47, "odds": {"make_playoff": 0.95}},
            {"wins": w + 1, "losses": 1, "n": 50_000, "p": 0.50, "odds": {"make_playoff": 0.70}},
            {"wins": w, "losses": 2, "n": 50_000, "p": 0.03, "odds": {"make_playoff": 0.20}}],
    }
    said = text(pages.needs_text(season, team, payload))
    assert "decides the most is against Big Rival in week 11" in said
    assert f"{team.school} can afford one loss." in said


def test_a_team_that_must_win_out_is_told_so(season):
    team = season.fbs_teams[0]
    w = pages.record(season, team.idx)[0]
    payload = {
        "own_games": [_own(team, "Big Rival", 0.55, 0.80, 0.10, 11)],
        "by_record": [
            {"wins": w + 1, "losses": 0, "n": 50_000, "p": 0.55, "odds": {"make_playoff": 0.80}},
            {"wins": w, "losses": 1, "n": 50_000, "p": 0.45, "odds": {"make_playoff": 0.10}}],
    }
    said = text(pages.needs_text(season, team, payload))
    assert f"{team.school} has to win out." in said
    assert "Its last game is against Big Rival" in said


def _raced(season, wins_for):
    """The season with its conference's early games played, a team in each
    finish of them decided by ``wins_for``, for the title race pages."""
    state = dataclasses.replace(season, games=[dict(g) for g in season.games])
    for g in state.games:
        if g.get("conference_game") and not g.get("is_ccg") and not g["completed"]:
            won = wins_for(g)
            if won is None:
                continue
            g["completed"] = True
            g["home_points"], g["away_points"] = (24, 17) if won == g["home_idx"] else (17, 24)
    return state


def test_a_team_in_the_race_is_told_what_it_needs(season, payload):
    team = next(t for t in season.fbs_teams if pages.has_conference(t))
    panel = pages.clinch_panel(season, team, 1)
    if clinch.conference_race(season, team.conference).status(team.idx) is None:
        assert "clinchpanel" in panel and f"Reaching the {team.conference} title game" in panel
        qs = dict(pages.team_questions(season, team, payload, 1, plain=True))
        question = f"How can {team.school} clinch a spot in the {team.conference} championship game?"
        assert question in qs and "<" not in qs[question]
        assert "clinchpanel" in pages.team_writeup(season, team, payload, 1)


def test_a_team_with_nothing_to_clinch_gets_no_panel_or_question(season, payload):
    conf = next(t.conference for t in season.fbs_teams if pages.has_conference(t))
    members = [t for t in season.fbs_teams if t.conference == conf]
    # Everyone's conference games played, the first team winning all of its,
    # the last losing all of its.
    first, last = members[0].idx, members[-1].idx
    ids = {t.idx for t in members}

    def winner(g):
        if g["home_idx"] not in ids or g["away_idx"] not in ids:
            return None
        if first in (g["home_idx"], g["away_idx"]):
            return first
        if last in (g["home_idx"], g["away_idx"]):
            return g["home_idx"] if g["away_idx"] == last else g["away_idx"]
        return g["home_idx"]

    state = _raced(season, winner)
    race = clinch.conference_race(state, conf)
    assert race.status(last) == "out"
    gone = next(t for t in members if t.idx == last)
    assert pages.clinch_panel(state, gone, 1) == ""
    asked = dict(pages.team_questions(state, gone, payload, 1, plain=True))
    assert not any("clinch" in q for q in asked)
    assert "clinchpanel" not in pages.team_writeup(state, gone, payload, 1)

    top = next(t for t in members if t.idx == first)
    assert race.status(first) == "clinched"
    assert pages.clinch_panel(state, top, 1) == ""
    asked = dict(pages.team_questions(state, top, payload, 1, plain=True))
    assert f"Has {top.school} clinched a spot in the {conf} championship game?" in asked

    rows = pages.conference_rows(state, conf, payload["league"])
    body = pages.conference_body(state, conf, rows, payload["n_sims"])
    assert 'id="clinching"' in body and 'id="tiebreakers"' in body
    assert "Out of the race" in body


def test_an_independent_has_no_clinching_panel(season, payload):
    team = dataclasses.replace(season.fbs_teams[0], conference="FBS Independents")
    assert pages.clinch_panel(season, team, 1) == ""
    assert pages.clinch_question(season, team, 1, str) is None


def test_the_tiebreakers_say_how_they_run(season):
    for conf, words in (("SEC", "Capped scoring margin"), ("Big Ten", "SportSource rating"),
                        ("Big 12", "Total wins"), ("Pac-12", "Three or more tied"),
                        ("Sun Belt", "Next in the division"), ("ACC", "eight or nine")):
        said = pages._tiebreak_html(conf, 2026)
        assert words in said, conf
        assert "<ol" in said
    assert "start again at step 1" in pages._tiebreak_html("SEC", 2026)


def test_a_row_says_what_happens_past_its_sure_thing():
    names = {1: "Alpha", 2: "Beta", 3: "Gamma"}
    row = {"state": "help", "record": (7, 2), "same_games": True, "chasing": [1, 2, 3],
           "already": [], "ways": None, "beats": []}
    can = pages._need_text({**row, "beyond": True}, 2, str, names)
    assert "no more than one of Alpha, Beta and Gamma finishes 7-2 or better" in can
    assert "If more do, it can still get in on tiebreakers." in can
    assert "If more do, it is out." in pages._need_text({**row, "beyond": False}, 2, str, names)
    assert "comes down to tiebreakers" in pages._need_text({**row, "beyond": None}, 2, str, names)
    two = pages._need_text({**row, "chasing": [1, 2], "beyond": True}, 2, str, names)
    assert "In if Alpha or Beta finishes below 7-2. If neither does, it can still" in two
    one = pages._need_text({**row, "chasing": [1], "already": [2], "beyond": False}, 2, str, names)
    assert "Beta is already there. In if Alpha finishes below 7-2. If not, it is out." in one


def test_a_tie_worked_out_says_who_and_how():
    names = {1: "Alpha", 2: "Beta", 3: "Gamma"}
    steps = lambda code: pages._step_title("SEC", code)
    row = {"state": "help", "record": (7, 2), "same_games": True, "chasing": [1, 2],
           "already": [], "ways": None, "beats": [], "beyond": True,
           "ties": [{"ahead": [], "level": [1, 2], "verdict": "out", "step": 1,
                     "shown": {7: "0-2", 1: "2-0", 2: "1-1"}, "decider": None}]}
    said = pages._need_text(row, 2, str, names | {7: "Team"}, step_name=steps)
    assert ("In if Alpha or Beta finishes below 7-2. If neither does: "
            "level with Alpha and Beta, out on head-to-head.") in said
    # The list on a team's page shows what the step found.
    drawn = pages._need_text(row, 2, str, names | {7: "Team"}, lambda ts: "CHIPS", steps)
    assert "CHIPS" in drawn and 'class="cl-ties"' in drawn and "Alpha 2-0" in drawn
    assert "<small>head-to-head: " in drawn          # the tag says it is out
    # Too many to list in a sentence, and it says what it can of them all.
    many = dict(row, ties=row["ties"] * 4)
    assert "it can still get in on tiebreakers" in pages._need_text(many, 2, str, names | {7: "Team"})
    # A tie only a later result settles, and one only the last step can.
    tied = {"ahead": [3], "level": [1], "verdict": "depends", "step": None, "shown": None,
            "team": 7, "ways": [[(2, 3)]], "else": "out"}
    who, why = pages._tie_parts(tied, str, names, steps)
    assert who == "behind Gamma, level with Alpha" and why == "in with Beta beating Gamma, otherwise out"
    own = dict(tied, ways=[[(7, 2)], [(3, 1), (2, 7)]], **{"else": "open"})
    assert pages._tie_parts(own, str, names | {7: "Team"}, steps)[1] == (
        "in with a win over Beta; or with Gamma beating Alpha and a loss to Beta, "
        "otherwise it comes down to the last step")
    unsaid = dict(tied, ways=None)
    assert pages._tie_parts(unsaid, str, names, steps)[1] == "depends on other results"
    last = dict(tied, verdict="open", step=7, ways=None)
    assert pages._tie_parts(last, str, names, steps)[1] == "comes down to capped scoring margin"


def test_a_week_says_what_every_way_shares_once():
    names = {1: "Miami", 2: "Duke", 3: "Georgia Tech", 4: "Clemson", 5: "North Carolina",
             6: "Louisville", 7: "Wake Forest", 8: "SMU"}
    ways = [[(1, 2), (3, 4), (5, 6)], [(1, 2), (7, 8), (5, 6)]]
    assert pages._week_text(ways, 1, str, names) == (
        "with a win over Duke, North Carolina beating Louisville, and either "
        "Georgia Tech beating Clemson or Wake Forest beating SMU")
    assert pages._week_text([[(1, 2)]], 1, str, names) == "with a win over Duke"
    assert pages._week_text([[(2, 1)], [(3, 4)]], 1, str, names, more=True) == (
        "with either a loss to Duke or Georgia Tech beating Clemson; or in other ways")
    # nothing shared: each way stands alone
    assert pages._week_text([[(1, 2), (3, 4)], [(5, 6), (7, 8)]], 1, str, names) == (
        "with a win over Duke and Georgia Tech beating Clemson; or with "
        "North Carolina beating Louisville and Wake Forest beating SMU")


def test_a_week_is_drawn_as_results_with_the_either_ones_boxed(season):
    by_idx = {t.idx: t for t in season.teams}
    a, b, c, d, e, f = [t.idx for t in season.fbs_teams[:6]]
    out = pages._recipe([[(a, b), (c, d)], [(a, b), (e, f)]], a, by_idx,
                        lambda school: f"../{pages.slug(school)}/")
    assert out.count('class="rs own"') == 1 and out.count('class="rs"') == 2
    assert out.index('class="rs own"') < out.index('class="rs-any"')
    assert "<em>and either</em>" in out
    assert f'href="../{pages.slug(by_idx[b].school)}/"' in out


def test_the_conference_page_folds_every_scenario_under_each_team(season):
    conf = next(t.conference for t in season.fbs_teams if pages.has_conference(t))
    race = clinch.conference_race(season, conf)
    alive = [t for t in race.teams if race.path(t, 1)]
    rows = [{"idx": t.idx, "team": t.school} for t in season.fbs_teams if t.conference == conf]
    by_idx = {t.idx: t for t in season.teams}
    body = pages._clinch_html(race, rows, by_idx, {t.school for t in season.fbs_teams}, 1)
    assert body.count('<details class="cg-more">') == len(alive)
    assert body.count('<ol class="cl-rows">') == len(alive)
    assert "<details class=\"cg-more\" open" not in body
