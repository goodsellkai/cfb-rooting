"""Derive the outcome-model parameters from closing betting lines.

Run with ``python -m cfbroot.calibration`` to reproduce the numbers baked into
:class:`cfbroot.config.ModelParams`.

The point of using betting lines is that a closing spread is a genuine
point-in-time forecast: it was published before the game and cannot have
absorbed the result. Ratings cannot make that claim -- FPI is restated after
each week -- which is why calibrating on "current ratings vs already-played
games" understates the error and why this runs offline instead.

Three quantities come out of it:

``sigma``   Results scatter around a closing line with an SD of about 15.3
            points; that is the irreducible noise in a college football game.
            FPI's own projections differ from the line by about 5.5 points of
            SD, so a forecast built on FPI carries both, and the two combine in
            quadrature.

``hfa``     The coefficient on a home-site indicator when the closing spread is
            regressed on the rating gap. Home field is identified by which
            games are at neutral sites, not by the rating gap, so it is the one
            parameter the rating lookahead leaves alone.

``slope``   1.0, because FPI is defined in points against an average opponent.
            The check here is that the two contaminated estimates bracket it:
            regressing the market spread on an inflated gap pulls the slope
            below 1, regressing realised margin on it pulls above, and the
            truth sits between.
"""

from __future__ import annotations

import argparse

import numpy as np

from .config import ModelParams
from .data.loader import load_season


def _pull(years: list[int]) -> np.ndarray:
    """Rows of (margin, spread, rating gap, is_home) for FBS-vs-FBS games."""
    import cfbd

    from .config import api_key

    cfg = cfbd.Configuration(access_token=api_key())
    rows: list[tuple[float, float, float, float]] = []
    for year in years:
        state = load_season(year)
        by_id = {g["game_id"]: g for g in state.games}
        with cfbd.ApiClient(cfg) as client:
            lines = cfbd.BettingApi(client).get_lines(
                year=year, season_type=cfbd.SeasonType.REGULAR)
        for game in lines:
            spread = next((float(l.spread) for l in (game.lines or [])
                           if l.spread is not None), None)
            if spread is None or game.home_score is None or game.away_score is None:
                continue
            row = by_id.get(game.id)
            if row is None or row["is_ccg"]:
                continue
            h, a = row["home_idx"], row["away_idx"]
            if not (state.teams[h].is_fbs and state.teams[a].is_fbs):
                continue
            rows.append((
                float(game.home_score - game.away_score),
                -spread,                       # CFBD quotes from the home side
                state.teams[h].rating - state.teams[a].rating,
                0.0 if row["neutral"] else 1.0,
            ))
    return np.array(rows, dtype=np.float64)


def calibrate(years: list[int]) -> dict:
    data = _pull(years)
    if data.size == 0:
        raise RuntimeError("no games with both a closing spread and a final score")
    margin, spread, gap, home = data[:, 0], data[:, 1], data[:, 2], data[:, 3]

    design = np.column_stack([gap, home])
    market, *_ = np.linalg.lstsq(design, spread, rcond=None)
    realised, *_ = np.linalg.lstsq(design, margin, rcond=None)

    game_noise = float((margin - spread).std(ddof=1))
    rating_error = float((design @ market - spread).std(ddof=1))

    return {
        "n": int(data.shape[0]),
        "years": years,
        "game_noise": game_noise,
        "rating_error": rating_error,
        "sigma": float(np.hypot(game_noise, rating_error)),
        "hfa_market": float(market[1]),
        "hfa_realised": float(realised[1]),
        "slope_market": float(market[0]),
        "slope_realised": float(realised[0]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cfbroot.calibration", description=__doc__)
    ap.add_argument("--years", type=int, nargs="+", default=[2025, 2024])
    args = ap.parse_args(argv)

    r = calibrate(args.years)
    p = ModelParams()
    print(f"{r['n']:,} FBS-vs-FBS games with a closing spread, {r['years']}\n")
    print(f"  SD(margin - closing spread)   {r['game_noise']:6.2f}   irreducible game noise")
    print(f"  SD(FPI projection - spread)   {r['rating_error']:6.2f}   FPI vs the market")
    print(f"  combined in quadrature        {r['sigma']:6.2f}   -> sigma "
          f"(in use: {p.sigma})")
    print()
    print(f"  home field, market spreads    {r['hfa_market']:6.2f}   -> hfa "
          f"(in use: {p.hfa})")
    print(f"  home field, realised margins  {r['hfa_realised']:6.2f}")
    print()
    print(f"  slope, market on gap          {r['slope_market']:6.2f}   biased low")
    print(f"  slope, margin on gap          {r['slope_realised']:6.2f}   biased high")
    print(f"  FPI is points-scaled          {1.00:6.2f}   -> rating_scale "
          f"(in use: {p.rating_scale})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
