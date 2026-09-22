"""Conference title game tiebreakers and the 2026 playoff bid rules."""

import numpy as np

from cfbroot import selection
from cfbroot.config import MAX_CONF_SIZE
from cfbroot.data import conference_rules as CR
from cfbroot.sim import kernels as K


def standings(results, n, conference="SEC", year=2026, score=None, divs=None,
              n_pick=2):
    """Order a made-up conference. ``results`` is (winner, loser) per game."""
    rules = CR.title_rules(conference, year)
    members = np.arange(n, dtype=np.int32)
    g_home = np.array([w for w, _ in results], dtype=np.int32)
    g_away = np.array([l for _, l in results], dtype=np.int32)
    winner = np.ones(len(results), dtype=np.uint8)          # home side won
    cw = np.bincount(g_home, minlength=n).astype(np.int32)
    cl = np.bincount(g_away, minlength=n).astype(np.int32)
    score = np.asarray(score if score is not None else np.zeros(n), dtype=np.float64)
    div_id = np.asarray(divs if divs is not None else [-1] * n, dtype=np.int32)
    order = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
    pct = np.zeros(MAX_CONF_SIZE)
    mark = np.full(n, -1, dtype=np.int32)
    res = np.zeros((MAX_CONF_SIZE, MAX_CONF_SIZE), dtype=np.int32)
    args = (members, n, cw, cl, cw.copy(), score, div_id)
    games = (np.arange(len(results), dtype=np.int32), 0, len(results),
             g_home, g_away, winner,
             np.array(CR.step_row(rules.two), dtype=np.int8),
             np.array(CR.step_row(rules.multi), dtype=np.int8), rules.flags)
    if divs is not None:
        return list(K._title_game_pair(*args, 2, *games, order, pct, mark, res))
    K._order_conference(*args, -1, *games, n_pick, order, pct, mark, res)
    return [int(members[order[k]]) for k in range(n)]


def test_head_to_head_settles_a_two_team_tie():
    # 0 and 1 both 2-1; 0 beat 1, though 1 rates higher.
    games = [(0, 1), (0, 2), (3, 0), (1, 2), (1, 3), (2, 3)]
    assert standings(games, 4, score=[0, 5, 0, 0])[:2] == [0, 1]


def test_a_three_way_tie_goes_past_head_to_head():
    # 3 is alone at 1-0 and takes first. 0, 1 and 2 tie at 1-1 for second.
    # 0 and 1 never met, so head-to-head settles nothing, nor do common
    # opponents (there are none outside the tie). 0's opponents have the best
    # conference record, so the SEC sends 0, although 1 rates far higher.
    games = [(0, 2), (3, 0), (2, 1), (1, 4)]
    assert standings(games, 5, score=[0, 9, 0, 0, 0])[:2] == [3, 0]


def test_the_acc_lets_a_team_with_fewer_games_into_the_tie():
    # 1 is 8-1 over nine games; 0 is 7-1 over eight and beat 1. On
    # percentage 1 leads, but in the ACC equal losses put them level and
    # head-to-head sends 0 first. The SEC's rules would not. 9 and 10 are 1-1,
    # also one loss, but two games is not one game short of nine, so they
    # stay out of it.
    games = [(0, 1)] + [(0, k) for k in range(3, 9)] + [(2, 0)]
    games += [(1, k) for k in range(3, 11)] + [(9, 2), (10, 2)]
    assert standings(games, 11, "ACC")[:2] == [0, 1]
    assert standings(games, 11, "SEC")[:2] == [1, 0]


def test_the_sun_belt_sends_its_division_winners():
    # The two best records are both East (0, 1); the West winner (2) goes.
    games = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    assert standings(games, 4, "Sun Belt", divs=[0, 0, 1, 1]) == [0, 2]


def test_sun_belt_divisions_follow_realignment():
    assert CR.division_of("Sun Belt", "Louisiana Tech", 2026) == "West"
    assert CR.division_of("Sun Belt", "Texas State", 2026) is None
    assert CR.division_of("Sun Belt", "Texas State", 2025) == "West"
    assert CR.division_of("Sun Belt", "Marshall", 2026) == "East"


def test_where_title_games_are_played():
    for c in ("SEC", "Big Ten", "Big 12", "ACC", "Mid-American"):
        assert not CR.title_rules(c, 2026).flags & CR.HOSTED
    for c in ("Pac-12", "Sun Belt", "American Athletic", "Conference USA",
              "Mountain West"):
        assert CR.title_rules(c, 2026).flags & CR.HOSTED


def test_notre_dame_is_in_when_ranked_in_the_top_12():
    # Four power champions and the best Group of Six team all sit below 12,
    # so only seven at-large places are left, and Notre Dame is eleventh.
    order = [f"T{i}" for i in range(1, 11)] + ["Notre Dame", "T12",
             "C1", "C2", "C3", "C4", "T17", "T18", "T19", "G6"]
    conf = {t: "Big Ten" for t in order}
    conf.update({"C1": "SEC", "C2": "Big Ten", "C3": "ACC", "C4": "Big 12",
                 "G6": "Mountain West", "Notre Dame": "FBS Independents"})
    champs = {"C1", "C2", "C3", "C4"}
    with_rule = selection.pick_field(order, champs, conf, rule="2026")
    without = selection.pick_field(order, champs, conf, rule="2026", top12=())
    assert "Notre Dame" in with_rule.seeds
    assert "Notre Dame" not in without.seeds
    assert len(with_rule.seeds) == 12
    assert selection.playoff_format(2026).top12 == ("Notre Dame",)
    assert selection.playoff_format(2025).top12 == ()
