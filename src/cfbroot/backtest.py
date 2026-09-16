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
import os

import numpy as np

from .config import METRIC_NAMES, POWER_CONFERENCES, SimConfig
from .data.loader import load_season
from .sim import run

WIN_CONF = METRIC_NAMES.index("win_conference")
FIELD = 12


def _client():
    import cfbd

    from .config import api_key
    return cfbd, cfbd.Configuration(access_token=api_key())


def actual_field(year: int) -> dict:
    """The teams the committee actually selected, by school."""
    cfbd, cfg = _client()
    with cfbd.ApiClient(cfg) as c:
        return {p.team.school: p for p in cfbd.PlayoffsApi(c).get_cfp_participants(year=year)}


def committee_ranking(year: int) -> dict[str, int]:
    """The last Playoff Committee Rankings poll of the season."""
    cfbd, cfg = _client()
    out: dict[str, int] = {}
    with cfbd.ApiClient(cfg) as c:
        api = cfbd.RankingsApi(c)
        for week in (14, 15, 16):
            try:
                weeks = api.get_rankings(year=year, week=week)
            except Exception:  # noqa: BLE001
                continue
            for w in weeks:
                for poll in w.polls:
                    if "Playoff Committee" in poll.poll:
                        out = {r.school: r.rank for r in poll.ranks}
    return out


def proxy_ranking(state) -> tuple[dict[str, int], set[str]]:
    """Committee rank for every FBS team, plus the conference champions."""
    cfg = SimConfig(n_sims=24, batch_size=24, seed=1)
    ranks: dict[str, int] = {}
    champs: set[str] = set()
    for i, t in enumerate(state.fbs_teams):
        res = run(state, t.idx, cfg)
        ranks[t.school] = int(np.argmax(res.rank_hist))
        if i == 0:
            champs = {u.school for u in state.fbs_teams
                      if res.team_counts[u.idx, WIN_CONF] == res.n_sims}
    return ranks, champs


def pick_field(ranks, champs, conference_of, rule: str) -> list[str]:
    order = sorted(ranks, key=lambda s: ranks[s])
    sel: list[str] = []
    if rule == "2026":
        for s in order:
            if s in champs and conference_of.get(s) in POWER_CONFERENCES:
                sel.append(s)
        for s in order:
            if s in champs and conference_of.get(s) not in POWER_CONFERENCES:
                sel.append(s)
                break
    else:
        for s in order:
            if s in champs and len(sel) < 5:
                sel.append(s)
    for s in order:
        if len(sel) >= FIELD:
            break
        if s not in sel:
            sel.append(s)
    return sel[:FIELD]


def run_year(year: int) -> None:
    state = load_season(year)
    state.params = dataclasses.replace(state.params, rating_sd=0.0)
    ranks, champs = proxy_ranking(state)
    conference_of = {t.school: t.conference for t in state.fbs_teams}

    print(f"\n{'=' * 70}\n{year}\n{'=' * 70}")

    parts = actual_field(year)
    if len(parts) == FIELD:
        for rule, label in (("2026", "4 power champions + best other (2026 rule)"),
                            ("hist", "5 highest ranked champions (2024-25 rule)")):
            mine = set(pick_field(ranks, champs, conference_of, rule))
            print(f"\nField, {label}: {len(mine & set(parts))}/{FIELD} correct")
            if mine - set(parts):
                print("   I add:  " + ", ".join(sorted(mine - set(parts))))
            if set(parts) - mine:
                print("   I miss: " + ", ".join(sorted(set(parts) - mine)))
    else:
        print(f"\n{len(parts)}-team playoff this season, field not comparable")

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
