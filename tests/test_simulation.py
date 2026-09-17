"""Structural and statistical checks on the season simulator."""

import numpy as np
import pytest

from cfbroot.config import METRIC_NAMES, ModelParams, SimConfig
from cfbroot.sim import build_guide, run

from conftest import make_mini_season

M = {name: i for i, name in enumerate(METRIC_NAMES)}


@pytest.fixture(scope="module")
def result(midseason):
    return run(midseason, "SEC Team 08", SimConfig(n_sims=40_000, batch_size=20_000))


# Invariants

@pytest.mark.parametrize("metric,expected", [
    ("win_conference", 10),          # ten conferences crown a champion
    ("make_conf_title_game", 20),    # two participants each
    ("make_playoff", 12),
    ("top4_seed", 4),
    ("reach_quarterfinal", 8),
    ("reach_semifinal", 4),
    ("reach_title_game", 2),
    ("win_national_title", 1),
])
def test_bracket_structure_holds_in_every_simulation(result, metric, expected):
    """Per-season counts, so each total equals expected * n_sims."""
    total = result.team_counts[:, M[metric]].sum()
    assert total == expected * result.n_sims


def test_independents_never_win_a_conference(midseason, result):
    for t in midseason.fbs_teams:
        if t.conference and "independent" in t.conference.lower():
            assert result.team_counts[t.idx, M["win_conference"]] == 0


def test_non_fbs_teams_never_reach_the_playoff(midseason, result):
    for t in midseason.teams:
        if not t.is_fbs:
            assert result.team_counts[t.idx, M["make_playoff"]] == 0


def test_seed_histogram_agrees_with_the_playoff_metric(result):
    assert result.seed_hist[1:].sum() == result.metric_counts[M["make_playoff"]]
    assert result.seed_hist.sum() == result.n_sims


def test_metric_implications_are_never_violated(result):
    """A team cannot win the title without reaching it, and so on down."""
    c = result.metric_counts
    assert c[M["win_national_title"]] <= c[M["reach_title_game"]]
    assert c[M["reach_title_game"]] <= c[M["reach_semifinal"]]
    assert c[M["reach_semifinal"]] <= c[M["reach_quarterfinal"]]
    assert c[M["reach_quarterfinal"]] <= c[M["make_playoff"]]
    assert c[M["top4_seed"]] <= c[M["make_playoff"]]
    assert c[M["win_conference"]] <= c[M["make_conf_title_game"]]


def test_completed_results_are_respected(midseason):
    """Games already played must never be re-rolled."""
    ki = midseason.kernel_inputs()
    played = ki.g_status != 0
    assert played.sum() > 0
    assert not np.isin(ki.remaining_idx, np.flatnonzero(played)).any()


# Reproducibility

def test_same_seed_gives_identical_results(midseason):
    cfg = SimConfig(n_sims=10_000, batch_size=5_000, seed=99)
    a = run(midseason, "SEC Team 08", cfg)
    b = run(midseason, "SEC Team 08", cfg)
    assert np.array_equal(a.metric_counts, b.metric_counts)
    assert np.array_equal(a.cond_counts, b.cond_counts)


def test_different_seeds_give_different_results(midseason):
    a = run(midseason, "SEC Team 08", SimConfig(n_sims=10_000, batch_size=5_000, seed=1))
    b = run(midseason, "SEC Team 08", SimConfig(n_sims=10_000, batch_size=5_000, seed=2))
    assert not np.array_equal(a.metric_counts, b.metric_counts)


# Calibration

def test_simulated_win_rate_matches_the_model_probability(midseason):
    """Each game's simulated home-win frequency must match its model input."""
    res = run(midseason, "SEC Team 08", SimConfig(n_sims=100_000, batch_size=25_000))
    p_model = np.array([g["pwin_home"] for g in res.game_keys])
    p_sim = res.n_home_wins / res.n_sims
    se = np.sqrt(p_model * (1 - p_model) / res.n_sims)
    z = (p_sim - p_model) / np.maximum(se, 1e-12)
    assert np.abs(z).max() < 5.0
    assert np.abs(z).mean() < 1.0


def test_expected_wins_matches_the_sum_of_win_probabilities(midseason):
    res = run(midseason, "SEC Team 08", SimConfig(n_sims=100_000, batch_size=25_000))
    focus = res.focus_idx
    expected = 0.0
    for g in midseason.games:
        if g["is_ccg"]:
            continue
        if g["status"] != 0:
            expected += float((g["status"] == 1 and g["home_idx"] == focus)
                              or (g["status"] == 2 and g["away_idx"] == focus))
        elif g["home_idx"] == focus:
            expected += g["pwin_home"]
        elif g["away_idx"] == focus:
            expected += 1.0 - g["pwin_home"]
    assert res.wins_mean == pytest.approx(expected, abs=0.03)


# Tiebreakers

