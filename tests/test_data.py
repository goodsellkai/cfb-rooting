"""Data normalisation and the outcome model."""

import numpy as np
import pytest

from cfbroot.config import ModelParams
from cfbroot.data.loader import default_year
from cfbroot.data.season import (AWAY_WON, HOME_WON, TO_SIMULATE, build_season,
                                 normalise_name)
from cfbroot.data.synthetic import build_payloads, synthetic_season
from cfbroot.model import evaluate, provenance, total_sigma, win_probability

from conftest import make_mini_season


# Name matching

@pytest.mark.parametrize("a,b", [
    ("Ohio State", "ohio state"),
    ("Texas A&M", "Texas A&M "),
    ("Miami (FL)", "Miami FL"),
    ("San José State", "San Jose State"),
    ("Louisiana-Monroe", "Louisiana Monroe"),
    ("University of Alabama", "Alabama"),
])
def test_names_that_should_match_do(a, b):
    assert normalise_name(a) == normalise_name(b)


@pytest.mark.parametrize("a,b", [
    ("Michigan", "Michigan State"),
    ("Miami (FL)", "Miami (OH)"),
    ("Texas", "Texas Tech"),
])
def test_names_that_should_not_match_do_not(a, b):
    assert normalise_name(a) != normalise_name(b)


# Outcome model

def test_even_teams_at_a_neutral_site_are_a_coin_flip():
    p = ModelParams()
    assert float(win_probability(0, 0, True, p)) == pytest.approx(0.5)


def test_home_field_is_worth_a_few_points():
    p = ModelParams()
    assert 0.5 < float(win_probability(0, 0, False, p)) < 0.60


def test_win_probability_is_monotone_in_the_rating_gap():
    p = ModelParams()
    gaps = np.arange(-30, 31, 1.0)
    probs = win_probability(gaps, 0.0, True, p)
    assert np.all(np.diff(probs) > 0)
    assert probs[0] < 0.05 and probs[-1] > 0.95


def test_win_probability_is_symmetric_at_a_neutral_site():
    p = ModelParams()
    a = float(win_probability(14, 0, True, p))
    b = float(win_probability(0, 14, True, p))
    assert a + b == pytest.approx(1.0)


def test_a_two_touchdown_favourite_is_priced_like_the_market():
    """A 14-point home favourite should sit in the low-to-mid 80s by midseason,
    and a little lower before the season, when FPI knows less."""
    import dataclasses

    p = ModelParams()
    mid = dataclasses.replace(p, rating_sd=p.team_error(6))
    pre = dataclasses.replace(p, rating_sd=p.team_error(0))
    assert 0.80 < float(win_probability(14 - p.hfa, 0, False, mid)) < 0.87
    assert 0.76 < float(win_probability(14 - p.hfa, 0, False, pre)) < 0.80


def test_model_parameters_are_fixed_not_refit():
    """Building a season doesn't change the model parameters."""
    from cfbroot.data.synthetic import synthetic_season

    default = ModelParams()
    for played_through in (0, 6, 12):
        s = synthetic_season(played_through=played_through)
        assert s.params.sigma == default.sigma
        assert s.params.hfa == default.hfa
        assert s.params.rating_scale == default.rating_scale


def test_parameters_match_the_documented_calibration():
    """The defaults must stay tied to what cfbroot.calibration and
    cfbroot.spread produce."""
    p = ModelParams()
    assert p.hfa == pytest.approx(2.74, abs=0.15)      # market home field
    assert p.rating_scale == 1.0                       # FPI is points-scaled
    # From 2023-25 results against ESPN's weekly FPI.
    assert p.sigma == pytest.approx(13.86, abs=0.15)
    assert p.team_error(0) == pytest.approx(7.18, abs=0.05)
    assert p.team_error(4) == pytest.approx(5.86, abs=0.05)
    assert p.team_error(30) == pytest.approx(4.68, abs=0.05)
    assert "closing betting lines" in provenance(p)


def test_rating_error_follows_the_week(midseason, preseason):
    """FPI is less sure of a team before any games than after six weeks."""
    p = ModelParams()
    assert preseason.params.rating_sd == pytest.approx(p.team_error(0))
    assert midseason.params.rating_sd == pytest.approx(p.team_error(6))
    assert midseason.params.rating_sd < preseason.params.rating_sd
    # Parameters given explicitly are left alone.
    s = synthetic_season(seed=7, played_through=6, params=ModelParams(rating_sd=3.0))
    assert s.params.rating_sd == 3.0


