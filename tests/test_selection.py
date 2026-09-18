"""Ranking adjustments and playoff field selection."""

import pytest

from cfbroot import massey, selection

from conftest import make_mini_season

CONF = {"A": "SEC", "B": "Big Ten", "C": "Big 12", "D": "ACC",
        "E": "Mountain West", "F": "Mountain West", "G": "American Athletic",
        "H": "FBS Independents"}


# Head to head

def test_lower_team_that_won_swaps_with_the_one_above_it():
    order, moves = selection.head_to_head_swap(["A", "B", "C"], {("B", "A")})
    assert order == ["B", "A", "C"]
    assert moves == [("B", "A", 1)]


def test_a_win_over_someone_far_above_does_not_leapfrog():
    order, moves = selection.head_to_head_swap(["A", "B", "C", "D"], {("D", "A")})
    assert order == ["A", "B", "C", "D"]
    assert moves == []


def test_a_split_series_does_not_swap():
    order, _ = selection.head_to_head_swap(["A", "B"], {("B", "A"), ("A", "B")})
    assert order == ["A", "B"]


def test_chained_results_resolve_one_pair_at_a_time():
    """C beat B and B beat A. Both pairs qualify but they share B.

    Passes run top down, so A and B swap first, which leaves B no longer next
    to C. Only one of the two overlapping swaps can apply, and the higher pair
    wins.
    """
    order, moves = selection.head_to_head_swap(["A", "B", "C"],
                                               {("C", "B"), ("B", "A")})
    assert order == ["B", "A", "C"]
    assert moves == [("B", "A", 1)]


def test_a_swap_that_creates_a_new_pair_is_picked_up_on_the_next_pass():
    # D beat C, and once D moves up it is next to B, which it also beat.
    order, moves = selection.head_to_head_swap(["A", "B", "C", "D"],
                                               {("D", "C"), ("D", "B")})
    assert order == ["A", "D", "B", "C"]
    assert len(moves) == 2


def test_a_circle_of_wins_terminates():
    order, _ = selection.head_to_head_swap(["A", "B", "C"],
                                           {("B", "A"), ("C", "B"), ("A", "C")},
                                           max_passes=4)
    assert sorted(order) == ["A", "B", "C"]


def test_head_to_head_pairs_can_ignore_title_games(midseason):
    played = selection.head_to_head_pairs(midseason)
    assert played and all(len(p) == 2 for p in played)


# Field selection, 2026-27 rules

ORDER = ["A", "B", "E", "H", "C", "G", "D", "F"]


def test_power_champions_are_guaranteed_however_low_they_rank():
    f = selection.pick_field(ORDER, {"A", "B", "C", "D", "E"}, CONF,
                             rule="2026", size=6)
    for t in ("A", "B", "C", "D"):
        assert t in f, f"{t} is a power champion and must be in"
    assert "champion" in f.auto["D"]


def test_the_group_of_six_bid_ignores_whether_they_won_the_conference():
    """E is the best Group of Six team; F won that conference. E gets it."""
    f = selection.pick_field(ORDER, {"A", "B", "C", "D", "F"}, CONF,
                             rule="2026", size=6)
    assert "E" in f and "Group of Six" in f.auto["E"]
    assert f.auto.get("F", "") != "highest ranked Group of Six (Mountain West)"


def test_an_independent_cannot_take_the_group_of_six_bid():
    order = ["H", "E", "A", "B", "C", "D"]      # H is independent and ranked top
    f = selection.pick_field(order, {"A", "B", "C", "D"}, CONF,
                             rule="2026", size=5)
    assert "Group of Six" in f.auto["E"]
    assert "Group of Six" not in f.auto.get("H", "")


def test_seeding_is_straight_off_the_ranking():
    f = selection.pick_field(ORDER, {"A", "B", "C", "D", "E"}, CONF,
                             rule="2026", size=6, n_byes=2)
    place = {t: i for i, t in enumerate(ORDER)}
    assert f.seeds == sorted(f.seeds, key=lambda t: place[t])
    assert f.byes == f.seeds[:2]
    assert f.seed_of(f.seeds[0]) == 1


def test_the_old_rule_takes_the_five_highest_ranked_champions():
    f = selection.pick_field(ORDER, {"A", "B", "C", "D", "F"}, CONF,
                             rule="2024", size=5)
    assert "F" in f            # a champion, even ranked last
    assert "E" not in f        # better team, no title, no automatic bid


def test_2024_seeding_gives_the_byes_to_champions():
    """B is ranked second but has no title, so champion D takes its bye."""
    f = selection.pick_field(ORDER, {"A", "C", "D", "E", "F"}, CONF,
                             rule="2024", size=6, n_byes=4,
                             champion_byes=True)
    assert f.byes == ["A", "E", "C", "D"]
    assert f.seeds[4:] == ["B", "F"]         # then straight off the ranking


