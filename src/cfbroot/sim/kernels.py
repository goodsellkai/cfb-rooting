"""Numba kernel that simulates full seasons.

Works on flat numpy arrays so numba can compile it. Without numba the same
code runs as plain Python.

Each simulated season:
1. picks a winner for every unplayed regular season game
2. adds up overall and conference records
2b. measures schedule strength and fits the least squares rating
3. orders each conference, breaks ties, and plays the title games
4. ranks every team with the committee proxy
5. picks and seeds the 12-team playoff
6. plays the bracket

Metric columns follow config.METRIC_NAMES.
"""

from __future__ import annotations

import math

import numpy as np

from ..config import MAX_CONF_SIZE

try:  # pragma: no cover - exercised by whichever branch the machine takes
    from numba import njit, prange
    HAVE_NUMBA = True
except ImportError:  # pragma: no cover
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        def wrap(fn):
            return fn
        return wrap(args[0]) if args and callable(args[0]) else wrap

    prange = range

# Metric columns. Must match config.METRIC_NAMES.
M_WIN_CONF = 0
M_CCG = 1
M_PLAYOFF = 2
M_TOP4 = 3
M_QF = 4
M_SF = 5
M_TITLE_GAME = 6
M_NATL = 7
M_UNDEFEATED = 8
N_METRICS = 9

_SQRT2 = 1.4142135623730951


@njit(cache=True, inline="always")
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


@njit(cache=True, inline="always")
def _win_prob(ra, rb, scale, edge, sigma):
    """P(team a beats team b) given a rating edge already applied to a."""
    return _norm_cdf((scale * (ra - rb) + edge) / sigma)


@njit(cache=True)
def _order_conference(members, n_m, cwins, closses, score,
                      conf_games, cg_lo, cg_hi, g_home, g_away, winner,
                      order, pct, mark, h2h):
    """Order a conference's members best to worst into ``order``.

    Sorts by conference win percentage, then head-to-head among tied teams,
    then committee score. The last step approximates the "highest ranked team"
    tiebreaker the Big 12 and Big Ten use.
    """
    for j in range(n_m):
        t = members[j]
        played = cwins[t] + closses[t]
        pct[j] = cwins[t] / played if played > 0 else 0.0
        order[j] = j

    # insertion sort by (pct desc, score desc)
    for a in range(1, n_m):
        key = order[a]
        kp = pct[key]
        ks = score[members[key]]
        b = a - 1
        while b >= 0 and (pct[order[b]] < kp - 1e-12 or
                          (abs(pct[order[b]] - kp) <= 1e-12 and
                           score[members[order[b]]] < ks)):
            order[b + 1] = order[b]
            b -= 1
        order[b + 1] = key

    # re-sort each tied block by head-to-head, then score
    i = 0
    while i < n_m:
        j = i + 1
        while j < n_m and abs(pct[order[j]] - pct[order[i]]) <= 1e-12:
            j += 1
        gsize = j - i
        if gsize > 1:
            for k in range(i, j):
                mark[members[order[k]]] = k - i
                h2h[k - i] = 0
            for gi in range(cg_lo, cg_hi):
                g = conf_games[gi]
                mh = mark[g_home[g]]
                ma = mark[g_away[g]]
                if mh >= 0 and ma >= 0:
                    if winner[g] == 1:
                        h2h[mh] += 1
                    else:
                        h2h[ma] += 1
            for a in range(i + 1, j):
                key = order[a]
                kh = h2h[mark[members[key]]]
                ks = score[members[key]]
                b = a - 1
                while b >= i:
                    ob = order[b]
                    oh = h2h[mark[members[ob]]]
                    if oh < kh or (oh == kh and score[members[ob]] < ks):
                        order[b + 1] = order[b]
                        b -= 1
                    else:
                        break
                order[b + 1] = key
            for k in range(i, j):
                mark[members[order[k]]] = -1
        i = j


