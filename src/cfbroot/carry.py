"""Measure how far FPI moves a team after a result.

Run ``python -m cfbroot.carry`` to reproduce carry_gain and carry_offset in
ModelParams.

FPI is published once a day. Between a Saturday kickoff and Sunday morning
the ratings do not know what happened, so the simulator would keep playing a
team at the strength it had on Friday. ESPN keeps every week's FPI, so the
move is straightforward to measure: for each pair of consecutive snapshots,
a team's rating change against the surprise in the game it played, meaning
the margin minus the margin the earlier snapshot predicted.

The move is a flat share of the surprise, linear from blowout losses to
blowout wins, and the share shrinks through the season as FPI grows more
certain about a team: 0.147 in week 3, 0.071 by week 12, averaging 0.098.

    share(week) = carry_gain / (week + carry_offset)

fits the ten weekly shares to within 0.004. Fitted on 2023-25 it put week 4
of 2026 at 0.129, against 0.129 measured. Half a point of the move is left
unexplained, well inside the rating error a simulated season draws anyway.
"""

from __future__ import annotations

import argparse

import numpy as np
from scipy.optimize import least_squares

from .config import ModelParams
from .data.loader import load_season
from .spread import fpi_snapshot

# ESPN's regular season week 1 snapshot was overwritten with the final
# ratings, so the first pair it can start from is week 2 to week 3.
WEEKS = range(2, 12)


def measure(years: list[int], params: ModelParams | None = None) -> list[dict]:
    """Per week, the share of the surprise FPI put into the rating."""
    p = params or ModelParams()
    rows: dict[int, list] = {}
    for y in years:
        state = load_season(y)
        ids = {t.idx: t.team_id for t in state.fbs_teams}
        played: dict[int, list] = {}
        for g in state.games:
            if g["status"] == 0 or g["is_ccg"] or g["season_type"] != "regular":
                continue
            played.setdefault(g["week"], []).append(g)
        for after in WEEKS:
            before, _ = fpi_snapshot(y, 2, after)
            later, _ = fpi_snapshot(y, 2, after + 1)
            for g in played.get(after + 1, []):
                h, a = g["home_idx"], g["away_idx"]
                rh, ra = before.get(ids.get(h)), before.get(ids.get(a))
                if rh is None or ra is None:
                    continue
                edge = rh - ra + (0.0 if g["neutral"] else p.hfa)
                surprise = (g["home_points"] - g["away_points"]) - edge
                for idx, e in ((h, surprise), (a, -surprise)):
                    tid = ids.get(idx)
                    if tid is not None and later.get(tid) is not None:
                        rows.setdefault(g["week"], []).append(
                            (e, later[tid] - before[tid]))
    out = []
    for week in sorted(rows):
        arr = np.array(rows[week], float)
        e, move = arr[:, 0], arr[:, 1]
        out.append({"week": week, "n": len(arr),
                    "share": float((e * move).sum() / (e ** 2).sum()),
                    "left": float(np.std(move - (e * move).sum()
                                          / (e ** 2).sum() * e))})
    return out


def fit(rows: list[dict]) -> tuple[float, float]:
    """(gain, offset) in gain / (week + offset)."""
    w = np.array([r["week"] for r in rows], float)
    share = np.array([r["share"] for r in rows])
    se = share / np.sqrt([r["n"] for r in rows])
    f = least_squares(lambda q: (q[0] / (w + q[1]) - share) / se, [1.2, 5.0])
    return float(f.x[0]), float(f.x[1])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
    args = ap.parse_args(argv)

    rows = measure(args.years)
    gain, offset = fit(rows)
    p = ModelParams()
    print(f"{'week':>5}{'teams':>7}{'share':>9}{'fit':>8}{'unexplained':>13}")
    for r in rows:
        print(f"{r['week']:>5}{r['n']:>7}{r['share']:>9.4f}"
              f"{gain / (r['week'] + offset):>8.4f}{r['left']:>13.2f}")
    print(f"\ngain   {gain:6.3f}   (in use: {p.carry_gain})")
    print(f"offset {offset:6.2f}   (in use: {p.carry_offset})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
