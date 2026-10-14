"""The written pages a crawler reads, built from a fabricated season."""

import html
import json
import re

import pytest

from cfbroot.config import SimConfig
from cfbroot.sim import build_guide, league_all, run, run_league
from cfbroot.web import pages


@pytest.fixture(scope="module")
def season(midseason):
    return midseason


@pytest.fixture(scope="module")
def payload(season):
    team = season.fbs_teams[0].school
    res = run(season, team, SimConfig(n_sims=4000, batch_size=2000))
    guide = build_guide(season, res, primary="make_playoff",
                        week=season.current_week())
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
    assert "remaining schedule" in said
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
    assert f"<title>{team.school} playoff odds" in head
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
        home = pages.home_body(season, season.fbs_teams, 2_500_000, stakes=s)
        assert 'href="this-week/"' in home
        assert home.count('class="stake"') == min(5, len(rows))
    assert "/this-week/" in pages.sitemap(season.fbs_teams)


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
