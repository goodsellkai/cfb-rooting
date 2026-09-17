"""Checks on the Massey rating and on the kernel's linear stand-in."""

import dataclasses

import numpy as np
import pytest

from cfbroot import massey
from cfbroot.config import ModelParams, SimConfig
from cfbroot.sim import run

from conftest import make_mini_season


def rate(n_teams, games, home_won, neutral=None):
    """``games`` is a list of (home, away) index pairs."""
    is_fbs = np.ones(n_teams, dtype=bool)
    home = [g[0] for g in games]
    away = [g[1] for g in games]
    neutral = [True] * len(games) if neutral is None else neutral
    return massey.build_linear(is_fbs, home, away, neutral).ratings(home_won)


def test_a_chain_comes_out_in_order():
    # A beat B, B beat C. Two equations, three teams, so the fit is exact.
    r = rate(3, [(0, 1), (1, 2)], [True, True])
    assert r[0] > r[1] > r[2]
    assert r == pytest.approx([1.0, 0.0, -1.0])


def test_everyone_one_and_one_is_a_tie():
    games = [(0, 1), (1, 2), (2, 0)]
    r = rate(3, games, [True, True, True])
    assert r == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)


def test_ratings_are_centred_on_zero():
    r = rate(4, [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)],
             [True, True, True, True, True, True])
    assert r.sum() == pytest.approx(0.0, abs=1e-9)
    assert r[0] > r[1] > r[2] > r[3]


def test_beating_a_good_team_is_worth_more_than_beating_a_bad_one():
    """Both winners go 1-0, but one beat the team that won everything else."""
    # 0 beats 1; 2 beats 3. Then 1 wins the rest and 3 loses the rest.
    games = [(0, 1), (2, 3), (1, 4), (1, 5), (3, 4), (3, 5)]
    r = rate(6, games, [True, True, True, True, False, False])
    assert r[0] > r[2]


def test_home_field_is_recovered():
    """Home teams sweeping a balanced round robin shows up as home advantage."""
    games = [(a, b) for a in range(4) for b in range(4) if a != b]
    is_fbs = np.ones(4, dtype=bool)
    sysm = massey.build_linear(is_fbs, [g[0] for g in games], [g[1] for g in games],
                        [False] * len(games))
    home_won = [True] * len(games)
    assert sysm.home_field(home_won) == pytest.approx(1.0)
    # Every team went 3-3, so nobody separates.
    assert sysm.ratings(home_won) == pytest.approx([0.0] * 4, abs=1e-9)


def test_non_fbs_teams_share_one_rating():
    is_fbs = np.array([True, True, False, False])
    sysm = massey.build_linear(is_fbs, [0, 1], [2, 3], [True, True])
    assert sysm.n_fbs == 2
    assert sysm.n_nodes == 3            # two FBS teams plus the combined node
    # Both FBS teams beat a non-FBS team, so they are indistinguishable.
    r = sysm.ratings([True, True])
    assert r[0] == pytest.approx(r[1])


def test_matches_a_direct_least_squares_fit(midseason):
    """The normal equations must give the same answer as lstsq on X."""
    games = [g for g in midseason.games if g["status"] != 0 and not g["is_ccg"]]
    is_fbs = np.array([t.is_fbs for t in midseason.teams], dtype=bool)
    sysm = massey.build_linear(is_fbs, [g["home_idx"] for g in games],
                        [g["away_idx"] for g in games],
                        [g["neutral"] for g in games])
    home_won = np.array([g["status"] == 1 for g in games])

    n = sysm.n_nodes + 1
    rows = np.arange(len(games))
    x = np.zeros((len(games), n))
    x[rows, sysm.g_hnode] += 1.0
    x[rows, sysm.g_anode] -= 1.0
    x[sysm.g_at_home, sysm.n_nodes] = 1.0
    y = np.where(home_won, 1.0, -1.0)
    direct, *_ = np.linalg.lstsq(x, y, rcond=None)

    assert sysm.ratings(home_won) == pytest.approx(direct[:sysm.n_fbs], abs=1e-9)


