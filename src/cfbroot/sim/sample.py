"""One simulated season, kept in full so it can be played back.

The Monte Carlo keeps only tallies. This runs a single season through the same
steps and keeps everything: every score, the standings, the title games, the
committee's ranking, the field and the bracket. It uses the same rules as the
kernel, and the same rating, massey.rate_selection_day(), that the kernel
reproduces, so a sample season is one draw from the distribution the odds come
from.

Scores are whole points here, since they are for reading. The kernel keeps the
unrounded margin; rounding changes a rating by a hair and never a result.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from .. import massey, selection
from ..config import MAX_CONF_SIZE
from ..data.season import TO_SIMULATE, SeasonState
from .kernels import _order_conference

HOME_WON, AWAY_WON = 1, 2


def _score(rng, mu, p) -> tuple[int, int]:
    """A drawn game, as whole points, home side first.

    The margin comes from the same distribution as the kernel's, so the win
    probability is the same; the total is drawn around it the same way.
    """
    m = mu + p.sigma * rng.normal()
    t = p.total_base + p.total_slope * abs(m) + p.total_sd * rng.normal()
    t = max(t, abs(m))
    hp, ap = round(0.5 * (t + m)), round(0.5 * (t - m))
    if (hp > ap) != (m > 0) or hp == ap:
        # Rounding tied or flipped a close one; the drawn margin decides it.
        hp, ap = (max(hp, ap) + 1, min(hp, ap)) if m > 0 else (min(hp, ap), max(hp, ap) + 1)
    return int(hp), int(ap)


def sample_season(state: SeasonState, seed: int | None = None) -> dict:
    """Simulate one season from ``state`` and return all of it, JSON ready."""
    rng = np.random.default_rng(seed)
    p = state.params
    ki = state.kernel_inputs()
    teams = state.teams
    fbs = [t for t in teams if t.is_fbs]

    # Each team's true strength this season: its rating plus an error that
    # lasts all year, as in the kernel. Games are played on this.
    eff = {t.idx: t.rating + (p.rating_sd * rng.normal() if t.is_fbs else 0.0)
           for t in teams}

    games = []
    for g in state.games:
        if g["is_ccg"]:
            continue
        g = dict(g)
        g["real"] = g["status"] != TO_SIMULATE
        if not g["real"]:
            edge = 0.0 if g["neutral"] else p.hfa
            mu = p.rating_scale * (eff[g["home_idx"]] - eff[g["away_idx"]]) + edge
            g["home_points"], g["away_points"] = _score(rng, mu, p)
            g["status"] = HOME_WON if g["home_points"] > g["away_points"] else AWAY_WON
            g["completed"] = True
        games.append(g)

    cut = massey.selection_week(state)
    ccg_week = cut if cut is not None else max(g["week"] for g in games) + 1
    sim = dataclasses.replace(state, games=list(games),
                              conferences=[dataclasses.replace(c) for c in state.conferences])

    # Standings, ordered with the kernel's own tiebreak code. The last
    # tiebreak is the power rating before the title games, as in the kernel.
    before = massey.fit_season(sim, include_ccg=False, through_week=cut,
                               extra_games=state.fcs_games, params=state.massey)
    score = np.zeros(len(teams))
    for j in range(before.n_fbs):
        t = int(before.fbs_idx[j])
        if t < len(teams):
            score[t] = before.power[j]
    winner = np.zeros(ki.g_home.size, dtype=np.uint8)
    for g in games:
        winner[g["sim_idx"]] = g["status"]
    cw = np.zeros(len(teams), dtype=np.int32)
    cl = np.zeros(len(teams), dtype=np.int32)
    for i in np.flatnonzero(ki.g_conf):
        w, l = ((ki.g_home[i], ki.g_away[i]) if winner[i] == HOME_WON
                else (ki.g_away[i], ki.g_home[i]))
        cw[w] += 1
        cl[l] += 1

    order_buf = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
    pct = np.zeros(MAX_CONF_SIZE)
    h2h = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
    mark = np.full(len(teams), -1, dtype=np.int32)
    champions: set[str] = set()
    conferences = []
    title_games = []
    for c in state.conferences:
        members = np.array(c.team_idxs, dtype=np.int32)
        n_m = members.size
        if n_m == 0:
            continue
        _order_conference(members, n_m, cw, cl, score, ki.conf_games,
                          ki.conf_games_ptr[c.idx], ki.conf_games_ptr[c.idx + 1],
                          ki.g_home, ki.g_away, winner, order_buf, pct, mark, h2h)
        order = [int(members[order_buf[k]]) for k in range(n_m)]
        entry = {"name": c.name, "order": order, "champion": None,
                 "title_game": None, "crowns": c.crowns_champion}
        conferences.append(entry)
        if not c.crowns_champion:
            continue
        if c.fixed_ccg is not None:
            t1, t2, st = c.fixed_ccg
        elif not c.has_ccg or n_m < 2:
            entry["champion"] = order[0]
            champions.add(teams[order[0]].school)
            continue
        else:
            t1, t2, st = order[0], order[1], TO_SIMULATE
            if len(c.divisions) >= 2:
                t2 = next((t for t in order[1:]
                           if teams[t].div_idx != teams[t1].div_idx), order[1])
        real = next((g for g in state.games if g["is_ccg"] and g["status"] != TO_SIMULATE
                     and {g["home_idx"], g["away_idx"]} == {t1, t2}), None)
        if real is not None:
            hp, ap, t1, t2 = (real["home_points"], real["away_points"],
                              real["home_idx"], real["away_idx"])
            neutral = real["neutral"]
        else:
            hp, ap = _score(rng, p.rating_scale * (eff[t1] - eff[t2]), p)
            neutral = True
        won, lost = (t1, t2) if hp > ap else (t2, t1)
        entry["champion"] = won
        champions.add(teams[won].school)
        tg = {"conference": c.name, "home": t1, "away": t2, "home_points": hp,
              "away_points": ap, "winner": won, "loser": lost, "neutral": neutral,
              "real": real is not None}
        entry["title_game"] = tg
        title_games.append(tg)
        sim.games.append({
            "game_id": -1000 - c.idx, "week": ccg_week, "season_type": "regular",
            "home_idx": t1, "away_idx": t2, "home": teams[t1].school,
            "away": teams[t2].school, "neutral": neutral, "conference_game": True,
            "home_points": hp, "away_points": ap,
            "status": HOME_WON if hp > ap else AWAY_WON, "completed": True,
            "is_ccg": True, "notes": f"{c.name} Championship"})

    # Selection Sunday: the rating, the committee's noise and rules, the field.
    ratings, _ = massey.rate_selection_day(sim, extra_games=state.fcs_games,
                                           params=state.massey)
    ranking = selection.rank_teams(sim, ratings, rng=rng,
                                   committee_sd=p.committee_sd,
                                   through_week=massey.selection_week(sim))
    fmt = selection.playoff_format(state.year)
    field = selection.pick_field(ranking.order, champions,
                                 {t.school: t.conference for t in fbs},
                                 rule=fmt.bids, n_byes=p.n_byes,
                                 champion_byes=fmt.champion_byes)
    by_name = {t.school: t.idx for t in teams}
    seeds = [by_name[s] for s in field.seeds]

    # The bracket: 5-12, 6-11, 7-10 and 8-9 at the higher seed, then neutral
    # sites, 1 against the 8-9 winner and so on, as in the kernel.
    def play(a, b, home_field):
        edge = p.hfa if home_field else 0.0
        hp, ap = _score(rng, p.rating_scale * (eff[a] - eff[b]) + edge, p)
        return {"home": a, "away": b, "home_points": hp, "away_points": ap,
                "winner": a if hp > ap else b, "neutral": not home_field}

    rounds = []
    if len(seeds) == 12:
        first = [play(seeds[i], seeds[j], True) for i, j in ((4, 11), (5, 10), (6, 9), (7, 8))]
        w = [g["winner"] for g in first]
        quarters = [play(seeds[0], w[3], False), play(seeds[1], w[2], False),
                    play(seeds[2], w[1], False), play(seeds[3], w[0], False)]
        q = [g["winner"] for g in quarters]
        semis = [play(q[0], q[3], False), play(q[1], q[2], False)]
        final = [play(semis[0]["winner"], semis[1]["winner"], False)]
        rounds = [{"name": "First round", "games": first},
                  {"name": "Quarterfinals", "games": quarters},
                  {"name": "Semifinals", "games": semis},
                  {"name": "National championship", "games": final}]
    champion = rounds[-1]["games"][0]["winner"] if rounds else None

    return _clean({
        "seed": seed, "year": state.year, "selection_week": cut,
        "games": [{"week": g["week"], "home": g["home_idx"], "away": g["away_idx"],
                   "home_points": g["home_points"], "away_points": g["away_points"],
                   "neutral": g["neutral"], "conference": bool(ki.g_conf[g["sim_idx"]]),
                   "real": g["real"], "p_home": g.get("pwin_home")}
                  for g in games],
        "conferences": conferences,
        "title_games": title_games,
        "ranking": [{"team": by_name[s], "rating": ranking.ratings[s]}
                    for s in ranking.order],
        "field": [{"team": t, "seed": i + 1, "bye": s in field.byes,
                   "how": field.auto.get(s, "")}
                  for i, (t, s) in enumerate(zip(seeds, field.seeds))],
        "rounds": rounds,
        "champion": champion,
    })


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not math.isfinite(f) else round(f, 4)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj
