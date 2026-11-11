"""The committee's ranking, carried into the simulation."""

import dataclasses

import numpy as np

from cfbroot import lean, massey, selection
from cfbroot.config import METRIC_NAMES, SimConfig
from cfbroot.sim import run_league
from cfbroot.sim.sample import committee_now

PLAYOFF = METRIC_NAMES.index("make_playoff")


def model_order(state, through):
    """The model's own order on the games through a week, as team indices."""
    ratings = massey.rate_season(state, extra_games=state.fcs_games, through_week=through)
    idx = {t.school: t.idx for t in state.fbs_teams}
    return [idx[s] for s in selection.rank_teams(state, ratings, through_week=through).order]


def promoted(state, through, frm=13, to=3):
    """A committee top 25 that is the model's with one team moved up."""
    order = model_order(state, through)[:25]
    team = order.pop(frm - 1)
    order.insert(to - 1, team)
    return team, {i: k + 1 for k, i in enumerate(order)}


def with_poll(state, week, poll):
    return dataclasses.replace(state, cfp_polls={week: poll})


def test_a_gap_is_the_rating_at_the_committees_place_less_the_teams_own():
    vals = {"A": 1.0, "B": 0.8, "C": 0.5, "D": 0.1}
    got = lean.gaps(vals, {"A": 1, "C": 2, "B": 3})
    assert got["A"] == 0.0
    assert np.isclose(got["C"], 0.3) and np.isclose(got["B"], -0.3)
    assert got["D"] == 0.0          # neither ranks it


def test_a_team_the_committee_left_out_is_taken_to_be_26th():
    vals = {f"T{i:02d}": 3.0 - 0.1 * i for i in range(30)}
    poll = {t: i for i, t in enumerate(sorted(vals)[1:26], 1)}      # all but the model's first
    got = lean.gaps(vals, poll)
    assert np.isclose(got["T00"], vals["T25"] - vals["T00"])


def test_the_season_takes_the_committees_newest_ranking(midseason):
    week = midseason.current_week()
    team, poll = promoted(midseason, week - 1)
    known = lean.for_state(with_poll(midseason, week, poll))
    assert known.week == week
    assert known.gap[team] > 0
    assert known.left == massey.selection_week(midseason) + 1 - week
    assert lean.for_state(midseason).week is None


def test_a_ranking_made_after_games_the_season_has_not_seen_is_passed_over(midseason):
    week = midseason.current_week()
    _, poll = promoted(midseason, week - 1)
    assert lean.for_state(with_poll(midseason, week + 1, poll)).week is None
    both = dataclasses.replace(midseason, cfp_polls={week: poll, week + 1: poll})
    assert lean.for_state(both).week == week


def test_less_of_a_gap_is_carried_the_more_polls_are_left():
    shares = [lean.share(k) for k in range(8)]
    assert shares[0] == 1.0
    assert all(a >= b for a, b in zip(shares, shares[1:]))
    assert shares[-1] == shares[-2] > 0       # past the table it holds
    known = lean.Lean(week=11, gap={3: 0.2}, left=2)
    assert np.isclose(known.carried()[3], 0.2 * lean.share(2))
    assert np.isclose(known.carried(0)[3], 0.2)


def test_noise_switched_off_stays_off():
    known = lean.Lean(week=11, gap={}, left=3)
    assert known.sd(0.0) == 0.0
    assert np.isclose(known.sd(selection.COMMITTEE_SD), lean.noise(3))
    assert lean.Lean().sd(0.05) == 0.05     # no ranking, no change


def test_the_projection_gives_the_committees_order_back_until_games_are_played(midseason):
    week = midseason.current_week()
    team, poll = promoted(midseason, week - 1)
    proj = committee_now(with_poll(midseason, week, poll))
    assert proj["since"] is None
    assert proj["order"].index(team) + 1 == poll[team]
    alone = committee_now(midseason)
    assert alone["since"] is None
    assert alone["order"].index(team) + 1 > poll[team]


def test_the_projection_is_ahead_of_the_committee_after_a_week_of_games(midseason):
    week = midseason.current_week() - 1         # a ranking from before the last games
    team, poll = promoted(midseason, week - 1)
    proj = committee_now(with_poll(midseason, week, poll))
    assert proj["since"] == week
    alone = committee_now(midseason)
    assert proj["order"].index(team) < alone["order"].index(team)


def test_the_simulation_follows_the_committee(midseason):
    week = midseason.current_week()
    order = model_order(midseason, week - 1)
    team = order[14]
    poll = {i: k + 1 for k, i in enumerate([team] + [i for i in order[:25] if i != team][:24])}
    state = with_poll(midseason, week, poll)
    ki = state.kernel_inputs()
    assert ki.lean[team] > 0 and midseason.kernel_inputs().lean.any() == False  # noqa: E712
    cfg = SimConfig(n_sims=3000, batch_size=3000, seed=5)
    col = {int(i): j for j, i in enumerate(np.flatnonzero(ki.is_fbs))}[team]
    liked = run_league(state, cfg).team_counts[col, PLAYOFF]
    alone = run_league(midseason, cfg).team_counts[col, PLAYOFF]
    assert liked > alone


def championship_week(state, played):
    """The season with its last week made the title games' week: every game
    before it played, one title game between the top two, played or not."""
    cut = massey.selection_week(state)
    games = [dict(g) for g in state.games]
    for g in games:
        if g["status"] == 0:
            g.update(status=1, completed=True, home_points=24, away_points=17)
    order = model_order(dataclasses.replace(state, games=games), cut - 1)
    a, b = state.teams[order[0]], state.teams[order[1]]
    games.append({**games[0], "game_id": -1, "week": cut, "is_ccg": True,
                  "home_idx": a.idx, "away_idx": b.idx, "home": a.school, "away": b.school,
                  "neutral": True, "status": 2 if played else 0, "completed": played,
                  "home_points": 10 if played else None,
                  "away_points": 31 if played else None})
    return dataclasses.replace(state, games=games), cut, order


def test_the_week_the_committee_has_seen_stops_for_the_title_games(midseason):
    assert lean.seen_week(midseason) == midseason.current_week() - 1
    before, cut, _ = championship_week(midseason, played=False)
    after, _, _ = championship_week(midseason, played=True)
    assert lean.seen_week(before) == cut - 1
    assert lean.seen_week(after) == cut


def test_the_projection_goes_on_past_the_title_games(midseason):
    after, cut, order = championship_week(midseason, played=True)
    poll = {i: k + 1 for k, i in enumerate(order[:25])}
    proj = committee_now(dataclasses.replace(after, cfp_polls={cut: poll}))
    assert proj["final"] and proj["since"] == cut and proj["week"] == cut
    # the winner goes past the team it beat, and nobody else moves
    assert proj["order"][:2] == [order[1], order[0]]
    assert proj["order"][2:25] == order[2:25]
    assert order[1] in proj["leaders"].values()


def test_the_final_ranking_stands_once_it_is_out(midseason):
    after, cut, order = championship_week(midseason, played=True)
    final = [order[3], order[1], order[0]] + [i for i in order[4:26]]
    poll = {i: k + 1 for k, i in enumerate(final)}
    state = dataclasses.replace(after, cfp_polls={cut + 1: poll})
    known = lean.for_state(state)
    assert known.final and not known.holds
    proj = committee_now(state)
    assert proj["final"] and proj["since"] is None
    assert proj["order"][:25] == final
    ki = state.kernel_inputs()
    assert ki.committee_sd == 0.0 and ki.h2h_depth == 0
    league = run_league(state, SimConfig(n_sims=400, batch_size=400, seed=2))
    col = {int(i): j for j, i in enumerate(np.flatnonzero(ki.is_fbs))}
    ranks = [int(np.argmax(league.rank_hist[col[i]])) for i in final]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert all(league.rank_hist[col[i]].max() == 400 for i in final)


def test_a_sample_season_ranks_the_weeks_played_as_they_were(midseason):
    from cfbroot.sim.sample import sample_season, weekly_systems
    played = lean.seen_week(midseason)            # the last week whose games are in
    order = model_order(midseason, played - 1)
    real = [order[3], order[0], order[2], order[1]] + order[4:25]
    # The committee ranked the teams after the week before; the latest week is played.
    state = with_poll(midseason, played, {i: k + 1 for k, i in enumerate(real)})
    ki = state.kernel_inputs()
    weekly = weekly_systems(state, ki)
    proj = committee_now(state, ki, weekly)
    assert proj["since"] == played
    seasons = [sample_season(state, seed=s, ki=ki, weekly=weekly) for s in (1, 2, 3)]
    for one in seasons:
        assert one["polls_actual"] == [played - 1]
        assert one["polls"][str(played - 1)] == real
        # the week played since is the projection, the same in every season
        assert one["polls"][str(played)] == proj["order"][:25]
    # the weeks after go on from there, and differ from season to season
    later = [str(w) for w in range(played + 1, played + 4) if str(w) in seasons[0]["polls"]]
    assert later and any(seasons[0]["polls"][w] != seasons[1]["polls"][w] for w in later)
    # a season replayed from the start takes none of it
    fresh = sample_season(state, seed=1, from_start=True, ki=ki, weekly=weekly)
    assert fresh["polls_actual"] == []
    assert fresh["polls"][str(played - 1)] != real
