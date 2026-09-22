"""Measure how wrong FPI is about a team, and how noisy one game is.

Run ``python -m cfbroot.spread`` to reproduce the values in ModelParams:
sigma, and the curve rating_sd_start, rating_sd_floor and rating_sd_weeks.

ESPN keeps every week's FPI. For each snapshot, every later regular-season
game between two FBS teams gets a residual: the margin minus the margin FPI
predicted. Two of one team's residuals share only that team's rating error,
since the opponents' errors and the game noise are independent, so the mean
product of a team's residuals across games is the variance of the error it
carries for the rest of the season. What is left of a game's squared residual
after both teams' errors is the game noise.

Measured over 2023-25, the team error is 7.1 points before the season and
levels off near 5 by mid-October, while the game noise holds at 13.5 to 14 all
year. The curve

    rating_sd(w)^2 = floor^2 + (start^2 - floor^2) * exp(-w / weeks)

with w the weeks of results FPI has seen, fitted on two seasons and tested on
the third, beat a constant in every case tried.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request

import numpy as np
from scipy.optimize import least_squares

from .config import ModelParams
from .data import cache
from .data.loader import load_season

URL = ("https://sports.core.api.espn.com/v2/sports/football/leagues/"
       "college-football/seasons/{y}/types/{t}/weeks/{w}/powerindex"
       "?limit=200&page={p}")
# (weeks of results behind the FPI, season type, ESPN week). ESPN's regular
# season week 1 snapshot was overwritten with the final ratings, so the
# preseason one stands in for "no results yet".
SNAPSHOTS = [(0, 1, 1)] + [(w, 2, w) for w in range(2, 13)]


def fpi_snapshot(year: int, season_type: int, week: int) -> tuple[dict, str | None]:
    """ESPN's FPI as published for one week: {espn id: fpi}, and its date."""
    def go():
        rows, last, page = {}, None, 1
        while True:
            req = urllib.request.Request(
                URL.format(y=year, t=season_type, w=week, p=page),
                headers={"User-Agent": "Mozilla/5.0 (compatible; cfbroot/0.1)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.load(resp)
            for it in d["items"]:
                last = last or it.get("lastUpdated")
                tid = re.search(r"/teams/(\d+)", it["team"]["$ref"]).group(1)
                v = {x["name"]: x.get("value") for x in it["predictives"]}.get("fpi")
                if v is not None:
                    rows[tid] = float(v)
            if page >= d.get("pageCount", 1):
                return {"rows": rows, "last": last}
            page += 1

    blob = cache.get_or_fetch("espn_fpi_week", {"year": year, "type": season_type,
                                                "week": week}, 365 * 86400, go)
    return {int(k): v for k, v in blob["rows"].items()}, blob["last"]


def _tau2(teams: list[np.ndarray]) -> float:
    """Mean product of a team's residuals across different games."""
    pairs = sum(len(v) * (len(v) - 1) for v in teams)
    return sum(v.sum() ** 2 - (v ** 2).sum() for v in teams) / pairs


def measure(years: list[int], params: ModelParams | None = None) -> list[dict]:
    """Team error and total residual variance per snapshot, pooled over years."""
    p = params or ModelParams()
    states = {y: load_season(y) for y in years}
    out = []
    for after, season_type, week in SNAPSHOTS:
        per_team: dict = {}
        sq = []
        for y, st in states.items():
            fpi, _ = fpi_snapshot(y, season_type, week)
            by = {t.idx: fpi.get(t.team_id) for t in st.fbs_teams}
            for g in st.games:
                if g["status"] == 0 or g["is_ccg"] or g["week"] <= after:
                    continue
                h, a = g["home_idx"], g["away_idx"]
                if by.get(h) is None or by.get(a) is None:
                    continue
                e = (g["home_points"] - g["away_points"]) - (
                    by[h] - by[a] + (0.0 if g["neutral"] else p.hfa))
                sq.append(e * e)
                per_team.setdefault((y, h), []).append(e)
                per_team.setdefault((y, a), []).append(-e)
        teams = [np.array(v) for v in per_team.values() if len(v) >= 2]
        if not teams:
            continue
        tau2 = _tau2(teams)
        # Its uncertainty, by resampling teams, so the curve can lean on the
        # weeks that are measured well.
        rng = np.random.default_rng(1)
        boot = [_tau2([teams[i] for i in rng.integers(0, len(teams), len(teams))])
                for _ in range(500)]
        out.append({"after": after, "games": len(sq), "tau2": float(tau2),
                    "tau2_se": float(np.std(boot)), "total2": float(np.mean(sq))})
    return out


def fit(rows: list[dict]) -> tuple[float, float, float, float]:
    """(sigma, start, floor, weeks) from measure()'s rows."""
    w = np.array([r["after"] for r in rows], float)
    t2 = np.array([r["tau2"] for r in rows])
    n = np.array([r["games"] for r in rows], float)
    sigma = float(np.sqrt(np.average(
        [r["total2"] - 2 * r["tau2"] for r in rows], weights=n)))

    def curve(q, w):
        return q[1] ** 2 + (q[0] ** 2 - q[1] ** 2) * np.exp(-w / q[2])

    se = np.array([r["tau2_se"] for r in rows])
    f = least_squares(lambda q: (curve(q, w) - t2) / se,
                      [7.0, 5.0, 4.0], bounds=([2, 2, 0.5], [12, 10, 30]))
    start, floor, weeks = (float(x) for x in f.x)
    return sigma, start, floor, weeks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", type=int, nargs="+", default=[2023, 2024, 2025])
    args = ap.parse_args(argv)
    rows = measure(args.years)
    sigma, start, floor, weeks = fit(rows)
    p = ModelParams()
    print(f"{'after week':>10}{'games':>7}{'team error':>12}{'curve':>8}{'total':>8}")
    for r in rows:
        c = np.sqrt(floor ** 2 + (start ** 2 - floor ** 2) * np.exp(-r["after"] / weeks))
        print(f"{r['after']:>10}{r['games']:>7}{np.sqrt(max(r['tau2'], 0)):>12.2f}"
              f"{c:>8.2f}{np.sqrt(r['total2']):>8.2f}")
    print(f"\ngame noise, sigma          {sigma:6.2f}   (in use: {p.sigma})")
    print(f"team error before season   {start:6.2f}   (in use: {p.rating_sd_start})")
    print(f"team error, floor          {floor:6.2f}   (in use: {p.rating_sd_floor})")
    print(f"weeks to close 63% of gap  {weeks:6.2f}   (in use: {p.rating_sd_weeks})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