def test_rate_season_stops_at_the_requested_week(midseason):
    early = massey.rate_season(midseason, through_week=3)
    late = massey.rate_season(midseason, through_week=6)
    assert set(early) and set(early) <= set(late)
    assert early != late


def test_kernel_reproduces_the_standalone_rating():
    """With only the Massey term switched on, the kernel's order is Massey's."""
    games = [("A", "B", True), ("B", "C", True), ("C", "D", True),
             ("D", "A", False), ("A", "C", True), ("B", "D", True)]
    params = dataclasses.replace(
        ModelParams(), rating_sd=0.0, w_rating=0.0, k_resume=0.0,
        k_champ=0.0, k_sos=0.0, k_lsq=1.0)
    state = make_mini_season(games, params=params)

    standalone = massey.rate_season(state)
    want = {s: i + 1 for i, s in enumerate(sorted(standalone,
                                                  key=lambda x: -standalone[x]))}
    cfg = SimConfig(n_sims=4, batch_size=4, seed=1)
    for t in state.fbs_teams:
        res = run(state, t.idx, cfg)
        assert int(np.argmax(res.rank_hist)) == want[t.school]


def test_lsq_term_is_off_by_default():
    assert ModelParams().k_lsq == 0.0


# The Massey rating itself

# The eleven sample values Massey publishes with his model description.
PUBLISHED_GOF = [(30, 29, .5270), (10, 9, .5359), (27, 24, .5836),
                 (27, 20, .6924), (50, 40, .7292), (10, 0, .8548),
                 (30, 14, .8786), (45, 21, .9433), (45, 14, .9823),
                 (30, 0, .9920), (56, 3, .9998)]


@pytest.mark.parametrize("pa,pb,expected", PUBLISHED_GOF)
def test_game_outcome_function_matches_the_published_values(pa, pb, expected):
    assert massey.gof(pa, pb) == pytest.approx(expected, abs=0.004)


def test_game_outcome_function_shape():
    assert massey.gof(21, 21) == pytest.approx(0.5)
    assert massey.gof(21, 24) == pytest.approx(1 - massey.gof(24, 21))
    # A one point win in a low scoring game says more than in a shootout.
    assert massey.gof(10, 9) > massey.gof(30, 29)
    # Running up the score pays less and less.
    assert massey.gof(45, 0) - massey.gof(35, 0) < massey.gof(17, 0) - massey.gof(7, 0)


def fit_wl(n_teams, games, home_won, neutral=None, **kw):
    rated = np.ones(n_teams, dtype=bool)
    neutral = [True] * len(games) if neutral is None else neutral
    return massey.fit(rated, [g[0] for g in games], [g[1] for g in games],
                      neutral, home_won=home_won, **kw)


def test_power_fit_orders_a_chain():
    f = fit_wl(3, [(0, 1), (1, 2)], [True, True])
    assert f.converged
    assert f.power[0] > f.power[1] > f.power[2]


def test_power_fit_ties_a_round_robin():
    f = fit_wl(3, [(0, 1), (1, 2), (2, 0)], [True, True, True])
    assert f.power[:3] == pytest.approx([f.power[0]] * 3, abs=1e-6)


def test_power_fit_recovers_home_field():
    """Everyone holds serve, so the fit has to explain it with home advantage."""
    pairs = [(a, b) for a in range(6) for b in range(6) if a != b]
    games = pairs * 6
    f = fit_wl(6, games, [True] * len(games), neutral=[False] * len(games))
    assert f.hfa > massey.MasseyParams().hfa_mean
    # Every team went 5-5, so none of them separates.
    assert f.power[:6] == pytest.approx([f.power[0]] * 6, abs=1e-6)

    away = fit_wl(6, games, [False] * len(games), neutral=[False] * len(games))
    assert away.hfa < f.hfa