@njit(cache=True, parallel=True, nogil=True)
def simulate_batch(n_sims, sims_per_chunk, seed,
                   rating, conf_id, div_id, is_fbs, exp_elite_wins,
                   g_home, g_away, g_neutral, g_conf, g_pwin, g_status,
                   remaining_idx,
                   conf_teams_ptr, conf_teams, conf_games_ptr, conf_games,
                   conf_has_ccg, conf_crowns, conf_n_div, conf_is_power,
                   conf_fixed_ccg,
                   fbs_idx,
                   lsq_node, lsq_solve,
                   hfa, sigma, tau, scale, w_rating, k_resume, k_champ,
                   k_sos, sos_loss_ratio, k_lsq,
                   ccg_elite_expectation, n_byes,
                   focus_team,
                   out_hw, out_metrics, out_wins, out_losses, out_seed,
                   out_rank, team_counts):
    n_teams = rating.shape[0]
    n_conf = conf_has_ccg.shape[0]
    n_g = g_home.shape[0]
    n_rem = remaining_idx.shape[0]
    n_fbs = fbs_idx.shape[0]
    n_chunks = team_counts.shape[0]
    n_mcol = lsq_solve.shape[1]   # FBS nodes, the combined non-FBS node, home field
    field = 12

    for c in prange(n_chunks):
        lo = c * sims_per_chunk
        if lo >= n_sims:
            continue
        hi = min(lo + sims_per_chunk, n_sims)
        np.random.seed(seed + c * 7919 + 1)

        eff = rating.copy()          # true strength; non-FBS teams keep their rating
        winner = np.zeros(n_g, dtype=np.uint8)
        wins = np.zeros(n_teams, dtype=np.int32)
        losses = np.zeros(n_teams, dtype=np.int32)
        cwins = np.zeros(n_teams, dtype=np.int32)
        closses = np.zeros(n_teams, dtype=np.int32)
        score = np.zeros(n_teams, dtype=np.float64)
        final = np.zeros(n_teams, dtype=np.float64)
        wpct = np.zeros(n_teams, dtype=np.float64)
        sos = np.zeros(n_teams, dtype=np.float64)
        lsq = np.zeros(n_teams, dtype=np.float64)
        mb = np.zeros(n_mcol, dtype=np.float64)
        champ = np.zeros(n_teams, dtype=np.uint8)
        in_ccg = np.zeros(n_teams, dtype=np.uint8)
        ccg_played = np.zeros(n_teams, dtype=np.uint8)
        ccg_delta = np.zeros(n_teams, dtype=np.int32)
        rank_of = np.full(n_teams, 9999, dtype=np.int32)
        seed_of = np.zeros(n_teams, dtype=np.int32)
        order = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
        pct = np.zeros(MAX_CONF_SIZE, dtype=np.float64)
        h2h = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
        mark = np.full(n_teams, -1, dtype=np.int32)
        members = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
        sortkey = np.zeros(n_fbs, dtype=np.float64)
        seeds = np.zeros(field, dtype=np.int32)
        reached = np.zeros(field, dtype=np.uint8)
        picked = np.zeros(n_teams, dtype=np.uint8)

        for s in range(lo, hi):
            # 0. Rating draw. A team's true strength is its published rating
            # plus an error that lasts the whole season. The committee still
            # ranks on the published rating, so only games use eff.
            if tau > 0.0:
                for j in range(n_fbs):
                    t = fbs_idx[j]
                    eff[t] = rating[t] + tau * np.random.normal(0.0, 1.0)

            # 1. Game outcomes
            for i in range(n_g):
                st = g_status[i]
                if st != 0:
                    winner[i] = st
                    continue
                if tau > 0.0:
                    edge = 0.0 if g_neutral[i] else hfa
                    p = _norm_cdf((scale * (eff[g_home[i]] - eff[g_away[i]])
                                   + edge) / sigma)
                else:
                    p = g_pwin[i]
                winner[i] = 1 if np.random.random() < p else 2

            # 2. Records
            for t in range(n_teams):
                wins[t] = 0
                losses[t] = 0
                cwins[t] = 0
                closses[t] = 0
            for i in range(n_g):
                h = g_home[i]
                a = g_away[i]
                if winner[i] == 1:
                    w = h
                    l = a
                else:
                    w = a
                    l = h
                wins[w] += 1
                losses[l] += 1
                if g_conf[i]:
                    cwins[w] += 1
                    closses[l] += 1

            # 2b. Schedule strength from opponents' final records. Each game
            # contributes (opponent win pct - 0.5), full weight for a win and
            # sos_loss_ratio for a loss, so beating a team that finishes strong
            # helps most and losing to one that collapses hurts most.
            for t in range(n_teams):
                played = wins[t] + losses[t]
                wpct[t] = wins[t] / played if played > 0 else 0.5
                sos[t] = 0.0
            for i in range(n_g):
                h = g_home[i]
                a = g_away[i]
                if not is_fbs[h] or not is_fbs[a]:
                    continue
                qh = wpct[h] - 0.5
                qa = wpct[a] - 0.5
                if winner[i] == 1:
                    sos[h] += qa
                    sos[a] += sos_loss_ratio * qh
                else:
                    sos[h] += sos_loss_ratio * qa
                    sos[a] += qh

            # 2c. Least squares win-loss rating, a cheap stand-in for the
            # Massey fit in cfbroot.massey. The normal equations only depend on
            # the schedule, so they were inverted once when the season was
            # built and each season here is one matrix-vector product. Title
            # games are not in this loop, which puts it at the week before
            # championship weekend.
            if k_lsq != 0.0:
                for k in range(n_mcol):
                    mb[k] = 0.0
                for i in range(n_g):
                    sgn = 1.0 if winner[i] == 1 else -1.0
                    mb[lsq_node[g_home[i]]] += sgn
                    mb[lsq_node[g_away[i]]] -= sgn
                    if not g_neutral[i]:
                        mb[n_mcol - 1] += sgn
                for j in range(n_fbs):
                    acc = 0.0
                    for k in range(n_mcol):
                        acc += lsq_solve[j, k] * mb[k]
                    lsq[fbs_idx[j]] = acc

            # 3. Committee score before title games
            for j in range(n_fbs):
                t = fbs_idx[j]
                score[t] = (w_rating * rating[t]
                            + k_resume * (wins[t] - exp_elite_wins[t])
                            + k_sos * sos[t]
                            + k_lsq * lsq[t])

            # 4. Conference championships
            for t in range(n_teams):
                champ[t] = 0
                in_ccg[t] = 0
                ccg_played[t] = 0
                ccg_delta[t] = 0

            for cf in range(n_conf):
                m_lo = conf_teams_ptr[cf]
                m_hi = conf_teams_ptr[cf + 1]
                n_m = m_hi - m_lo
                if n_m < 1 or not conf_crowns[cf]:
                    continue
                for j in range(n_m):
                    members[j] = conf_teams[m_lo + j]

                _order_conference(members, n_m, cwins, closses, score,
                                  conf_games, conf_games_ptr[cf],
                                  conf_games_ptr[cf + 1],
                                  g_home, g_away, winner,
                                  order, pct, mark, h2h)

                fixed_home = conf_fixed_ccg[cf, 0]
                if fixed_home >= 0:
                    t1 = fixed_home
                    t2 = conf_fixed_ccg[cf, 1]
                    st = conf_fixed_ccg[cf, 2]
                elif not conf_has_ccg[cf] or n_m < 2:
                    champ[members[order[0]]] = 1
                    continue
                else:
                    nd = conf_n_div[cf]
                    if nd >= 2:
                        # best finisher from each of the top two divisions
                        t1 = -1
                        t2 = -1
                        d1 = -1
                        for k in range(n_m):
                            t = members[order[k]]
                            d = div_id[t]
                            if t1 < 0:
                                t1 = t
                                d1 = d
                            elif t2 < 0 and d != d1:
                                t2 = t
                                break
                        if t2 < 0:
                            t2 = members[order[1]]
                    else:
                        t1 = members[order[0]]
                        t2 = members[order[1]]
                    st = 0

                in_ccg[t1] = 1
                in_ccg[t2] = 1
                ccg_played[t1] = 1
                ccg_played[t2] = 1
                if st == 1:
                    won = t1
                    lost = t2
                elif st == 2:
                    won = t2
                    lost = t1
                else:
                    p = _win_prob(eff[t1], eff[t2], scale, 0.0, sigma)
                    if np.random.random() < p:
                        won = t1
                        lost = t2
                    else:
                        won = t2
                        lost = t1
                champ[won] = 1
                ccg_delta[won] = 1

            # 5. Final ranking
            for j in range(n_fbs):
                t = fbs_idx[j]
                exp = exp_elite_wins[t]
                if ccg_played[t] == 1:
                    exp += ccg_elite_expectation
                final[t] = (w_rating * rating[t]
                            + k_resume * (wins[t] + ccg_delta[t] - exp)
                            + k_champ * champ[t]
                            + k_sos * sos[t]
                            + k_lsq * lsq[t])
                sortkey[j] = -final[t]

            ranked = np.argsort(sortkey)
            for k in range(n_fbs):
                rank_of[fbs_idx[ranked[k]]] = k

            # 6. Playoff field
            for t in range(n_teams):
                picked[t] = 0
                seed_of[t] = 0
            n_sel = 0

            # the four power-conference champions are guaranteed
            for k in range(n_fbs):
                t = fbs_idx[ranked[k]]
                if champ[t] == 1 and conf_id[t] >= 0 and conf_is_power[conf_id[t]]:
                    if picked[t] == 0 and n_sel < field:
                        picked[t] = 1
                        seeds[n_sel] = t
                        n_sel += 1
            # plus the highest-ranked Group of Six team. From 2026 this bid
            # goes to the best team in those leagues whether or not it won its
            # conference, which is the rule that changed after Tulane and
            # James Madison both auto-qualified in 2025. Independents are not
            # a Group of Six conference and cannot take it, which is what
            # conf_crowns screens out here.
            for k in range(n_fbs):
                t = fbs_idx[ranked[k]]
                if (picked[t] == 0 and conf_id[t] >= 0
                        and not conf_is_power[conf_id[t]]
                        and conf_crowns[conf_id[t]]):
                    picked[t] = 1
                    seeds[n_sel] = t
                    n_sel += 1
                    break
            # at-large bids fill the rest
            for k in range(n_fbs):
                if n_sel >= field:
                    break
                t = fbs_idx[ranked[k]]
                if picked[t] == 0:
                    picked[t] = 1
                    seeds[n_sel] = t
                    n_sel += 1

            # straight seeding: reorder the field by committee rank
            for a in range(1, n_sel):
                key = seeds[a]
                kr = rank_of[key]
                b = a - 1
                while b >= 0 and rank_of[seeds[b]] > kr:
                    seeds[b + 1] = seeds[b]
                    b -= 1
                seeds[b + 1] = key
            for k in range(n_sel):
                seed_of[seeds[k]] = k + 1
                reached[k] = 1
            for k in range(n_byes):
                reached[k] = 2  # a bye is a free trip to the quarterfinal

            # 7. Bracket
            if n_sel == field:
                w1 = _play(seeds, 4, 11, eff, scale, hfa, sigma, True)
                reached[w1] = 2
                w2 = _play(seeds, 5, 10, eff, scale, hfa, sigma, True)
                reached[w2] = 2
                w3 = _play(seeds, 6, 9, eff, scale, hfa, sigma, True)
                reached[w3] = 2
                w4 = _play(seeds, 7, 8, eff, scale, hfa, sigma, True)
                reached[w4] = 2

                q1 = _play(seeds, 0, w4, eff, scale, hfa, sigma, False)
                reached[q1] = 3
                q2 = _play(seeds, 1, w3, eff, scale, hfa, sigma, False)
                reached[q2] = 3
                q3 = _play(seeds, 2, w2, eff, scale, hfa, sigma, False)
                reached[q3] = 3
                q4 = _play(seeds, 3, w1, eff, scale, hfa, sigma, False)
                reached[q4] = 3

                s1 = _play(seeds, q1, q4, eff, scale, hfa, sigma, False)
                reached[s1] = 4
                s2 = _play(seeds, q2, q3, eff, scale, hfa, sigma, False)
                reached[s2] = 4
                ch = _play(seeds, s1, s2, eff, scale, hfa, sigma, False)
                reached[ch] = 5
            else:
                for k in range(field):
                    reached[k] = 0

            # 8. Outputs
            for k in range(n_rem):
                out_hw[s, k] = 1 if winner[remaining_idx[k]] == 1 else 0

            ft = focus_team
            out_wins[s] = wins[ft]
            out_losses[s] = losses[ft]
            out_seed[s] = seed_of[ft]
            out_rank[s] = rank_of[ft] + 1 if rank_of[ft] < 9999 else 0

            for m in range(N_METRICS):
                out_metrics[s, m] = 0
            out_metrics[s, M_WIN_CONF] = champ[ft]
            out_metrics[s, M_CCG] = in_ccg[ft]
            out_metrics[s, M_UNDEFEATED] = 1 if losses[ft] == 0 else 0
            sd = seed_of[ft]
            if sd > 0:
                out_metrics[s, M_PLAYOFF] = 1
                if sd <= n_byes:
                    out_metrics[s, M_TOP4] = 1
                r = reached[sd - 1]
                if r >= 2:
                    out_metrics[s, M_QF] = 1
                if r >= 3:
                    out_metrics[s, M_SF] = 1
                if r >= 4:
                    out_metrics[s, M_TITLE_GAME] = 1
                if r >= 5:
                    out_metrics[s, M_NATL] = 1

            # league-wide tallies, so the app can show anyone's odds
            for j in range(n_fbs):
                t = fbs_idx[j]
                if champ[t] == 1:
                    team_counts[c, t, M_WIN_CONF] += 1
                if in_ccg[t] == 1:
                    team_counts[c, t, M_CCG] += 1
                if losses[t] == 0:
                    team_counts[c, t, M_UNDEFEATED] += 1
            for k in range(n_sel):
                t = seeds[k]
                team_counts[c, t, M_PLAYOFF] += 1
                if k < n_byes:
                    team_counts[c, t, M_TOP4] += 1
                r = reached[k]
                if r >= 2:
                    team_counts[c, t, M_QF] += 1
                if r >= 3:
                    team_counts[c, t, M_SF] += 1
                if r >= 4:
                    team_counts[c, t, M_TITLE_GAME] += 1
                if r >= 5:
                    team_counts[c, t, M_NATL] += 1


@njit(cache=True, inline="always")
def _play(seeds, i, j, eff, scale, hfa, sigma, home_field):
    """Play seed slot ``i`` against slot ``j``; return the winning slot."""
    a = seeds[i]
    b = seeds[j]
    edge = hfa if home_field else 0.0
    p = _norm_cdf((scale * (eff[a] - eff[b]) + edge) / sigma)
    return i if np.random.random() < p else j
