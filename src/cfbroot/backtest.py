"""Check the committee proxy against real playoff selections and rankings.

Run ``python -m cfbroot.backtest`` for the last few finished seasons.

A finished season has nothing left to simulate, so the kernel is deterministic
and its ranking is the committee proxy applied to real results. Rating error is
switched off for the same reason: this measures the proxy, not a forecast.

Two caveats when reading the field comparison. The 12-team playoff only started
in 2024, and the auto-bid rule has changed: 2024 and 2025 took the five
highest-ranked conference champions, while 2026 guarantees the four power
conference champions plus the best other champion. Both are reported.
"""

from __future__ import annotations

import argparse
import dataclasses

import numpy as np

from . import selection
from .config import METRIC_NAMES, POWER_CONFERENCES, SimConfig
from .data.loader import load_season
from .sim import run_league

WIN_CONF = METRIC_NAMES.index("win_conference")
FIELD = 12


def _client():
    import cfbd

    from .config import api_key
    return cfbd, cfbd.Configuration(access_token=api_key())


def committee_polls(year: int) -> dict[int, dict[str, int]]:
    """Every Playoff Committee Rankings poll of the regular season, by week.

    From ESPN, which publishes the same polls and does not meter its calls.
    """
    from .data.espn_source import fetch_committee_polls

    by_id = {str(t.team_id): t.school for t in load_season(year).teams
             if t.team_id is not None}
    polls = fetch_committee_polls(year)
    return {int(week): {by_id[tid]: rank for tid, rank in ranks.items()
                        if tid in by_id}
            for week, ranks in polls.items()}


def actual_field(year: int) -> dict:
    """The teams the committee actually selected.

    Worked out from its final poll and that season's bid rules, which is how
    the real field was picked: in 2024 and 2025 the five highest ranked
    conference champions and seven at large, and in 2023 the top four.
    """
    state = load_season(year)
    order = sorted(committee_ranking(year).items(), key=lambda kv: kv[1])
    names = [t for t, _ in order]
    champs, _ = massey_champions(state)
    conf = {t.school: t.conference for t in state.fbs_teams}
    if year < 2024:
        field = selection.pick_field(names, champs, conf, rule="none", size=4)
    else:
        fmt = selection.playoff_format(year)
        field = selection.pick_field(names, champs, conf, rule=fmt.bids,
                                     champion_byes=fmt.champion_byes)
    return {t: i + 1 for i, t in enumerate(field.seeds)}


def massey_champions(state) -> tuple[set, set]:
    """Conference champions: title game winners, plus leagues without one."""
    from . import massey

    won, lost = massey.title_game_results(state, massey.selection_week(state))
    champs = set(won)
    crowned = {state.teams[g["home_idx"]].conf_idx for g in state.games
               if g["is_ccg"]}
    for c in state.conferences:
        if c.crowns_champion and c.idx not in crowned and c.team_idxs:
            members = [state.teams[i].school for i in c.team_idxs]
            champs.add(members[0])          # nobody to play for it
    return champs, set(lost)


def committee_ranking(year: int) -> dict[str, int]:
    """The final Playoff Committee Rankings poll, the one the field came from."""
    polls = committee_polls(year)
    return polls[max(polls)] if polls else {}


def committee_pre_title(year: int) -> dict[str, int]:
    """The last committee poll before championship weekend.

    The committee publishes one poll a week. The last is released after the
    title games and is the one the field comes from; the one before it is the
    standing going into championship weekend, which is where the Massey rating
    is fitted.
    """
    polls = committee_polls(year)
    weeks = sorted(polls)
    return polls[weeks[-2]] if len(weeks) >= 2 else {}


def proxy_ranking(state) -> tuple[dict[str, int], set[str]]:
    """Committee rank for every FBS team, plus the conference champions.

    A finished season is the same in every simulation, so one run answers for
    the whole league rather than one per team.
    """
    league = run_league(state, SimConfig(n_sims=24, batch_size=24, seed=1))
    ranks = {league.names[j]: int(np.argmax(league.rank_hist[j]))
             for j in range(len(league.names))}
    champs = {league.names[j] for j in range(len(league.names))
              if league.team_counts[j, WIN_CONF] == league.n_sims}
    return ranks, champs


