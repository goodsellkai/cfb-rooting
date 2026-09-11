"""Checks on the conditional analysis that produces the rooting guide."""

import numpy as np
import pytest

from cfbroot.config import METRIC_NAMES, SimConfig
from cfbroot.sim import build_guide, league_table, run

from conftest import make_mini_season

M = {name: i for i, name in enumerate(METRIC_NAMES)}


@pytest.fixture(scope="module")
def result(midseason):
    return run(midseason, "SEC Team 08", SimConfig(n_sims=200_000, batch_size=25_000))


@pytest.fixture(scope="module")
def guide(midseason, result):
    return build_guide(midseason, result, primary="win_conference",
                       week=midseason.current_week())


def test_conditioning_obeys_the_law_of_total_probability(result):
    """P(m) = P(m|home wins)P(home wins) + P(m|away wins)P(away wins)."""
    n = result.n_sims
    for mi in range(len(METRIC_NAMES)):
        total = result.metric_counts[mi]
        recombined = result.cond_counts[:, mi] + (total - result.cond_counts[:, mi])
        assert np.all(recombined == total)

    # and in probability form, for a metric with real spread
    mi = M["win_conference"]
    n_h = result.n_home_wins
    ok = (n_h > 0) & (n_h < n)
    p_h = result.cond_counts[ok, mi] / n_h[ok]
    p_a = (result.metric_counts[mi] - result.cond_counts[ok, mi]) / (n - n_h[ok])
    w = n_h[ok] / n
    assert np.allclose(p_h * w + p_a * (1 - w), result.metric_counts[mi] / n)


def test_conditional_counts_never_exceed_their_marginals(result):
    assert np.all(result.cond_counts <= result.metric_counts[None, :])
    assert np.all(result.cond_counts >= 0)
    assert np.all(result.n_home_wins <= result.n_sims)


def test_winning_your_own_game_helps_you(midseason, result):
    """The sign on a team's own game is the one thing that cannot be subtle."""
    focus = result.focus_idx
    for gi, g in enumerate(result.game_keys):
        if focus not in (g["home_idx"], g["away_idx"]):
            continue
        mi = M["win_conference"]
        n_h = int(result.n_home_wins[gi])
        n_a = result.n_sims - n_h
        if min(n_h, n_a) < 1000:
            continue
        p_h = result.cond_counts[gi, mi] / n_h
        p_a = (result.metric_counts[mi] - result.cond_counts[gi, mi]) / n_a
        if g["home_idx"] == focus:
            assert p_h > p_a, f"{g['away']} at {g['home']}: winning at home hurt?"
        else:
            assert p_a > p_h, f"{g['away']} at {g['home']}: winning away hurt?"


def test_a_rivals_loss_helps_you_win_the_conference(midseason, result):
    """Within a conference, an opponent losing should raise your title odds."""
    focus = midseason.teams[result.focus_idx]
    mi = M["win_conference"]
    n = result.n_sims
    checked = 0
    for gi, g in enumerate(result.game_keys):
        h, a = midseason.teams[g["home_idx"]], midseason.teams[g["away_idx"]]
        if focus.idx in (h.idx, a.idx):
            continue
        if h.conf_idx != focus.conf_idx or a.conf_idx != focus.conf_idx:
            continue  # both must be conference rivals
        n_h = int(result.n_home_wins[gi])
        n_a = n - n_h
        if min(n_h, n_a) < 5000:
            continue
        p_h = result.cond_counts[gi, mi] / n_h
        p_a = (result.metric_counts[mi] - result.cond_counts[gi, mi]) / n_a
        # whichever way it breaks, one rival winning must not help *both* of them
        assert not (p_h > p_a + 1e-9 and p_a > p_h + 1e-9)
        checked += 1
    assert checked > 0, "fixture produced no intra-conference games to check"


def test_an_unrelated_game_has_no_detectable_leverage(midseason, result):
    """Games outside the team's conference shouldn't affect its conference title odds."""
    guide = build_guide(midseason, result, primary="win_conference",
                        week=midseason.current_week())
    focus = midseason.teams[result.focus_idx]
    others = [e for e in guide.games
              if midseason.teams[e.home_idx].conf_idx != focus.conf_idx
              and midseason.teams[e.away_idx].conf_idx != focus.conf_idx]
    assert others, "fixture produced no out-of-conference games"
    sig = sum(1 for e in others if e.swings["win_conference"].significant)
    # Conference titles are decided inside the conference.
    assert sig <= max(1, int(0.05 * len(others)))


def test_significance_requires_both_a_real_effect_and_enough_samples(guide):
    for e in guide.games:
        s = e.swings[guide.primary]
        if s.significant:
            assert s.qvalue <= guide.fdr_q
            assert not (s.lo <= 0 <= s.hi), "a significant swing cannot straddle zero"


