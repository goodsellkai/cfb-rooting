"""Checks on the Massey rating, and that the simulator runs the same one."""

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


def with_title_game(state):
    """The same season with one played title game added: SEC Team 16 beats
    SEC Team 01, so the fit has a winner to credit and a loser to spare."""
    st = dataclasses.replace(state, games=list(state.games))
    t1, t2 = st.team_by_name("SEC Team 01"), st.team_by_name("SEC Team 16")
    week = max(g["week"] for g in st.games) + 1
    st.games.append(dict(st.games[0], game_id=999999, week=week,
                         home_idx=t1.idx, away_idx=t2.idx, home=t1.school,
                         away=t2.school, neutral=True, home_points=17,
                         away_points=24, status=2, is_ccg=True,
                         notes="SEC Championship"))
    st.conferences[t1.conf_idx].fixed_ccg = (t1.idx, t2.idx, 2)
    return st


def simulator_rating(state):
    """Run the simulator's rating functions on a finished season."""
    from cfbroot.sim import kernels as K

    ki = state.kernel_inputs()
    m, mp = ki.massey, state.massey
    n_g, n = ki.g_home.size, m.n_nodes
    at_home = (~ki.g_neutral).astype(np.float64)
    gval = np.array([K._outcome(ki.g_hpts[i], ki.g_apts[i], mp.gof_k, mp.gof_c,
                                mp.gof_q, mp.mov_weight, mp.mov_flat)
                     for i in range(n_g)])
    cc = [(m.node[h], m.node[a], ki.conf_ccg_home[cf], *ki.conf_ccg_pts[cf])
          for cf, (h, a, st) in enumerate(ki.conf_fixed_ccg) if h >= 0 and st]
    k = max(len(cc), 1)
    cc_h = np.array([c[0] for c in cc] or [0], np.int32)[:k]
    cc_a = np.array([c[1] for c in cc] or [0], np.int32)[:k]
    cc_home = np.array([c[2] for c in cc] or [0.0])[:k]
    cc_g = np.array([K._outcome(c[3], c[4], mp.gof_k, mp.gof_c, mp.gof_q,
                                mp.mov_weight, mp.mov_flat) for c in cc] or [0.0])
    cc_won = np.array([1 if c[3] > c[4] else 0 for c in cc] or [0], np.uint8)
    vec, wts = K._fit_work(n, n_g, m.x_h.size, ki.n_conf)
    r0, r1 = m.start.copy(), m.start.copy()
    K._fit_power(n_g, m.g_in_fit, m.g_hnode, m.g_anode, at_home, gval,
                 m.x_h, m.x_a, m.x_home, m.x_g, cc_h, cc_a, cc_home, cc_g, 0,
                 m.minv, m.prior, m.prec, n, 1e-12, 500, r0, vec, wts)
    c0, c1, loser = np.zeros(n), np.zeros(n), np.zeros(n, np.uint8)
    K._selection_rating(n_g, m.g_in_fit, m.g_hnode, m.g_anode, at_home, gval,
                        ki.g_hpts, ki.g_apts, m.x_h, m.x_a, m.x_home, m.x_g,
                        m.x_won, cc_h, cc_a, cc_home, cc_g, cc_won, len(cc),
                        np.full(n, -1, np.int32), loser, m.minv, m.prior,
                        m.prec.copy(), np.zeros(n + 1), m.played, n, m.n_fbs,
                        mp.prior_sd, mp.prior_games, 1e-12, 500,
                        mp.correction_abs, mp.correction_passes, m.gh_t,
                        m.gh_logw, m.tg_ptr, m.tg_ref, m.tg_home, r0, r1, c0, c1,
                        np.zeros(n), np.zeros(m.gh_t.size), np.zeros(n, np.uint8),
                        vec, wts)
    return {t.school: float(c0[m.node[t.idx]] if loser[m.node[t.idx]]
                            else c1[m.node[t.idx]]) for t in state.fbs_teams}


@pytest.mark.parametrize("title_game", [False, True])
def test_the_simulator_rates_a_season_exactly_like_the_standalone(title_game):
    """Every simulated season is rated by rate_selection_day(), not a stand-in."""
    state = full_season()
    if title_game:
        state = with_title_game(state)
    want, _ = massey.rate_selection_day(state)
    got = simulator_rating(state)
    assert max(abs(got[s] - want[s]) for s in want) < 1e-8


def test_a_title_game_win_is_added_and_a_loss_is_not():
    """The winner is rated with the game and the loser without it."""
    base = massey.rate_selection_day(full_season())[0]
    after = simulator_rating(with_title_game(full_season()))
    # SEC Team 16 won it and gains. SEC Team 01 lost it and keeps the rating
    # it had without it, give or take the winner's move through the network.
    assert after["SEC Team 16"] > base["SEC Team 16"] + 0.01
    assert abs(after["SEC Team 01"] - base["SEC Team 01"]) < 0.005


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
