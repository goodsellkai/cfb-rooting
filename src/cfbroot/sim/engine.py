"""Runs the kernel in batches and adds up the results.

Which team you are asking about never changed how a season played out, so the
kernel records every team and one set of simulations answers for all of them.
That is what lets the app simulate once and then switch teams instantly.

Unplayed games are simulated independently, so splitting one set of simulations
by the result of a game gives that game's effect. No game needs its own run,
and since both halves share the other games' draws, the difference between them
is less noisy than two separate runs.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..config import METRIC_NAMES, ModelParams, SimConfig
from ..data.season import SeasonState
from ..selection import playoff_format
from . import kernels
from .kernels import N_METRICS

MAX_WINS = 20
MAX_SEED = 12


@dataclass
class SimResults:
    """One team's view of a run. The raw simulations are not kept."""

    n_sims: int
    focus_idx: int
    focus_name: str
    metric_names: list[str]

    metric_counts: np.ndarray        # (M,) times the focus team achieved it
    cond_counts: np.ndarray          # (G, M) achieved AND home team won game G
    n_home_wins: np.ndarray          # (G,) times the home team won game G
    wins_hist: np.ndarray            # (MAX_WINS+1,) focus regular-season wins
    wins_made_playoff: np.ndarray    # (MAX_WINS+1,) of those, how many got a bid
    seed_hist: np.ndarray            # (13,) focus playoff seed, 0 = missed
    rank_hist: np.ndarray            # (n_fbs+1,) focus final committee rank
    team_counts: np.ndarray          # (n_teams, M) league-wide tallies

    remaining_sim_idx: np.ndarray    # kernel game index for each column G
    game_keys: list                  # the SeasonState game dict per column G

    elapsed: float = 0.0
    params: ModelParams | None = None
    used_numba: bool = True

    @property
    def wins_mean(self) -> float:
        w = np.arange(self.wins_hist.size)
        return float((w * self.wins_hist).sum() / max(self.n_sims, 1))

    def playoff_rate_by_wins(self) -> dict[int, tuple[float, int]]:
        """P(playoff | win total), with the simulation count behind each."""
        out = {}
        for w in range(self.wins_hist.size):
            n = int(self.wins_hist[w])
            if n:
                out[w] = (float(self.wins_made_playoff[w]) / n, n)
        return out

    def metric(self, name: str) -> int:
        return self.metric_names.index(name)

    def probability(self, name: str) -> float:
        return float(self.metric_counts[self.metric(name)] / max(self.n_sims, 1))


@dataclass
class LeagueResults:
    """Every team's results from one set of simulations."""

    n_sims: int
    metric_names: list[str]
    fbs_idx: np.ndarray              # team index behind each column
    n_teams: int

    team_counts: np.ndarray          # (n_fbs, M)
    cond_counts: np.ndarray          # (G, M, n_fbs)
    n_home_wins: np.ndarray          # (G,)
    wins_hist: np.ndarray            # (n_fbs, MAX_WINS+1)
    wins_made_playoff: np.ndarray    # (n_fbs, MAX_WINS+1)
    seed_hist: np.ndarray            # (n_fbs, MAX_SEED+1)
    rank_hist: np.ndarray            # (n_fbs, n_fbs+1)

    remaining_sim_idx: np.ndarray
    game_keys: list
    names: list[str]                 # school per column

    elapsed: float = 0.0
    params: ModelParams | None = None
    used_numba: bool = True

    def slot(self, team_idx: int) -> int:
        hit = np.flatnonzero(self.fbs_idx == team_idx)
        if not hit.size:
            raise ValueError(f"team index {team_idx} is not an FBS team here")
        return int(hit[0])

    def for_team(self, team_idx: int) -> SimResults:
        """One team's slice, in the shape the rooting guide expects."""
        j = self.slot(team_idx)
        wide = np.zeros((self.n_teams, len(self.metric_names)), dtype=np.int64)
        wide[self.fbs_idx] = self.team_counts
        return SimResults(
            n_sims=self.n_sims, focus_idx=team_idx, focus_name=self.names[j],
            metric_names=list(self.metric_names),
            metric_counts=self.team_counts[j].copy(),
            cond_counts=np.ascontiguousarray(self.cond_counts[:, :, j]),
            n_home_wins=self.n_home_wins,
            wins_hist=self.wins_hist[j].copy(),
            wins_made_playoff=self.wins_made_playoff[j].copy(),
            seed_hist=self.seed_hist[j].copy(),
            rank_hist=self.rank_hist[j].copy(),
            team_counts=wide,
            remaining_sim_idx=self.remaining_sim_idx, game_keys=self.game_keys,
            elapsed=self.elapsed, params=self.params, used_numba=self.used_numba)


def _thread_count() -> int:
    try:
        import numba
        return max(int(numba.get_num_threads()), 1)
    except Exception:  # noqa: BLE001
        return 1


