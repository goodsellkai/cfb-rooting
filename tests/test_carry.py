"""Ratings carried forward for results the published ones have not seen."""

import pytest

from cfbroot.config import ModelParams
from cfbroot.data.season import build_season, carry_ratings

STAMP = "2026-09-20T08:00Z"


def mini(start_date, home_points=38, away_points=10, neutral=False,
         away_fbs=True):
    teams_raw = [
        {"id": 1, "school": "Home", "abbreviation": "H", "conference": "Test",
         "classification": "fbs"},
        {"id": 2, "school": "Away", "abbreviation": "A",
         "conference": "Test" if away_fbs else None,
         "classification": "fbs" if away_fbs else "fcs"},
    ]
    games_raw = [{
        "id": 1, "season": 2026, "week": 4, "season_type": "regular",
        "start_date": start_date, "completed": True, "neutral_site": neutral,
        "conference_game": away_fbs,
        "home_id": 1, "home_team": "Home", "home_conference": "Test",
        "home_points": home_points,
        "away_id": 2, "away_team": "Away",
        "away_conference": "Test" if away_fbs else None,
        "away_points": away_points, "notes": "",
    }]
    if not away_fbs:
        teams_raw = teams_raw[:1]
    state = build_season(
        year=2026, teams_raw=teams_raw,
        conferences_raw=[{"id": 1, "name": "Test", "abbreviation": "T",
                          "classification": "fbs", "member_count": 2}],
        games_raw=games_raw,
        fpi_raw=[{"team": "Home", "fpi": 5.0}, {"team": "Away", "fpi": 0.0}],
        params=ModelParams(), recalibrate=False)
    state.ratings_updated = STAMP
    return state


def ratings(state):
    return {t.school: t.rating for t in state.teams}


def test_moves_both_teams_by_the_share_of_the_surprise():
    state = mini("2026-09-26T23:00Z")
    p = state.params
    before = ratings(state)
    assert carry_ratings(state) == 1

    # 38-10 against a rating edge of 5 + home field is a 20.25 point surprise.
    surprise = 28 - (5.0 - 0.0 + p.hfa)
    shift = p.carry_gain / (4 + p.carry_offset) * surprise
    after = ratings(state)
    assert after["Home"] == pytest.approx(before["Home"] + shift)
    assert after["Away"] == pytest.approx(before["Away"] - shift)
    assert state.carried_games == 1


def test_a_neutral_site_game_drops_home_field():
    state = mini("2026-09-26T23:00Z", neutral=True)
    p = state.params
    carry_ratings(state)
    shift = p.carry_gain / (4 + p.carry_offset) * (28 - 5.0)
    assert ratings(state)["Home"] == pytest.approx(5.0 + shift)


def test_a_result_the_ratings_have_seen_is_left_alone():
    state = mini("2026-09-19T23:00Z")
    before = ratings(state)
    assert carry_ratings(state) == 0
    assert ratings(state) == before


def test_no_timestamp_means_no_carry():
    state = mini("2026-09-26T23:00Z")
    state.ratings_updated = None
    before = ratings(state)
    assert carry_ratings(state) == 0
    assert ratings(state) == before


def test_games_against_non_fbs_are_left_alone():
    state = mini("2026-09-26T23:00Z", away_fbs=False)
    before = ratings(state)
    assert carry_ratings(state) == 0
    assert ratings(state) == before


def test_an_unplayed_game_does_not_move_anything():
    state = mini("2026-09-26T23:00Z")
    for g in state.games:
        g["status"] = 0
        g["completed"] = False
    before = ratings(state)
    assert carry_ratings(state) == 0
    assert ratings(state) == before
