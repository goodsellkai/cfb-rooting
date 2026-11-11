"""How much the preseason AP poll counts in a sample season's early rankings.

Run ``python -m cfbroot.preseason``. For each of the first ten weeks of
2023-26, the model's rating on the games so far is blended with the
preseason poll the way a sample season blends it, ranked the way the
committee is modelled to, and scored against the AP's poll after that week.
It prints what is behind AP_PRIOR_WEIGHT, AP_PRIOR_WEEKS and AP_PRIOR_TOP in
``sim/sample.py``.

Besides the mean places off, two things a reader notices: a top-six team
that loses early and does not drop, and a ranked team that wins and still
slides three places or more.
"""

from __future__ import annotations

import argparse

import numpy as np
from scipy.stats import norm

from . import massey, selection
from .backtest import fcs_games_for
from .data.espn_source import AP_POLL, fetch_polls
from .data.loader import load_season

WEEKS = range(1, 11)


def _season(year):
    state = load_season(year)
    state.cfp_polls = {}
    fcs = fcs_games_for(year)
    by_id = {str(t.team_id): t.school for t in state.teams if t.team_id is not None}
    polls = {int(w): {by_id[i]: r for i, r in ranks.items() if i in by_id}
             for w, ranks in fetch_polls(year, AP_POLL).items()}
    names = {t.idx: t.school for t in state.teams}
    pre = {names[i]: r for i, r in (state.polls.get("ap_preseason") or {}).items()}
    if not pre:                 # older seasons carry the preseason poll as week 1
        pre = polls[1]
    polls[1] = pre
    rated, results = {}, {}
    for w in WEEKS:
        rated[w] = massey.rate_season(state, extra_games=fcs, through_week=w)
        done = [g for g in state.games
                if g["week"] == w and g["status"] != 0 and not g["is_ccg"]]
        results[w] = ({g["away"] if g["status"] == 1 else g["home"] for g in done},
                      {g["home"] if g["status"] == 1 else g["away"] for g in done})
    return state, polls, pre, rated, results


def _places(season, w, weight, weeks, top):
    state, _, pre, rated, _ = season
    vals = rated[w]
    n = max(len(state.fbs_teams), 2)
    spread = float(np.std(list(vals.values()))) or 1.0
    mean = float(np.mean(list(vals.values())))
    a = weight * max(0.0, weeks - (w - 1)) / weeks
    out = {}
    for t, v in vals.items():
        z = float(norm.ppf(1.0 - (pre[t] - 0.5) / n)) if t in pre else 0.0
        z = min(z, top) if top else z
        out[t] = (1 - a) * v + a * (mean + z * spread)
    order = selection.rank_teams(state, out, through_week=w).order
    return {t: i + 1 for i, t in enumerate(order)}


def _score(seasons, weight, weeks, top):
    off, stuck, top_losers, wins, slides, by_year = [], 0, 0, 0, 0, {}
    for year, season in seasons.items():
        polls, results = season[1], season[4]
        prev, mine = dict(polls[1]), []
        for w in WEEKS:
            if w + 1 not in polls:
                continue
            place = _places(season, w, weight, weeks, top)
            after, before = polls[w + 1], polls[w]
            mine.append(np.mean([abs(place[t] - r) for t, r in after.items() if t in place]))
            losers, winners = results[w]
            for t, r in before.items():
                if t in losers and r <= 6 and w <= 3 and t in place:
                    top_losers += 1
                    stuck += (place[t] - prev.get(t, place[t]) <= 1
                              and after.get(t, 28) - r >= 3)
                if t in winners and t in after and t in prev and w >= 2:
                    wins += 1
                    slides += prev[t] - place[t] < -2
            prev = place
        by_year[year] = float(np.mean(mine))
        off += mine
    return float(np.mean(off)), stuck, top_losers, slides / max(wins, 1), by_year


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cfbroot.preseason", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025, 2026])
    args = ap.parse_args(argv)
    seasons = {y: _season(y) for y in args.years}
    print("weight  weeks  top    places off  top-6 losers not dropping  "
          "ranked winners sliding 3+   by season")
    for weight in (0.7, 0.85, 1.0):
        for weeks in (6, 7, 8, 9):
            for top in (None, 2.2, 2.0, 1.8):
                off, stuck, n, slide, by_year = _score(seasons, weight, weeks, top)
                print(f"{weight:>5}  {weeks:>5}  {str(top):>4}   {off:8.2f}   "
                      f"{stuck:>12} of {n:<14} {100 * slide:9.1f}%          "
                      + "  ".join(f"{y} {v:.2f}" for y, v in by_year.items()), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
