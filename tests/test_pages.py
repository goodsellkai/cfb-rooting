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
    assert kinds == {"WebPage", "BreadcrumbList", "FAQPage"}
    assert f"<title>{team.school} playoff odds" in head
    assert 'name="robots"' in head


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
