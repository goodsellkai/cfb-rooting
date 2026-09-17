"""Runs the kernel in batches and adds up the results.

Unplayed games are simulated independently, so splitting one set of
simulations by the result of a game gives that game's effect. No game needs its
own run, and since both halves share the other games' draws, the difference
between them is less noisy than two separate runs.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..config import METRIC_NAMES, ModelParams, SimConfig
from ..data.season import SeasonState
from . import kernels
from .kernels import N_METRICS

MAX_WINS = 20


@dataclass
class SimResults:
    """Summary counts from a run. The raw simulations are not kept."""

    n_sims: int
    focus_idx: int
    focus_name: str
    metric_names: list[str]

    metric_counts: np.ndarray        # (M,) times the focus team achieved it
    cond_counts: np.ndarray          # (G, M) achieved AND home team won game G
    n_home_wins: np.ndarray          # (G,) times the home team won game G
    cond_wins_sum: np.ndarray        # (G,) focus wins summed over home-win sims
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


def _thread_count() -> int:
    try:
        import numba
        return max(int(numba.get_num_threads()), 1)
    except Exception:  # noqa: BLE001
        return 1


def run(state: SeasonState, focus: str | int, cfg: SimConfig | None = None,
        progress: Callable[[int, int], None] | None = None) -> SimResults:
    """Simulate the rest of the season ``cfg.n_sims`` times."""
    cfg = cfg or SimConfig()

    if isinstance(focus, int):
        focus_idx = focus
    else:
        team = state.team_by_name(focus)
        if team is None:
            raise ValueError(f"unknown team: {focus!r}")
        focus_idx = team.idx
    if not state.teams[focus_idx].is_fbs:
        raise ValueError(f"{state.teams[focus_idx].school} is not an FBS team")

    ki = state.kernel_inputs()
    p = state.params
    mp = state.massey
    m = ki.massey
    fbs_idx = np.flatnonzero(ki.is_fbs).astype(np.int32)
    n_fbs = fbs_idx.size
    n_rem = int(ki.remaining_idx.size)
    n_teams = ki.n_teams

    n_sims = int(cfg.n_sims)
    batch = int(min(cfg.batch_size, n_sims))
    n_threads = _thread_count()
    n_chunks = max(n_threads * 2, 1)
    sims_per_chunk = max(1, math.ceil(batch / n_chunks))
    n_chunks = max(1, math.ceil(batch / sims_per_chunk))

    metric_counts = np.zeros(N_METRICS, dtype=np.int64)
    cond_counts = np.zeros((n_rem, N_METRICS), dtype=np.int64)
    n_home_wins = np.zeros(n_rem, dtype=np.int64)
    cond_wins_sum = np.zeros(n_rem, dtype=np.float64)
    wins_hist = np.zeros(MAX_WINS + 1, dtype=np.int64)
    wins_made_playoff = np.zeros(MAX_WINS + 1, dtype=np.int64)
    seed_hist = np.zeros(13, dtype=np.int64)
    rank_hist = np.zeros(n_fbs + 1, dtype=np.int64)
    team_counts = np.zeros((n_teams, N_METRICS), dtype=np.int64)

    out_hw = np.zeros((batch, n_rem), dtype=np.uint8)
    out_metrics = np.zeros((batch, N_METRICS), dtype=np.uint8)
    out_wins = np.zeros(batch, dtype=np.int32)
    out_losses = np.zeros(batch, dtype=np.int32)
    out_seed = np.zeros(batch, dtype=np.int32)
    out_rank = np.zeros(batch, dtype=np.int32)
    chunk_counts = np.zeros((n_chunks, n_teams, N_METRICS), dtype=np.int64)

    t0 = time.perf_counter()
    done = 0
    b = 0
    while done < n_sims:
        this = min(batch, n_sims - done)
        chunk_counts[:] = 0
        kernels.simulate_batch(
            this, sims_per_chunk, int(cfg.seed) + b * 104729,
            ki.rating, ki.conf_id, ki.div_id, ki.is_fbs,
            ki.g_home, ki.g_away, ki.g_neutral, ki.g_conf, ki.g_status,
            ki.g_hpts, ki.g_apts, ki.remaining_idx,
            ki.conf_teams_ptr, ki.conf_teams, ki.conf_games_ptr, ki.conf_games,
            ki.conf_has_ccg, ki.conf_crowns, ki.conf_n_div, ki.conf_is_power,
            ki.conf_fixed_ccg,
            fbs_idx,
            m.node, m.hinv, m.prior, m.prec,
            m.team_games_ptr, m.team_games, m.team_at_home,
            p.hfa, p.sigma, p.rating_sd, p.rating_scale,
            p.total_base, p.total_slope, p.total_sd,
            mp.gof_k, mp.gof_c, mp.gof_q, mp.mov_weight, mp.mov_flat,
            p.massey_iters, mp.correction_abs, p.correction_passes,
            p.committee_sd, p.title_jump_margin, p.h2h_depth,
            p.n_byes, focus_idx,
            out_hw, out_metrics, out_wins, out_losses, out_seed, out_rank,
            chunk_counts)

        hw = out_hw[:this]
        mt = out_metrics[:this]
        metric_counts += mt.sum(axis=0, dtype=np.int64)
        n_home_wins += hw.sum(axis=0, dtype=np.int64)
        if n_rem:
            # (G x S) @ (S x M): every game-by-metric joint count in one GEMM
            cond_counts += (hw.T.astype(np.float32) @ mt.astype(np.float32)
                            ).astype(np.int64)
            cond_wins_sum += hw.T.astype(np.float32) @ out_wins[:this].astype(np.float32)
        clipped = np.clip(out_wins[:this], 0, MAX_WINS)
        wins_hist += np.bincount(clipped, minlength=MAX_WINS + 1).astype(np.int64)
        made = mt[:, kernels.M_PLAYOFF].astype(bool)
        wins_made_playoff += np.bincount(clipped[made],
                                         minlength=MAX_WINS + 1).astype(np.int64)
        seed_hist += np.bincount(np.clip(out_seed[:this], 0, 12),
                                 minlength=13).astype(np.int64)
        rank_hist += np.bincount(np.clip(out_rank[:this], 0, n_fbs),
                                 minlength=n_fbs + 1).astype(np.int64)
        team_counts += chunk_counts.sum(axis=0)

        done += this
        b += 1
        if progress:
            progress(done, n_sims)

    return SimResults(
        n_sims=n_sims, focus_idx=focus_idx,
        focus_name=state.teams[focus_idx].school,
        metric_names=list(METRIC_NAMES),
        metric_counts=metric_counts, cond_counts=cond_counts,
        n_home_wins=n_home_wins, cond_wins_sum=cond_wins_sum,
        wins_hist=wins_hist, wins_made_playoff=wins_made_playoff,
        seed_hist=seed_hist, rank_hist=rank_hist,
        team_counts=team_counts,
        remaining_sim_idx=ki.remaining_idx.copy(),
        game_keys=[g for g in state.games
                   if not g["is_ccg"] and g["status"] == 0],
        elapsed=time.perf_counter() - t0,
        params=p, used_numba=kernels.HAVE_NUMBA)