def test_each_season_runs_its_own_rules():
    assert selection.playoff_format(2026) == selection.PlayoffFormat("2026", False)
    assert selection.playoff_format(2025) == selection.PlayoffFormat("2024", False)
    assert selection.playoff_format(2024) == selection.PlayoffFormat("2024", True)


def test_no_automatic_bids_just_takes_the_top_of_the_ranking():
    f = selection.pick_field(ORDER, {"D"}, CONF, rule="none", size=3)
    assert f.seeds == ORDER[:3]


# Title games count one way only

def ccg_season():
    """Four teams, then a title game between the top two."""
    games = [("A", "B", True), ("A", "C", True), ("B", "C", True),
             ("A", "D", True), ("B", "D", True), ("C", "D", True),
             ("B", "A", True)]          # the title game: B beats A
    st = make_mini_season(games)
    st.games[-1]["is_ccg"] = True
    return st


def test_a_title_game_loss_is_dropped_and_a_win_is_kept():
    st = ccg_season()
    won, lost = massey.title_game_results(st)
    assert won == {"B"} and lost == {"A"}

    merged, diag = massey.rate_selection_day(st)
    base = massey.rate_season(st, include_ccg=False)
    full = massey.rate_season(st, include_ccg=True)

    assert merged["A"] == pytest.approx(base["A"])   # the loser never played it
    assert merged["B"] == pytest.approx(full["B"])   # the winner keeps it
    assert merged["B"] > base["B"]                   # and it helped
    assert diag["title_game_winners"] == 1
    assert diag["title_game_losers"] == 1


# Conference title game jump

RAT = {"A": 2.00, "B": 1.90, "C": 1.86, "D": 1.84, "E": 1.50}
BASE = ["A", "B", "C", "D", "E"]


def test_a_close_title_game_winner_moves_in_front_of_the_loser():
    """D beat B. They are 0.06 apart, so D takes B's place."""
    order, moves = selection.title_game_jump(BASE, RAT, [("D", "B")])
    assert order == ["A", "D", "B", "C", "E"]
    assert moves == [("D", "B", 4, 2)]


def test_the_loser_keeps_its_place_when_it_is_clearly_better():
    """E beat A, but they are 0.5 apart. One game does not overturn that."""
    order, moves = selection.title_game_jump(BASE, RAT, [("E", "A")])
    assert order == BASE
    assert moves == []


def test_the_margin_is_the_only_thing_that_limits_the_jump():
    """Nothing about how many teams are in between; only the rating gap."""
    close = {"A": 2.00, "B": 1.99, "C": 1.98, "D": 1.97, "E": 1.96}
    order, _ = selection.title_game_jump(BASE, close, [("E", "A")])
    assert order == ["E", "A", "B", "C", "D"]   # past three, gap 0.04


def test_a_winner_already_ahead_is_left_alone():
    order, moves = selection.title_game_jump(BASE, RAT, [("B", "D")])
    assert order == BASE
    assert moves == []


def test_the_margin_is_read_after_the_title_game_credit():
    """Just inside and just outside the threshold, on the same pair."""
    inside = dict(RAT, D=1.91)          # gap 0.09 -> 1.90 - 1.91 is negative
    outside = dict(RAT, D=1.75)         # gap 0.15
    assert selection.title_game_jump(BASE, inside, [("D", "B")])[0][1] == "D"
    assert selection.title_game_jump(BASE, outside, [("D", "B")])[0] == BASE


def test_the_threshold_is_configurable():
    order, _ = selection.title_game_jump(BASE, RAT, [("E", "A")], margin=1.0)
    assert order[0] == "E"


def test_title_game_pairs_reads_winner_first():
    st = ccg_season()
    assert selection.title_game_pairs(st) == [("B", "A")]


# Committee noise

def test_no_generator_means_a_repeatable_ranking(midseason):
    rat = massey.rate_season(midseason)
    a = selection.rank_teams(midseason, rat)
    b = selection.rank_teams(midseason, rat)
    assert a.order == b.order
    assert a.ratings == rat


def test_a_generator_shuffles_the_order_but_not_much(midseason):
    import numpy as np

    rat = massey.rate_season(midseason)
    fixed = selection.rank_teams(midseason, rat).order
    rng = np.random.default_rng(3)
    moved = []
    for _ in range(40):
        noisy = selection.rank_teams(midseason, rat, rng=rng).order
        moved.append(np.mean([abs(noisy.index(t) - i)
                              for i, t in enumerate(fixed[:25])]))
    assert 0.3 < float(np.mean(moved)) < 6.0
    assert fixed != selection.rank_teams(midseason, rat, rng=rng).order


def test_the_noise_can_be_switched_off(midseason):
    import numpy as np

    rat = massey.rate_season(midseason)
    quiet = selection.rank_teams(midseason, rat, rng=np.random.default_rng(0),
                                 committee_sd=0.0)
    assert quiet.order == selection.rank_teams(midseason, rat).order


def test_the_calibrated_spread_is_a_small_share_of_the_rating():
    assert 0.02 <= selection.COMMITTEE_SD <= 0.15