def run_league(state: SeasonState, cfg: SimConfig | None = None,
               progress: Callable[[int, int], None] | None = None
               ) -> LeagueResults:
    """Simulate the rest of the season and record every team."""
    cfg = cfg or SimConfig()
    ki = state.kernel_inputs()
    p = state.params
    mp = state.massey
    m = ki.massey

    fmt = playoff_format(state.year)
    bid_rule = kernels.BIDS_2026 if fmt.bids == "2026" else kernels.BIDS_CHAMPIONS
    fbs_idx = np.flatnonzero(ki.is_fbs).astype(np.int32)
    n_fbs = int(fbs_idx.size)
    n_rem = int(ki.remaining_idx.size)

    n_sims = int(cfg.n_sims)
    batch = int(min(cfg.batch_size, n_sims))
    n_chunks = max(_thread_count() * 2, 1)
    sims_per_chunk = max(1, math.ceil(batch / n_chunks))
    n_chunks = max(1, math.ceil(batch / sims_per_chunk))

    team_counts = np.zeros((n_fbs, N_METRICS), dtype=np.int64)
    cond_counts = np.zeros((n_rem, N_METRICS, n_fbs), dtype=np.int64)
    n_home_wins = np.zeros(n_rem, dtype=np.int64)

    out_hw = np.zeros((batch, n_rem), dtype=np.uint8)
    out_metrics = np.zeros((batch, N_METRICS, n_fbs), dtype=np.uint8)
    # Histograms are accumulated inside the kernel, one slice per thread, so
    # the per-season win, seed and rank arrays never have to exist.
    h_wins = np.zeros((n_chunks, n_fbs, MAX_WINS + 1), dtype=np.int64)
    h_made = np.zeros((n_chunks, n_fbs, MAX_WINS + 1), dtype=np.int64)
    h_seed = np.zeros((n_chunks, n_fbs, MAX_SEED + 1), dtype=np.int64)
    h_rank = np.zeros((n_chunks, n_fbs, n_fbs + 1), dtype=np.int64)

    t0 = time.perf_counter()
    done = 0
    b = 0
    while done < n_sims:
        this = min(batch, n_sims - done)
        kernels.simulate_batch(
            this, sims_per_chunk, int(cfg.seed) + b * 104729,
            ki.rating, ki.conf_id, ki.div_id, ki.is_fbs,
            ki.g_home, ki.g_away, ki.g_neutral, ki.g_conf, ki.g_status,
            ki.g_hpts, ki.g_apts, ki.remaining_idx,
            ki.conf_teams_ptr, ki.conf_teams, ki.conf_games_ptr, ki.conf_games,
            ki.conf_has_ccg, ki.conf_crowns, ki.conf_n_div, ki.conf_is_power,
            ki.conf_fixed_ccg, ki.conf_ccg_pts, ki.conf_ccg_home,
            fbs_idx,
            n_chunks, m.node, m.n_fbs, m.g_in_fit, m.g_hnode, m.g_anode,
            m.x_h, m.x_a, m.x_home, m.x_g, m.x_won, m.played,
            m.minv, m.start, m.prior, m.prec, m.tg_ptr, m.tg_ref, m.tg_home,
            m.gh_t, m.gh_logw,
            p.hfa, p.sigma, p.rating_sd, p.rating_scale,
            p.total_base, p.total_slope, p.total_sd,
            mp.gof_k, mp.gof_c, mp.gof_q, mp.mov_weight, mp.mov_flat,
            mp.prior_sd, mp.prior_games, p.fit_tol, p.fit_max_iter,
            mp.correction_abs, mp.correction_passes,
            p.committee_sd, p.title_jump_margin,
            p.worst_loss_boost, p.worst_loss_scale,
            p.best_win_boost, p.best_win_scale, p.h2h_depth,
            p.n_byes, bid_rule, fmt.champion_byes,
            out_hw, out_metrics, h_wins, h_made, h_seed, h_rank)

        hw = out_hw[:this]
        mt = out_metrics[:this]
        team_counts += mt.sum(axis=0, dtype=np.int64).T
        n_home_wins += hw.sum(axis=0, dtype=np.int64)
        if n_rem:
            # (G x S) @ (S x T) per metric: every game-by-team joint count.
            hwf = hw.T.astype(np.float32)
            for mi in range(N_METRICS):
                cond_counts[:, mi, :] += (
                    hwf @ mt[:, mi, :].astype(np.float32)).astype(np.int64)

        done += this
        b += 1
        if progress:
            progress(done, n_sims)

    return LeagueResults(
        n_sims=n_sims, metric_names=list(METRIC_NAMES), fbs_idx=fbs_idx,
        n_teams=ki.n_teams,
        team_counts=team_counts, cond_counts=cond_counts,
        n_home_wins=n_home_wins, wins_hist=h_wins.sum(axis=0),
        wins_made_playoff=h_made.sum(axis=0), seed_hist=h_seed.sum(axis=0),
        rank_hist=h_rank.sum(axis=0),
        remaining_sim_idx=ki.remaining_idx.copy(),
        game_keys=[g for g in state.games
                   if not g["is_ccg"] and g["status"] == 0],
        names=[state.teams[int(t)].school for t in fbs_idx],
        elapsed=time.perf_counter() - t0,
        params=p, used_numba=kernels.HAVE_NUMBA)


def run(state: SeasonState, focus: str | int, cfg: SimConfig | None = None,
        progress: Callable[[int, int], None] | None = None) -> SimResults:
    """Simulate the season and return one team's view.

    A convenience wrapper. Anything showing more than one team should call
    run_league() once and slice it, rather than simulating again per team.
    """
    if isinstance(focus, int):
        focus_idx = focus
    else:
        team = state.team_by_name(focus)
        if team is None:
            raise ValueError(f"unknown team: {focus!r}")
        focus_idx = team.idx
    if not state.teams[focus_idx].is_fbs:
        raise ValueError(f"{state.teams[focus_idx].school} is not an FBS team")
    return run_league(state, cfg, progress).for_team(focus_idx)
