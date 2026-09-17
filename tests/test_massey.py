"""Checks on the Massey rating and on the kernel's linear stand-in."""

import dataclasses

import numpy as np
import pytest

from cfbroot import massey
from cfbroot.config import ModelParams, SimConfig
from cfbroot.sim import run

from conftest import make_mini_season


def full_season():
    """A fabricated season with every game played, for exact comparisons."""
    from cfbroot.data.synthetic import synthetic_season
    return synthetic_season(seed=7, played_through=20)


def test_the_kernel_fit_reproduces_the_standalone_one():
    """The Monte Carlo's cheap fit has to agree with the one that was validated.

    The kernel reuses a Hessian and takes the correction at its mode instead of
    integrating it, so the two are not identical by construction. The power
    stage should land on top of the standalone; the correction is allowed to
    differ by a couple of places.
    """
    from scipy.stats import spearmanr

    from cfbroot.config import ModelParams
    from cfbroot.sim.kernels import _massey_correct, _massey_power

    state = full_season()
    mp = massey.MasseyParams()
    p = ModelParams()
    ki = state.kernel_inputs()
    ks = massey.kernel_system(ki.is_fbs, ki.g_home, ki.g_away, ki.g_neutral,
                              ki.rating, p.sigma, p.hfa, mp)
    n_g = ki.g_home.size
    at_home = (~ki.g_neutral).astype(np.float64)
    r = ks.prior.copy()
    gval = np.zeros(n_g)
    grad = np.zeros(ks.n_nodes + 1)
    step = np.zeros(ks.n_nodes + 1)
    power = np.zeros(ks.n_nodes)
    _massey_power(n_g, ks.g_hnode, ks.g_anode, ks.g_hfix, ks.g_afix, at_home,
                  ki.g_hpts, ki.g_apts, ks.hinv, ks.prior, ks.prec, ks.n_nodes,
                  mp.gof_k, mp.gof_c, mp.gof_q, mp.mov_weight, mp.mov_flat,
                  40, r, gval, grad, step)
    assert np.abs(step).max() < 1e-6, "the reused Hessian has to converge"

    # min_games high enough that every non-FBS team shares one node, which is
    # what the kernel does; otherwise the two are rating different things.
    want = massey.rate_season(state, include_ccg=False, which="power",
                              min_games=999)
    got = {t.school: r[ks.node[t.idx]] for t in state.fbs_teams}
    rank = lambda d: {s: i + 1 for i, s in enumerate(sorted(d, key=lambda x: -d[x]))}
    ra, rb = rank(want), rank(got)
    arr = np.array([(ra[s], rb[s]) for s in want], float)
    assert spearmanr(arr[:, 0], arr[:, 1]).statistic > 0.99
    assert np.abs(arr[:, 0] - arr[:, 1]).mean() < 2.0

    _massey_correct(ks.g_hnode, ks.g_anode, ks.g_hfix, ks.g_afix, at_home,
                    ki.g_hpts, ki.g_apts, ks.n_nodes, r, mp.correction_abs, 2,
                    ks.team_games_ptr, ks.team_games, ks.team_at_home,
                    np.full(ks.n_nodes, -1, dtype=np.int32), power)
    full = {t.school: power[ks.node[t.idx]] for t in state.fbs_teams}
    ref_rating = massey.rate_season(state, include_ccg=False, min_games=999)
    rc, rd = rank(ref_rating), rank(full)
    arr = np.array([(rc[s], rd[s]) for s in ref_rating], float)
    assert spearmanr(arr[:, 0], arr[:, 1]).statistic > 0.99
    assert np.abs(arr[:, 0] - arr[:, 1]).mean() < 4.0


def test_a_title_game_win_is_added_and_a_loss_is_not():
    """ccg_beat carries a win only, which is what makes a title game one-way."""
    from cfbroot.config import ModelParams
    from cfbroot.sim.kernels import _massey_correct, _massey_power

    state = full_season()
    mp, p = massey.MasseyParams(), ModelParams()
    ki = state.kernel_inputs()
    ks = massey.kernel_system(ki.is_fbs, ki.g_home, ki.g_away, ki.g_neutral,
                              ki.rating, p.sigma, p.hfa, mp)
    n_g = ki.g_home.size
    at_home = (~ki.g_neutral).astype(np.float64)
    r = ks.prior.copy()
    args = (n_g, ks.g_hnode, ks.g_anode, ks.g_hfix, ks.g_afix, at_home,
            ki.g_hpts, ki.g_apts, ks.hinv, ks.prior, ks.prec, ks.n_nodes,
            mp.gof_k, mp.gof_c, mp.gof_q, mp.mov_weight, mp.mov_flat, 30)
    _massey_power(*args, r, np.zeros(n_g), np.zeros(ks.n_nodes + 1),
                  np.zeros(ks.n_nodes + 1))

    def correct(ccg):
        out = np.zeros(ks.n_nodes)
        _massey_correct(ks.g_hnode, ks.g_anode, ks.g_hfix, ks.g_afix, at_home,
                        ki.g_hpts, ki.g_apts, ks.n_nodes, r, mp.correction_abs,
                        2, ks.team_games_ptr, ks.team_games, ks.team_at_home,
                        ccg, out)
        return out

    none = correct(np.full(ks.n_nodes, -1, dtype=np.int32))
    won = np.full(ks.n_nodes, -1, dtype=np.int32)
    winner, loser = 0, int(np.argmin(none))       # beat the worst team there is
    won[winner] = loser
    after = correct(won)
    gain = after[winner] - none[winner]
    assert gain > 0, "a title game win has to help"
    # The loser has no entry, so nothing pushes it down. It still drifts a
    # hair, because the winner moved and the two are linked through the rest
    # of the schedule, but the drift is orders of magnitude smaller and is not
    # a penalty.
    assert abs(after[loser] - none[loser]) < gain / 100.0
    assert after[loser] >= none[loser] - 1e-6, "losing it must not cost anything"


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
