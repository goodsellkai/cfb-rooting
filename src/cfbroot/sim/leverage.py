"""Turns simulation counts into the rooting guide."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import DEFAULT_METRICS, METRIC_LABELS, METRIC_NAMES
from ..data.season import SeasonState
from ..stats import (Estimate, benjamini_hochberg, mc_stderr, newcombe_diff_ci,
                     prop_diff_pvalue, sims_for_resolution, wilson_ci)
from .engine import SimResults


@dataclass
class MetricSwing:
    """How one metric moves depending on who wins one game."""

    metric: str
    label: str
    p_if_home: float
    p_if_away: float
    delta: float                 # p_if_home - p_if_away
    lo: float
    hi: float
    pvalue: float
    # FDR results at two scopes: within the game's week, and across all
    # remaining games.
    q_week: float = 1.0
    q_all: float = 1.0
    sig_week: bool = False
    sig_all: bool = False
    qvalue: float = 1.0          # whichever scope the caller asked for
    significant: bool = False
    n_home: int = 0
    n_away: int = 0

    @property
    def root_for_home(self) -> bool:
        """Whether the home team winning helps. See ``confidence`` for how sure."""
        if not np.isfinite(self.delta):
            return self.p_if_home >= self.p_if_away
        return self.delta >= 0

    @property
    def confidence(self) -> str:
        """How reliable the direction is.

        ``clear``    significant after FDR control
        ``leaning``  the estimate favors one side, but the interval includes zero
        ``thin``     too few simulations on one side
        """
        if not self.reliable:
            return "thin"
        return "clear" if self.significant else "leaning"

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)

    @property
    def conservative(self) -> float:
        """The confidence bound closest to zero, or zero if the interval crosses it.

        Used for ranking so noisy games don't rise to the top.
        """
        if not (np.isfinite(self.lo) and np.isfinite(self.hi)):
            return 0.0
        if self.lo <= 0.0 <= self.hi:
            return 0.0
        return min(abs(self.lo), abs(self.hi))

    @property
    def min_arm(self) -> int:
        return min(self.n_home, self.n_away)

    @property
    def reliable(self) -> bool:
        """Whether the smaller side has enough simulations (250+) to trust."""
        return self.min_arm >= 250


@dataclass
class GameLeverage:
    game: dict
    home: str
    away: str
    home_idx: int
    away_idx: int
    week: int
    start_date: str | None
    neutral: bool
    p_home_win: float
    is_own_game: bool
    swings: dict[str, MetricSwing] = field(default_factory=dict)
    primary: str = ""

    @property
    def primary_swing(self) -> MetricSwing:
        return self.swings[self.primary]

    @property
    def root_for(self) -> str:
        """Always names a side. ``confidence`` says how firm that call is."""
        s = self.primary_swing
        return self.home if s.root_for_home else self.away

    @property
    def root_for_side(self) -> str:
        return "home" if self.primary_swing.root_for_home else "away"

    @property
    def confidence(self) -> str:
        return self.primary_swing.confidence

    @property
    def magnitude(self) -> float:
        return self.primary_swing.abs_delta

    @property
    def defensible(self) -> float:
        return self.primary_swing.conservative

    @property
    def reliable(self) -> bool:
        return self.primary_swing.reliable

    def as_dict(self) -> dict:
        return {
            "game_id": self.game.get("game_id"),
            "home": self.home, "away": self.away,
            "home_idx": self.home_idx, "away_idx": self.away_idx,
            "week": self.week, "start_date": self.start_date,
            "neutral": self.neutral,
            "p_home_win": self.p_home_win,
            "is_own_game": self.is_own_game,
            "root_for": self.root_for,
            "root_for_side": self.root_for_side,
            "confidence": self.confidence,
            "magnitude": self.magnitude,
            "defensible": self.defensible,
            "reliable": self.reliable,
            "primary": self.primary,
            # Metric name is the key and labels are on the client, so neither is repeated.
            "swings": {k: {"p_if_home": v.p_if_home, "p_if_away": v.p_if_away,
                           "delta": v.delta, "lo": v.lo, "hi": v.hi,
                           "q_week": v.q_week, "q_all": v.q_all,
                           "sig_week": v.sig_week, "sig_all": v.sig_all,
                           "n_home": v.n_home, "n_away": v.n_away,
                           "min_arm": v.min_arm, "reliable": v.reliable,
                           "home": v.root_for_home}
                       for k, v in self.swings.items()},
        }


@dataclass
class Guide:
    team: str
    n_sims: int
    week: int | None            # None means "every remaining game"
    headline: dict[str, Estimate]
    expected_wins: float
    wins_distribution: list[float]
    seed_distribution: list[float]
    games: list[GameLeverage]
    own_games: list[GameLeverage]
    primary: str
    alpha: float
    fdr_q: float
    n_tests: int
    resolution: float
    elapsed: float
    used_numba: bool
    notes: list[str] = field(default_factory=list)


def _all_swings(res: SimResults, metrics: list[str], alpha: float) -> dict:
    """Every game against every metric, in one pass.

    Done one at a time this called into scipy six thousand times for what is a
    handful of array operations, and took most of the time spent building a
    guide.
    """
    mi = np.array([res.metric_names.index(m) for m in metrics])
    n1 = res.n_home_wins.astype(np.float64)[:, None]        # (G, 1)
    n2 = float(res.n_sims) - n1
    k1 = res.cond_counts[:, mi].astype(np.float64)          # (G, M)
    k2 = res.metric_counts[mi].astype(np.float64)[None, :] - k1
    ok = (n1 > 0) & (n2 > 0)

    with np.errstate(invalid="ignore", divide="ignore"):
        p1 = np.where(n1 > 0, k1 / n1, np.nan)
        p2 = np.where(n2 > 0, k2 / n2, np.nan)
        delta, lo, hi = newcombe_diff_ci(k1, n1, k2, n2, alpha)
        pv = prop_diff_pvalue(k1, n1, k2, n2)

    nan = np.full_like(p1, np.nan)
    return {"p1": p1, "p2": p2,
            "delta": np.where(ok, delta, nan),
            "lo": np.where(ok, lo, nan), "hi": np.where(ok, hi, nan),
            "pvalue": np.where(ok, pv, 1.0),
            "n1": np.broadcast_to(n1, p1.shape).astype(np.int64),
            "n2": np.broadcast_to(n2, p1.shape).astype(np.int64)}


def build_guide(state: SeasonState, res: SimResults, *,
                metrics: list[str] | None = None,
                primary: str | None = None,
                week: int | None = None,
                alpha: float = 0.05,
                fdr_q: float = 0.05) -> Guide:
    """Rank games by how much they move the focus team's season.

    ``week=None`` scores every remaining game. Passing a week limits it to that slate.
    """
    # Compute every metric so the client can switch without re-running.
    metrics = list(metrics or METRIC_NAMES)
    primary = primary or DEFAULT_METRICS[0]
    if primary not in metrics:
        metrics.insert(0, primary)
    m_idx = {m: res.metric_names.index(m) for m in metrics}

    focus_idx = res.focus_idx
    # Score every remaining game. Week is a filter applied afterwards.
    entries_all: list[GameLeverage] = []
    sw = _all_swings(res, metrics, alpha)
    for gi in range(len(res.game_keys)):
        g = res.game_keys[gi]
        lev = GameLeverage(
            game=g, home=g["home"], away=g["away"],
            home_idx=g["home_idx"], away_idx=g["away_idx"],
            week=g["week"], start_date=g.get("start_date"),
            neutral=g["neutral"], p_home_win=g.get("pwin_home", float("nan")),
            is_own_game=(g["home_idx"] == focus_idx or g["away_idx"] == focus_idx),
            primary=primary)
        for mj, m in enumerate(metrics):
            lev.swings[m] = MetricSwing(
                metric=m, label=METRIC_LABELS.get(m, m),
                p_if_home=float(sw["p1"][gi, mj]),
                p_if_away=float(sw["p2"][gi, mj]),
                delta=float(sw["delta"][gi, mj]),
                lo=float(sw["lo"][gi, mj]), hi=float(sw["hi"][gi, mj]),
                pvalue=float(sw["pvalue"][gi, mj]),
                n_home=int(sw["n1"][gi, mj]), n_away=int(sw["n2"][gi, mj]))
        entries_all.append(lev)

    # FDR control is applied within each metric.
    def _apply(group: list[GameLeverage], metric: str, attr_q: str, attr_sig: str) -> int:
        pvals = np.array([e.swings[metric].pvalue for e in group], dtype=np.float64)
        if not pvals.size:
            return 0
        rejected, qvals = benjamini_hochberg(pvals, fdr_q)
        for e, r, q in zip(group, rejected, qvals):
            setattr(e.swings[metric], attr_q, float(q))
            setattr(e.swings[metric], attr_sig, bool(r))
        return int(pvals.size)

    by_week: dict[int, list[GameLeverage]] = {}
    for e in entries_all:
        by_week.setdefault(e.week, []).append(e)

    for m in metrics:
        _apply(entries_all, m, "q_all", "sig_all")
        for slate in by_week.values():
            _apply(slate, m, "q_week", "sig_week")

    # Point the generic fields at whichever scope this caller asked for.
    scope_all = week is None
    for e in entries_all:
        for m in metrics:
            sw = e.swings[m]
            sw.qvalue = sw.q_all if scope_all else sw.q_week
            sw.significant = sw.sig_all if scope_all else sw.sig_week

    entries = entries_all if week is None else by_week.get(week, [])
    n_tests = len(entries)

    own = [e for e in entries if e.is_own_game]
    others = [e for e in entries if not e.is_own_game]
    def sort_key(e: GameLeverage):
        mag = e.magnitude if np.isfinite(e.magnitude) else 0.0
        sw = e.primary_swing
        return (not sw.significant, not sw.reliable, -e.defensible, -mag)

    others.sort(key=sort_key)
    own.sort(key=sort_key)

    headline = {}
    for m in res.metric_names:
        k = int(res.metric_counts[res.metric_names.index(m)])
        headline[m] = Estimate.from_counts(k, res.n_sims, alpha)

    n = res.n_sims
    wins_dist = (res.wins_hist / max(n, 1)).tolist()
    seed_dist = (res.seed_hist / max(n, 1)).tolist()

    p_primary = headline[primary].p
    resolution = float(2.0 * 1.959963985 * np.sqrt(
        2.0 * p_primary * (1 - p_primary) / max(n, 1))) if np.isfinite(p_primary) else float("nan")

    notes: list[str] = []
    if np.isfinite(p_primary) and (p_primary < 0.02 or p_primary > 0.98):
        notes.append(
            f"{res.focus_name}'s {METRIC_LABELS.get(primary, primary).lower()} "
            f"chance is {p_primary:.1%}, so this week's games barely move it. "
            f"Try a different metric.")
    if res.n_sims < 50_000:
        notes.append(
            f"With {res.n_sims:,} simulations, swings under about "
            f"{resolution:.1%} are noise. A 1 point swing needs about "
            f"{sims_for_resolution(0.01, p_primary):,.0f} simulations.")
    if not res.used_numba:
        notes.append("numba is not installed, so the kernel ran in pure Python, "
                     "which is orders of magnitude slower.")

    return Guide(
        team=res.focus_name, n_sims=res.n_sims, week=week,
        headline=headline, expected_wins=res.wins_mean,
        wins_distribution=wins_dist, seed_distribution=seed_dist,
        games=others, own_games=own, primary=primary,
        alpha=alpha, fdr_q=fdr_q, n_tests=int(n_tests),
        resolution=resolution, elapsed=res.elapsed, used_numba=res.used_numba,
        notes=notes + list(state.notes))


def league_all(state: SeasonState, res: SimResults, alpha: float = 0.05) -> list[dict]:
    """Every FBS team's odds for every metric.

    Every interval in one call. Asking for them one at a time was most of the
    cost of answering a request, and none of it depends on which team was asked
    about.
    """
    fbs = state.fbs_teams
    idx = np.array([t.idx for t in fbs], dtype=np.int64)
    counts = res.team_counts[idx]                      # (teams, metrics)
    probs = counts / max(res.n_sims, 1)
    lo, hi = wilson_ci(counts, res.n_sims, alpha)
    names = res.metric_names
    return [{"idx": t.idx, "team": t.school, "conference": t.conference,
             "rating": t.rating,
             "p": dict(zip(names, probs[i].tolist())),
             "lo": dict(zip(names, np.asarray(lo)[i].tolist())),
             "hi": dict(zip(names, np.asarray(hi)[i].tolist())),
             "espn_playoff_prob": getattr(t, "espn_playoff_prob", None)}
            for i, t in enumerate(fbs)]


def league_table(state: SeasonState, res: SimResults, metric: str = "make_playoff",
                 limit: int = 25, alpha: float = 0.05) -> list[dict]:
    """League-wide odds for one metric, best first."""
    fbs = state.fbs_teams
    idx = np.array([t.idx for t in fbs], dtype=np.int64)
    counts = res.team_counts[idx, res.metric_names.index(metric)]
    probs = counts / max(res.n_sims, 1)
    lo, hi = wilson_ci(counts, res.n_sims, alpha)
    rows = [{"team": t.school, "conference": t.conference, "rating": t.rating,
             "p": float(probs[i]), "lo": float(np.asarray(lo)[i]),
             "hi": float(np.asarray(hi)[i]),
             "se": float(mc_stderr(float(probs[i]), res.n_sims))}
            for i, t in enumerate(fbs)]
    rows.sort(key=lambda r: -r["p"])
    return rows[:limit]