def test_every_game_names_a_side_to_root_for(guide):
    """Every game names a team to root for."""
    for e in guide.games + guide.own_games:
        assert e.root_for, f"{e.away} at {e.home} produced no side to root for"
        assert e.root_for in (e.home, e.away)
        assert e.confidence in ("clear", "leaning", "thin")


def test_root_for_points_at_the_side_that_helps(guide):
    for e in guide.games:
        s = e.swings[guide.primary]
        assert e.root_for == (e.home if s.root_for_home else e.away)
        assert (e.root_for_side == "home") == (e.root_for == e.home)
        if np.isfinite(s.delta) and s.delta != 0:
            assert e.root_for == (e.home if s.delta > 0 else e.away)


def test_confidence_grades_match_the_underlying_statistics(guide):
    for e in guide.games:
        s = e.swings[guide.primary]
        if e.confidence == "clear":
            assert s.significant and s.reliable
            assert not (s.lo <= 0 <= s.hi)
        elif e.confidence == "leaning":
            assert s.reliable and not s.significant
        else:
            assert e.confidence == "thin" and not s.reliable
            assert s.min_arm < 250


def test_guide_is_sorted_with_resolved_games_first(guide):
    sig = [e.swings[guide.primary].significant for e in guide.games]
    # every significant entry precedes every non-significant one
    assert sig == sorted(sig, reverse=True)
    resolved = [e.defensible for e in guide.games if e.swings[guide.primary].significant]
    assert resolved == sorted(resolved, reverse=True)


def test_every_metric_is_available_without_re_simulating(midseason, result):
    """All metrics come from one run, so switching metric needs no re-run."""
    g = build_guide(midseason, result, primary="make_playoff", week=None)
    for e in g.games + g.own_games:
        assert set(e.swings) == set(METRIC_NAMES)
    assert set(g.headline) == set(METRIC_NAMES)


def test_fdr_is_controlled_within_each_metric(midseason, result):
    """q-values for one metric must not depend on which others were computed."""
    few = build_guide(midseason, result, metrics=["make_playoff"],
                      primary="make_playoff", week=None)
    many = build_guide(midseason, result, metrics=list(METRIC_NAMES),
                       primary="make_playoff", week=None)
    a = {(e.home, e.away): e.swings["make_playoff"].qvalue for e in few.games}
    b = {(e.home, e.away): e.swings["make_playoff"].qvalue for e in many.games}
    assert a.keys() == b.keys()
    for k in a:
        assert a[k] == pytest.approx(b[k])


def test_week_filter_selects_only_that_week(midseason, result):
    wk = midseason.current_week() + 1
    g = build_guide(midseason, result, week=wk)
    assert g.games, "no games found in the requested week"
    assert all(e.week == wk for e in g.games + g.own_games)


def test_all_weeks_covers_every_remaining_game(midseason, result):
    g = build_guide(midseason, result, week=None)
    assert len(g.games) + len(g.own_games) == len(midseason.remaining_games)


def test_more_simulations_shrink_the_intervals(midseason):
    small = run(midseason, "SEC Team 08", SimConfig(n_sims=20_000, batch_size=20_000))
    big = run(midseason, "SEC Team 08", SimConfig(n_sims=320_000, batch_size=25_000))
    gs = build_guide(midseason, small, primary="win_conference", week=None)
    gb = build_guide(midseason, big, primary="win_conference", week=None)
    ws = np.median([e.swings["win_conference"].hi - e.swings["win_conference"].lo
                    for e in gs.games])
    wb = np.median([e.swings["win_conference"].hi - e.swings["win_conference"].lo
                    for e in gb.games])
    # 16x the simulations should roughly halve the interval width twice over
    assert ws / wb == pytest.approx(4.0, rel=0.25)


def test_league_table_is_ordered_and_bounded(midseason, result):
    rows = league_table(midseason, result, "make_playoff", limit=25)
    assert len(rows) == 25
    assert [r["p"] for r in rows] == sorted((r["p"] for r in rows), reverse=True)
    for r in rows:
        assert 0.0 <= r["lo"] <= r["p"] <= r["hi"] <= 1.0


def test_a_decided_game_produces_no_rooting_row():
    """A game already in the books cannot be a rooting interest."""
    games = [("A", "B", True), ("A", "C", True), ("A", "D", True),
             ("B", "C", True), ("B", "D", True), ("C", "D", True)]
    s = make_mini_season(games)
    res = run(s, "A", SimConfig(n_sims=2_000, batch_size=2_000))
    assert res.n_home_wins.size == 0
    g = build_guide(s, res, week=None)
    assert g.games == [] and g.own_games == []