def test_scores_and_win_loss_give_different_answers():
    """Margin of victory is the whole difference between Massey and the BCS one."""
    rated = np.ones(3, dtype=bool)
    h, a, neu = [0, 1], [1, 2], [True, True]
    blowout = massey.fit(rated, h, a, neu, home_points=[56, 21],
                         away_points=[0, 20])
    narrow = massey.fit(rated, h, a, neu, home_points=[21, 21],
                        away_points=[20, 20])
    # Same two wins either way, but the margins separate the teams differently.
    assert (blowout.power[0] - blowout.power[2]) > (narrow.power[0] - narrow.power[2])
    wl = massey.fit(rated, h, a, neu, home_won=[True, True])
    assert wl.power[0] > wl.power[1] > wl.power[2]


def test_correction_rewards_winning_over_winning_big():
    """Team 0 wins narrowly, team 1 loses narrowly. Only the record separates."""
    rated = np.ones(6, dtype=bool)
    h = [0, 1, 0, 1, 0, 1]
    a = [2, 3, 4, 5, 3, 2]
    hp = [21, 20, 21, 20, 21, 20]
    ap = [20, 21, 20, 21, 20, 21]
    f = massey.fit(rated, h, a, [True] * 6, home_points=hp, away_points=ap)
    # 0 went 3-0 in one score games, 1 went 0-3. The correction widens the gap
    # the scores alone leave.
    assert f.rating[0] - f.rating[1] > f.power[0] - f.power[1]


def test_prior_keeps_an_unconnected_early_season_in_check(preseason, midseason):
    strong = dataclasses.replace(massey.MasseyParams(), prior_sd=0.2)
    loose = dataclasses.replace(massey.MasseyParams(), prior_sd=16.0)
    a = massey.fit_season(midseason, through_week=2, params=strong,
                          prior_from_rating=True)
    b = massey.fit_season(midseason, through_week=2, params=loose,
                          prior_from_rating=True)
    assert np.ptp(a.power) < np.ptp(b.power)


def test_fit_season_returns_nothing_before_any_games(preseason):
    assert massey.fit_season(preseason) is None


def test_rate_season_only_returns_fbs_teams(midseason):
    r = massey.rate_season(midseason)
    assert set(r) == {t.school for t in midseason.fbs_teams}


def test_margin_weight_flattens_blowouts():
    """A rout and a squeaker should converge as mov_weight falls."""
    rated = np.ones(3, dtype=bool)
    h, a, neu = [0, 1], [1, 2], [True, True]

    def spread(mov, hp, ap):
        p = dataclasses.replace(massey.MasseyParams(), mov_weight=mov)
        f = massey.fit(rated, h, a, neu, home_points=hp, away_points=ap, params=p)
        return f.power[0] - f.power[2]

    rout = ([52, 49], [7, 7])
    squeaker = ([21, 21], [20, 20])
    full = spread(1.0, *rout) - spread(1.0, *squeaker)
    half = spread(0.45, *rout) - spread(0.45, *squeaker)
    none = spread(0.0, *rout) - spread(0.0, *squeaker)
    assert full > half > none
    assert none == pytest.approx(0.0, abs=1e-9)   # only the wins are left


def test_margin_weight_keeps_the_win_itself():
    """At mov_weight 0 the winner still outranks the loser."""
    p = dataclasses.replace(massey.MasseyParams(), mov_weight=0.0)
    f = massey.fit(np.ones(3, dtype=bool), [0, 1], [1, 2], [True, True],
                   home_points=[21, 21], away_points=[20, 20], params=p)
    assert f.power[0] > f.power[1] > f.power[2]


def test_margin_weight_one_is_masseys_own_model():
    rated = np.ones(3, dtype=bool)
    args = dict(home_points=[35, 21], away_points=[7, 20])
    p = dataclasses.replace(massey.MasseyParams(), mov_weight=1.0)
    a = massey.fit(rated, [0, 1], [1, 2], [True, True], params=p, **args)
    b = massey.fit(rated, [0, 1], [1, 2], [True, True], **args)
    assert not np.allclose(a.power, b.power)      # the default discounts margin
    assert massey.MasseyParams().mov_weight == 0.45