def test_head_to_head_beats_the_rating_fallback():
    """A and B finish 2-1 and A beat B, so A wins the title even though B is rated higher."""
    games = [("A", "B", True), ("A", "C", True), ("D", "A", True),
             ("B", "C", True), ("B", "D", True), ("C", "D", True)]
    s = make_mini_season(games, ratings={"A": -10.0, "B": 25.0, "C": 0.0, "D": 0.0})
    s.conferences[0].has_ccg = False   # crown the regular-season leader outright
    res = run(s, "A", SimConfig(n_sims=2_000, batch_size=2_000))
    a = s.team_by_name("A").idx
    b = s.team_by_name("B").idx
    assert res.team_counts[a, M["win_conference"]] == res.n_sims
    assert res.team_counts[b, M["win_conference"]] == 0


def test_best_record_wins_the_conference_outright():
    games = [("A", "B", True), ("A", "C", True), ("A", "D", True),
             ("B", "C", True), ("B", "D", True), ("C", "D", True)]
    s = make_mini_season(games, ratings={"A": -20.0, "B": 20.0, "C": 5.0, "D": 5.0})
    s.conferences[0].has_ccg = False
    res = run(s, "A", SimConfig(n_sims=1_000, batch_size=1_000))
    a = s.team_by_name("A").idx
    assert res.team_counts[a, M["win_conference"]] == res.n_sims


def test_title_game_participants_are_the_top_two_finishers():
    games = [("A", "B", True), ("A", "C", True), ("A", "D", True),
             ("B", "C", True), ("B", "D", True), ("C", "D", True)]
    s = make_mini_season(games, ratings={"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0})
    res = run(s, "A", SimConfig(n_sims=2_000, batch_size=2_000))
    idx = {n: s.team_by_name(n).idx for n in "ABCD"}
    assert res.team_counts[idx["A"], M["make_conf_title_game"]] == res.n_sims
    assert res.team_counts[idx["B"], M["make_conf_title_game"]] == res.n_sims
    assert res.team_counts[idx["C"], M["make_conf_title_game"]] == 0
    assert res.team_counts[idx["D"], M["make_conf_title_game"]] == 0


def test_evenly_matched_title_game_is_a_coin_flip():
    games = [("A", "B", True), ("A", "C", True), ("A", "D", True),
             ("B", "C", True), ("B", "D", True), ("C", "D", True)]
    s = make_mini_season(games, ratings={n: 0.0 for n in "ABCD"})
    res = run(s, "A", SimConfig(n_sims=40_000, batch_size=20_000))
    a = s.team_by_name("A").idx
    p = res.team_counts[a, M["win_conference"]] / res.n_sims
    assert p == pytest.approx(0.5, abs=0.02)


# Committee proxy calibration

def test_at_large_selection_is_driven_by_record_not_by_rating(midseason):
    """Even the top-rated team rarely gets an at-large bid with 4 losses."""
    best = max(midseason.fbs_teams, key=lambda t: t.rating)
    res = run(midseason, best.idx, SimConfig(n_sims=120_000, batch_size=25_000))
    rates = res.playoff_rate_by_wins()
    n_games = sum(1 for g in midseason.games
                  if not g["is_ccg"] and best.idx in (g["home_idx"], g["away_idx"]))

    four_loss = n_games - 4
    two_loss = n_games - 2
    if rates.get(four_loss, (0, 0))[1] > 2_000:
        assert rates[four_loss][0] < 0.45, (
            f"a 4-loss {best.school} makes the field "
            f"{rates[four_loss][0]:.0%} of the time, too often")
    if rates.get(two_loss, (0, 0))[1] > 2_000:
        assert rates[two_loss][0] > 0.60

    # and the relationship must be monotone: more wins is never worse
    ordered = [rates[w][0] for w in sorted(rates) if rates[w][1] > 1_000]
    assert ordered == sorted(ordered), "more wins must never lower playoff odds"


def test_past_opponents_later_wins_help_you(midseason):
    """A team gains when a team it already played goes on to win more.

    Nothing in the model says this directly. It falls out of the rating being
    a network: an opponent's later results move that opponent's rating, and
    yours is fitted against theirs.
    """
    from cfbroot.sim import build_guide

    # A bubble team, so the odds are not pinned near 0 or 1.
    ranked = sorted(midseason.fbs_teams, key=lambda t: -t.rating)
    focus = ranked[13]
    past = set()
    for g in midseason.games:
        if g["is_ccg"] or g["status"] == 0:
            continue
        if focus.idx not in (g["home_idx"], g["away_idx"]):
            continue
        opp = g["away_idx"] if g["home_idx"] == focus.idx else g["home_idx"]
        if midseason.teams[opp].is_fbs:
            past.add(opp)
    assert past, "fixture team has no completed games against FBS opponents"

    res = run(midseason, focus.idx, SimConfig(n_sims=120_000, batch_size=25_000))
    p = res.probability("make_playoff")
    assert 0.02 < p < 0.98, f"focus team is saturated at {p:.1%}"

    guide = build_guide(midseason, res, primary="make_playoff", week=None)
    vals = []
    for e in guide.games:
        for opp in past:
            if opp in (e.home_idx, e.away_idx):
                sw = e.swings["make_playoff"]
                # Orient to "my past opponent wins this game".
                vals.append(sw.delta if e.home_idx == opp else -sw.delta)
    assert vals, "no remaining games involve a past opponent"
    assert float(np.mean(vals)) > 0, "past opponents winning should help"