def test_diagnostics_report_without_changing_anything():
    rng = np.random.default_rng(0)
    n = 500
    rh, ra = rng.normal(0, 10, n), rng.normal(0, 10, n)
    neutral = rng.random(n) < 0.1
    p = ModelParams()
    margin = (rh - ra) + p.hfa * (~neutral) + rng.normal(0, p.sigma, n)
    d = evaluate(rh, ra, neutral, margin, p)
    assert d.n_games == n
    assert 0.0 <= d.brier <= 0.25
    assert 0.5 <= d.accuracy <= 1.0
    # a correctly specified model should sit near sigma * sqrt(2/pi)
    assert d.mean_abs_margin_error == pytest.approx(p.sigma * 0.7979, rel=0.12)


def test_diagnostics_handle_an_empty_season():
    d = evaluate([], [], [], [], ModelParams())
    assert d.n_games == 0
    assert "fixed from a historical calibration" in d.summary()


# Season build

def test_completed_and_pending_games_are_classified_correctly():
    s = make_mini_season([("A", "B", True), ("C", "D", False), ("A", "C", None)])
    by_pair = {(g["home"], g["away"]): g for g in s.games}
    assert by_pair[("A", "B")]["status"] == HOME_WON
    assert by_pair[("C", "D")]["status"] == AWAY_WON
    assert by_pair[("A", "C")]["status"] == TO_SIMULATE
    assert len(s.remaining_games) == 1


def test_non_fbs_opponents_are_registered_but_excluded_from_the_field():
    p = build_payloads(seed=3, played_through=0)
    s = build_season(2026, p["teams"], p["conferences"], p["games"], p["fpi"])
    fcs = [t for t in s.teams if not t.is_fbs]
    assert fcs, "fixture should contain FCS opponents"
    for t in fcs:
        assert t.conf_idx == -1
        assert t.rating == pytest.approx(ModelParams().fcs_rating)


def test_teams_without_a_rating_are_imputed_not_dropped():
    p = build_payloads(seed=3, played_through=0)
    dropped = p["fpi"].pop()["team"]
    s = build_season(2026, p["teams"], p["conferences"], p["games"], p["fpi"])
    t = s.team_by_name(dropped)
    assert t is not None and np.isfinite(t.rating)
    assert t.rating_source == "imputed"
    assert any("median" in n for n in s.notes)


def test_current_week_is_the_first_week_with_an_unplayed_game():
    p = build_payloads(seed=3, played_through=5)
    s = build_season(2026, p["teams"], p["conferences"], p["games"], p["fpi"])
    assert s.current_week() == 6
    assert all(g["status"] != TO_SIMULATE for g in s.games if g["week"] <= 5)


def test_conference_games_are_flagged_only_within_a_conference():
    p = build_payloads(seed=3, played_through=0)
    s = build_season(2026, p["teams"], p["conferences"], p["games"], p["fpi"])
    ki = s.kernel_inputs()
    for i in range(ki.g_home.size):
        h, a = ki.g_home[i], ki.g_away[i]
        same = ki.conf_id[h] >= 0 and ki.conf_id[h] == ki.conf_id[a]
        assert bool(ki.g_conf[i]) == bool(same and ki.is_fbs[h] and ki.is_fbs[a])


def test_team_search_finds_teams_by_prefix():
    p = build_payloads(seed=3, played_through=0)
    s = build_season(2026, p["teams"], p["conferences"], p["games"], p["fpi"])
    hits = s.search_teams("SEC Team 0")
    assert hits and all(h.school.startswith("SEC Team 0") for h in hits)


def test_default_year_rolls_over_in_july():
    import datetime as dt
    assert default_year(dt.date(2026, 9, 5)) == 2026
    assert default_year(dt.date(2027, 1, 10)) == 2026
    assert default_year(dt.date(2026, 3, 1)) == 2025
    assert default_year(dt.date(2026, 7, 1)) == 2026


# Conference title games

def _season_with_note(note, conference_game=True):
    """A 4-team conference plus one extra game carrying ``note``."""
    from cfbroot.data.season import build_season
    teams = ["A", "B", "C", "D"]
    teams_raw = [{"id": i + 1, "school": t, "conference": "Test Conf",
                  "classification": "fbs"} for i, t in enumerate(teams)]
    conferences_raw = [{"id": 1, "name": "Test Conf", "abbreviation": "TC",
                        "classification": "fbs", "member_count": 4}]
    base = {"season": 2026, "season_type": "regular",
            "start_date": "2026-11-01T00:00:00Z", "completed": False,
            "neutral_site": False, "conference_game": True,
            "home_points": None, "away_points": None, "notes": ""}
    games_raw = [
        {**base, "id": 1, "week": 5, "home_id": 1, "home_team": "A",
         "away_id": 2, "away_team": "B", "home_conference": "Test Conf",
         "away_conference": "Test Conf"},
        {**base, "id": 2, "week": 14, "home_id": 1, "home_team": "A",
         "away_id": 3, "away_team": "C", "home_conference": "Test Conf",
         "away_conference": "Test Conf", "neutral_site": True,
         "conference_game": conference_game, "notes": note},
    ]
    fpi_raw = [{"team": t, "fpi": 0.0} for t in teams]
    return build_season(2026, teams_raw, conferences_raw, games_raw, fpi_raw,
                        recalibrate=False)


