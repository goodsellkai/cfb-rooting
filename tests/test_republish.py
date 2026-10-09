"""The stand-in season a republish builds from the published state file.

A republish hands `pages.py` its own small objects instead of a real
`SeasonState`. Nothing at runtime would notice if they stopped matching: the
pages would still be written, just with a record of 0-0 or a missing sentence.
So the contract is pinned here.
"""

import json

from cfbroot.web import pages
from cfbroot.web.republish import Season


def published(payload_games, teams):
    """The shape `data/state.json` has, cut down to what `Season` reads."""
    return {
        "year": 2026,
        "current_week": 6,
        "default_week": 5,
        "sample_seasons": 2,
        "team_index": {
            str(t["idx"]): {"name": t["name"], "abbr": t["name"][:3].upper(),
                            "logo": t.get("logo", ""), "color": t.get("color"),
                            "conference": t["conference"], "fbs": t["fbs"],
                            "rating": t["rating"]}
            for t in teams
        },
        "teams": [{"idx": t["idx"], "name": t["name"],
                   "conference": t["conference"], "rating": t["rating"],
                   "abbr": t["name"][:3].upper(),
                   "slug": pages.slug(t["name"])}
                  for t in teams if t["fbs"]],
        "played": payload_games,
    }


TEAMS = [
    {"idx": 0, "name": "Alpha", "conference": "Big Test", "rating": 7.0,
     "fbs": True, "logo": "https://example.invalid/a.png", "color": "#123456"},
    {"idx": 1, "name": "Beta", "conference": "Big Test", "rating": 2.0,
     "fbs": True, "logo": "https://example.invalid/b.png", "color": "#654321"},
    {"idx": 2, "name": "Gamma State", "conference": None, "rating": -30.0,
     "fbs": False, "logo": "", "color": None},
]

GAMES = [
    # Alpha beat Beta in conference, then lost to a non-FBS side at their place.
    {"week": 1, "home": 0, "away": 1, "home_points": 31, "away_points": 10,
     "neutral": False, "title_game": False, "conference": True},
    {"week": 2, "home": 2, "away": 0, "home_points": 21, "away_points": 14,
     "neutral": False, "title_game": False, "conference": False},
    # A title game, which a record ignores.
    {"week": 3, "home": 0, "away": 1, "home_points": 40, "away_points": 0,
     "neutral": True, "title_game": True, "conference": True},
]


def test_season_reads_the_published_file():
    s = Season(published(GAMES, TEAMS))
    assert s.year == 2026
    assert s.current_week() == 6
    assert s.default_week() == 5
    assert [t.school for t in s.fbs_teams] == ["Alpha", "Beta"]
    assert len(s.teams) == 3                    # the non-FBS side is kept
    assert s.fbs_teams[0].logo.endswith("a.png")
    assert s.fbs_teams[0].color == "#123456"


def test_record_matches_what_the_games_say():
    """`pages.record` is the one place a republish rebuilds rather than reads."""
    s = Season(published(GAMES, TEAMS))
    w, l, cw, cl, results = pages.record(s, 0)
    assert (w, l) == (1, 1)                     # the title game is excluded
    assert (cw, cl) == (1, 0)
    assert [r["week"] for r in results] == [1, 2]
    assert results[0] == {"week": 1, "won": True, "opponent": "Beta",
                          "at": "", "score": "31-10"}
    assert results[1]["at"] == "at "            # away game keeps the prefix
    assert results[1]["score"] == "14-21"

    w, l, cw, cl, _ = pages.record(s, 1)
    assert (w, l, cw, cl) == (0, 1, 0, 1)


def test_the_writeup_can_quote_espn():
    """`team_writeup` reads `espn_odds` off the team, which republish sets."""
    s = Season(published(GAMES, TEAMS))
    alpha = s.fbs_teams[0]
    assert alpha.espn_odds is None              # until the league table is read
    alpha.espn_odds = {"make_playoff": 0.5}
    assert alpha.espn_odds["make_playoff"] == 0.5


def test_every_attribute_pages_reads_exists():
    """A new read in pages.py should fail here, not silently on the live site."""
    s = Season(published(GAMES, TEAMS))
    for name in ("year", "games", "fbs_teams", "teams", "polls"):
        assert hasattr(s, name), name
    for name in ("current_week", "default_week"):
        assert callable(getattr(s, name)), name
    for name in ("school", "logo", "conference", "color", "idx", "rating",
                 "is_fbs", "espn_odds", "abbreviation"):
        assert hasattr(s.fbs_teams[0], name), name
    # The keys `record` indexes, which are not the keys the site is served.
    for key in ("week", "completed", "is_ccg", "home_idx", "away_idx", "home",
                "away", "home_points", "away_points", "conference_game"):
        assert key in s.games[0], key


def test_the_state_file_round_trips():
    """Whatever Season is given it must not mutate, since it is republished."""
    payload = published(GAMES, TEAMS)
    before = json.dumps(payload, sort_keys=True)
    Season(payload)
    assert json.dumps(payload, sort_keys=True) == before