def _compare(label: str, mine: dict[str, int], theirs: dict[str, int],
             conference_of: dict[str, str], depth: int = 25) -> None:
    """Print how one ranking lines up with a committee poll."""
    from scipy.stats import spearmanr

    pairs = [(v, mine[k], k) for k, v in theirs.items() if k in mine and v <= depth]
    if len(pairs) < 3:
        print()
        print(f"{label}: not enough overlap to compare")
        return
    arr = np.array([(a, b) for a, b, _ in pairs], dtype=float)
    err = np.abs(arr[:, 0] - arr[:, 1])
    power = [e for e, q in zip(err, pairs) if conference_of.get(q[2]) in POWER_CONFERENCES]
    other = [e for e, q in zip(err, pairs) if conference_of.get(q[2]) not in POWER_CONFERENCES]
    their12 = {k for k, v in theirs.items() if v <= FIELD}
    my12 = set(sorted(mine, key=lambda x: mine[x])[:FIELD])
    print()
    print(f"{label} ({len(pairs)} matched):")
    print(f"   Spearman {spearmanr(arr[:, 0], arr[:, 1]).statistic:+.2f}, "
          f"mean rank error {err.mean():.1f}")
    if power and other:
        print(f"   power conferences {np.mean(power):.1f}, others {np.mean(other):.1f}")
    print(f"   {len(their12 & my12)}/{len(their12)} of their top 12 are in my top 12")
    pairs.sort(key=lambda q: -abs(q[1] - q[0]))
    print("   biggest misses (negative means I rank them too high):")
    for c, m, school in pairs[:4]:
        print(f"      {school:<20}committee {c:>3}   mine {m:>3}   {m - c:+d}")


def fcs_games_for(year: int) -> list[dict]:
    """FCS games, or nothing if they cannot be fetched.

    Without them the rating still works; every non-FBS opponent just shares one
    rating, which costs about three places of accuracy per team.
    """
    from .data.cfbd_source import CFBDSource
    try:
        return CFBDSource(year).fcs_games()
    except Exception:  # noqa: BLE001
        return []


def massey_ranking(state, use_scores: bool = True) -> dict[str, int]:
    """Massey rank per school, fitted to the week before the title games."""
    from .massey import rate_season, title_game_week

    ccg_week = title_game_week(state)
    ratings = rate_season(state, extra_games=fcs_games_for(state.year),
                          use_scores=use_scores,
                          through_week=None if ccg_week is None else ccg_week - 1)
    order = sorted(ratings, key=lambda s: -ratings[s])
    return {s: i + 1 for i, s in enumerate(order)}


def run_year(year: int) -> None:
    state = load_season(year)
    state.params = dataclasses.replace(state.params, rating_sd=0.0)
    ranks, champs = proxy_ranking(state)
    conference_of = {t.school: t.conference for t in state.fbs_teams}

    print(f"\n{'=' * 70}\n{year}\n{'=' * 70}")

    parts = actual_field(year)
    if len(parts) == FIELD:
        for rule, label in (("2026", "2026-27 rules"),
                            ("2024", "five highest ranked champions (2024-25)")):
            order = sorted(ranks, key=lambda t: ranks[t])
            mine = set(selection.pick_field(order, champs, conference_of,
                                            rule=rule, size=FIELD).seeds)
            print(f"\nField, {label}: {len(mine & set(parts))}/{FIELD} correct")
            if mine - set(parts):
                print("   I add:  " + ", ".join(sorted(mine - set(parts))))
            if set(parts) - mine:
                print("   I miss: " + ", ".join(sorted(set(parts) - mine)))
    else:
        print(f"\n{len(parts)}-team playoff this season, field not comparable")

    pre = committee_pre_title(year)
    if pre:
        _compare("Massey vs the committee going into championship weekend",
                 massey_ranking(state), pre, conference_of)

    final = committee_ranking(year)
    if not final:
        return
    pairs = [(v, ranks[k], k) for k, v in final.items() if k in ranks and v <= 25]
    arr = np.array([(a, b) for a, b, _ in pairs], dtype=float)
    err = np.abs(arr[:, 0] - arr[:, 1])
    power = [e for e, p in zip(err, pairs) if conference_of.get(p[2]) in POWER_CONFERENCES]
    other = [e for e, p in zip(err, pairs) if conference_of.get(p[2]) not in POWER_CONFERENCES]

    from scipy.stats import spearmanr
    mine_order = sorted(ranks, key=lambda s: ranks[s])
    comm12 = {k for k, v in final.items() if v <= FIELD}
    print(f"\nAgainst the committee's final top 25 ({len(pairs)} matched):")
    print(f"   Spearman {spearmanr(arr[:, 0], arr[:, 1]).statistic:+.2f}, "
          f"mean rank error {err.mean():.1f}")
    print(f"   power conferences {np.mean(power):.1f}, others {np.mean(other):.1f}")
    print(f"   {len(comm12 & set(mine_order[:FIELD]))}/{len(comm12)} of their top 12 "
          f"are in my top 12")
    pairs.sort(key=lambda p: -abs(p[1] - p[0]))
    print("   biggest misses (negative means I rank them too high):")
    for c, m, school in pairs[:4]:
        print(f"      {school:<20}committee {c:>3}   mine {m:>3}   {m - c:+d}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cfbroot.backtest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", default=[2025, 2024, 2023])
    args = ap.parse_args(argv)
    for year in args.years:
        run_year(year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