def test_a_title_game_is_pulled_out_of_the_regular_schedule():
    s = _season_with_note("Test Conf Championship")
    ccg = [g for g in s.games if g["is_ccg"]]
    assert len(ccg) == 1
    assert s.conferences[0].fixed_ccg == (ccg[0]["home_idx"], ccg[0]["away_idx"], 0)
    # and it must not also appear as an ordinary game to be simulated
    assert all(not g["is_ccg"] for g in s.remaining_games)
    assert s.kernel_inputs().g_home.size == 1


def test_title_game_detection_survives_an_unset_conference_flag():
    """CFBD does not always mark title games as conference games."""
    s = _season_with_note("Test Conf Championship", conference_game=False)
    assert sum(g["is_ccg"] for g in s.games) == 1
    assert s.conferences[0].fixed_ccg is not None


def test_a_bowl_game_is_not_mistaken_for_a_title_game():
    s = _season_with_note("Rose Bowl")
    assert sum(g["is_ccg"] for g in s.games) == 0
    assert s.conferences[0].fixed_ccg is None


def test_a_played_title_game_pins_the_champion():
    from cfbroot.config import SimConfig
    from cfbroot.sim import run
    s = _season_with_note("Test Conf Championship")
    ccg = next(g for g in s.games if g["is_ccg"])
    s.conferences[0].fixed_ccg = (ccg["home_idx"], ccg["away_idx"], 2)  # away won
    res = run(s, "A", SimConfig(n_sims=1_000, batch_size=1_000))
    from cfbroot.config import METRIC_NAMES
    mi = METRIC_NAMES.index("win_conference")
    assert res.team_counts[ccg["away_idx"], mi] == res.n_sims
    assert res.team_counts[ccg["home_idx"], mi] == 0


# CFBD payload shape (camelCase)

def test_sdk_payloads_are_normalised_to_snake_case():
    """SDK payloads arrive in camelCase and are converted to snake_case.

    If this breaks, every game loads with no teams and the season is empty.
    """
    from cfbroot.data.cfbd_source import _jsonable, _snake
    assert _snake("homeId") == "home_id"
    assert _snake("startTimeTBD") == "start_time_tbd"
    assert _snake("awayLineScores") == "away_line_scores"
    assert _snake("fpi") == "fpi"
    nested = _jsonable({"homeTeam": "Ohio State",
                        "resumeRanks": {"strengthOfRecord": 3}})
    assert nested == {"home_team": "Ohio State",
                      "resume_ranks": {"strength_of_record": 3}}


def test_a_raw_cfbd_shaped_payload_builds_a_real_season():
    """End-to-end guard: camelCase in, populated season out."""
    from cfbroot.data.cfbd_source import _jsonable
    raw_games = [{
        "id": 1, "season": 2026, "week": 1, "seasonType": "regular",
        "startDate": "2026-08-29T16:00:00Z", "startTimeTBD": False,
        "completed": True, "neutralSite": False, "conferenceGame": True,
        "homeId": 1, "homeTeam": "A", "homeConference": "Test Conf",
        "homeClassification": "fbs", "homePoints": 31,
        "awayId": 2, "awayTeam": "B", "awayConference": "Test Conf",
        "awayClassification": "fbs", "awayPoints": 17, "notes": None,
    }]
    raw_teams = [{"id": 1, "school": "A", "conference": "Test Conf",
                  "classification": "fbs"},
                 {"id": 2, "school": "B", "conference": "Test Conf",
                  "classification": "fbs"}]
    s = build_season(2026, [_jsonable(t) for t in raw_teams],
                     [{"id": 1, "name": "Test Conf", "classification": "fbs"}],
                     [_jsonable(g) for g in raw_games],
                     [{"team": "A", "fpi": 5.0}, {"team": "B", "fpi": 0.0}],
                     recalibrate=False)
    assert len(s.games) == 1
    g = s.games[0]
    assert g["home"] == "A" and g["away"] == "B"
    assert g["status"] == HOME_WON
    assert all(t.is_fbs for t in s.teams), "teams must not degrade to placeholders"


def test_a_title_game_with_the_conference_flag_unset_is_still_detected():
    """CFBD really does send conference_game=False for title games."""
    s = _season_with_note("SEC Championship", conference_game=False)
    assert sum(g["is_ccg"] for g in s.games) == 1
