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

# Automatic bid rules, passed as bid_rule.
BIDS_2026 = 0         # power champions plus the best Group of Six team
BIDS_CHAMPIONS = 1    # the five highest-ranked champions (2024, 2025)

_SQRT2 = 1.4142135623730951


@njit(cache=True, inline="always")
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


@njit(cache=True, inline="always")
def _win_prob(ra, rb, scale, edge, sigma):
    """P(team a beats team b) given a rating edge already applied to a."""
    return _norm_cdf((scale * (ra - rb) + edge) / sigma)


@njit(cache=True, inline="always")
def _draw_score(mu, sigma, tb, ts, tsd):
    """One game's margin and the two scores, as (margin, home, away).

    The margin is drawn from the distribution the win probability already came
    from, so switching from a coin flip to a margin leaves every game's odds
    untouched. The total is drawn around it -- blowouts are slightly higher
    scoring, which is the ts slope -- and is held at or above the margin so the
    loser never finishes below zero.
    """
    m = mu + sigma * np.random.normal(0.0, 1.0)
    if m == 0.0:
        m = 1.0                      # football has no ties any more
    t = tb + ts * abs(m) + tsd * np.random.normal(0.0, 1.0)
    if t < abs(m):
        t = abs(m)
    return m, 0.5 * (t + m), 0.5 * (t - m)


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
                   rating, conf_id, div_id, is_fbs,
                   g_home, g_away, g_neutral, g_conf, g_status, g_hpts, g_apts,
                   remaining_idx,
                   conf_teams_ptr, conf_teams, conf_games_ptr, conf_games,
                   conf_has_ccg, conf_crowns, conf_n_div, conf_is_power,
                   conf_fixed_ccg,
                   fbs_idx,
                   n_chunk_hint, m_node, m_hn, m_an, m_hfix, m_afix,
                   m_hinv, m_prior, m_prec, m_tg_ptr, m_tg_games, m_tg_home,
                   hfa, sigma, tau, scale,
                   total_base, total_slope, total_sd,
                   gof_k, gof_c, gof_q, mov_w, mov_flat, m_iters,
                   corr_sd, corr_passes, committee_sd, jump_margin, h2h_depth,
                   n_byes, bid_rule, champion_byes,
                   out_hw, out_metrics,
                   h_wins, h_wins_made, h_seed, h_rank):
    n_teams = rating.shape[0]
    n_conf = conf_has_ccg.shape[0]
    n_g = g_home.shape[0]
    n_rem = remaining_idx.shape[0]
    n_fbs = fbs_idx.shape[0]
    n_chunks = n_chunk_hint
    n_nodes = m_prior.shape[0] - 1
    field = 12

    for c in prange(n_chunks):
        lo = c * sims_per_chunk
        if lo >= n_sims:
            continue
        hi = min(lo + sims_per_chunk, n_sims)
        np.random.seed(seed + c * 7919 + 1)

        eff = rating.copy()          # true strength; non-FBS teams keep their rating
        winner = np.zeros(n_g, dtype=np.uint8)
        hpts = np.zeros(n_g, dtype=np.float64)
        apts = np.zeros(n_g, dtype=np.float64)
        wins = np.zeros(n_teams, dtype=np.int32)
        losses = np.zeros(n_teams, dtype=np.int32)
        cwins = np.zeros(n_teams, dtype=np.int32)
        closses = np.zeros(n_teams, dtype=np.int32)
        score = np.zeros(n_teams, dtype=np.float64)
        final = np.zeros(n_teams, dtype=np.float64)
        g_at_home = np.zeros(n_g, dtype=np.float64)
        for i in range(n_g):
            if not g_neutral[i]:
                g_at_home[i] = 1.0
        mr = m_prior.copy()          # warm start, carried between seasons
        gval = np.zeros(n_g, dtype=np.float64)
        mgrad = np.zeros(n_nodes + 1, dtype=np.float64)
        mstep = np.zeros(n_nodes + 1, dtype=np.float64)
        mpower = np.zeros(n_nodes, dtype=np.float64)
        ccg_beat = np.full(n_nodes, -1, dtype=np.int32)
        ccg_w = np.full(n_conf, -1, dtype=np.int32)
        ccg_l = np.full(n_conf, -1, dtype=np.int32)
        champ = np.zeros(n_teams, dtype=np.uint8)
        in_ccg = np.zeros(n_teams, dtype=np.uint8)
        rank_of = np.full(n_teams, 9999, dtype=np.int32)
        seed_of = np.zeros(n_teams, dtype=np.int32)
        order = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
        pct = np.zeros(MAX_CONF_SIZE, dtype=np.float64)
        h2h = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
        mark = np.full(n_teams, -1, dtype=np.int32)
        members = np.zeros(MAX_CONF_SIZE, dtype=np.int32)
        sortkey = np.zeros(n_fbs, dtype=np.float64)
        seeds = np.zeros(field, dtype=np.int32)
        reseed = np.zeros(field, dtype=np.int32)
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

            # 1. Game outcomes and scores. Played games keep their real
            # scores; the rest get a drawn margin, and the two scores follow
            # from it. Drawing the margin rather than flipping a weighted coin
            # gives exactly the same win probability, because it is the same
            # distribution the probability was read off in the first place.
            for i in range(n_g):
                st = g_status[i]
                if st != 0:
                    winner[i] = st
                    hpts[i] = g_hpts[i]
                    apts[i] = g_apts[i]
                    continue
                edge = 0.0 if g_neutral[i] else hfa
                mu = scale * (eff[g_home[i]] - eff[g_away[i]]) + edge
                m, hp, ap = _draw_score(mu, sigma, total_base, total_slope,
                                        total_sd)
                winner[i] = 1 if m > 0.0 else 2
                hpts[i] = hp
                apts[i] = ap

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

            # 3. Massey power rating for this season's scores. Only the
            # power stage runs here; the win-loss correction waits until the
            # title games are played, because a title game win has to count
            # for the winner and a loss has to not count against the loser.
            _massey_power(n_g, m_hn, m_an, m_hfix, m_afix, g_at_home,
                          hpts, apts, m_hinv, m_prior, m_prec, n_nodes,
                          gof_k, gof_c, gof_q, mov_w, mov_flat, m_iters,
                          mr, gval, mgrad, mstep)
            for j in range(n_fbs):
                t = fbs_idx[j]
                score[t] = mr[m_node[t]]

            # 4. Conference championships
            for t in range(n_teams):
                champ[t] = 0
                in_ccg[t] = 0
            for k in range(n_nodes):
                ccg_beat[k] = -1
            for cf in range(n_conf):
                ccg_w[cf] = -1
                ccg_l[cf] = -1

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
                ccg_w[cf] = won
                ccg_l[cf] = lost
                ccg_beat[m_node[won]] = m_node[lost]

            # 5. The win-loss correction, then the ranking. The correction
            # sees the title games, one way only. A nudge on top stands in for
            # the committee's own variability.
            _massey_correct(m_hn, m_an, m_hfix, m_afix, g_at_home, hpts, apts,
                            n_nodes, mr, corr_sd, corr_passes,
                            m_tg_ptr, m_tg_games, m_tg_home, ccg_beat, mpower)
            for j in range(n_fbs):
                t = fbs_idx[j]
                v = mpower[m_node[t]]
                if committee_sd > 0.0:
                    v += committee_sd * np.random.normal(0.0, 1.0)
                final[t] = v
                sortkey[j] = -v

            ranked = np.argsort(sortkey)

            # Head to head: a team directly below one it beat in the regular
            # season swaps with it. Only the top of the table can matter to the
            # field, so only that far down is checked.
            depth = h2h_depth
            if depth > n_fbs:
                depth = n_fbs
            for _pass in range(6):
                moved = False
                for k in range(depth - 1):
                    hi = fbs_idx[ranked[k]]
                    lo = fbs_idx[ranked[k + 1]]
                    if _beat(m_node[lo], m_node[hi], m_hn, m_an, hpts, apts,
                             m_tg_ptr, m_tg_games, m_tg_home):
                        tmp = ranked[k]
                        ranked[k] = ranked[k + 1]
                        ranked[k + 1] = tmp
                        moved = True
                if not moved:
                    break

            # A title game winner sitting just behind the team it beat moves in
            # front of it, when the rating says the two are that close.
            for cf in range(n_conf):
                w = ccg_w[cf]
                l = ccg_l[cf]
                if w < 0 or l < 0:
                    continue
                if final[l] - final[w] > jump_margin:
                    continue
                pw = -1
                pl = -1
                for k in range(n_fbs):
                    t = fbs_idx[ranked[k]]
                    if t == w:
                        pw = k
                    elif t == l:
                        pl = k
                if pw < 0 or pl < 0 or pw <= pl:
                    continue
                tmp = ranked[pw]
                for k in range(pw, pl, -1):
                    ranked[k] = ranked[k - 1]
                ranked[pl] = tmp

            for k in range(n_fbs):
                rank_of[fbs_idx[ranked[k]]] = k

            # 6. Playoff field
            for t in range(n_teams):
                picked[t] = 0
                seed_of[t] = 0
            n_sel = 0

            if bid_rule == BIDS_2026:
                # the four power-conference champions are guaranteed
                for k in range(n_fbs):
                    t = fbs_idx[ranked[k]]
                    if (champ[t] == 1 and conf_id[t] >= 0
                            and conf_is_power[conf_id[t]] and picked[t] == 0):
                        picked[t] = 1
                        seeds[n_sel] = t
                        n_sel += 1
                # plus the highest-ranked Group of Six team, whether or not it
                # won its conference. Independents are not a Group of Six
                # conference and cannot take it, which is what conf_crowns
                # screens out here.
                for k in range(n_fbs):
                    t = fbs_idx[ranked[k]]
                    if (picked[t] == 0 and conf_id[t] >= 0
                            and not conf_is_power[conf_id[t]]
                            and conf_crowns[conf_id[t]]):
                        picked[t] = 1
                        seeds[n_sel] = t
                        n_sel += 1
                        break
            else:
                # 2024 and 2025: the five highest-ranked conference champions,
                # from any conference, which is how Tulane and James Madison
                # both got in in 2025.
                for k in range(n_fbs):
                    if n_sel >= 5:
                        break
                    t = fbs_idx[ranked[k]]
                    if (champ[t] == 1 and conf_id[t] >= 0
                            and conf_crowns[conf_id[t]]):
                        picked[t] = 1
                        seeds[n_sel] = t
                        n_sel += 1
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
            if champion_byes:
                # 2024: the four best-ranked champions take seeds 1-4 and the
                # byes, however far down the ranking they sit. Everyone else
                # follows in ranking order.
                n_top = 0
                for k in range(n_sel):
                    if champ[seeds[k]] == 1 and n_top < 4:
                        reseed[n_top] = seeds[k]
                        n_top += 1
                n_rest = n_top
                for k in range(n_sel):
                    t = seeds[k]
                    is_top = False
                    for q in range(n_top):
                        if reseed[q] == t:
                            is_top = True
                    if not is_top:
                        reseed[n_rest] = t
                        n_rest += 1
                for k in range(n_sel):
                    seeds[k] = reseed[k]
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

            # 8. Outputs. Everything is recorded for every team. Which team
            # is being asked about never changed how a season played out, so
            # one set of simulations answers for all of them.
            for k in range(n_rem):
                out_hw[s, k] = 1 if winner[remaining_idx[k]] == 1 else 0

            max_w = h_wins.shape[2] - 1
            max_r = h_rank.shape[2] - 1
            for j in range(n_fbs):
                t = fbs_idx[j]
                for m in range(N_METRICS):
                    out_metrics[s, m, j] = 0
                out_metrics[s, M_WIN_CONF, j] = champ[t]
                out_metrics[s, M_CCG, j] = in_ccg[t]
                if losses[t] == 0:
                    out_metrics[s, M_UNDEFEATED, j] = 1
                w = wins[t]
                if w > max_w:
                    w = max_w
                h_wins[c, j, w] += 1
                rk = rank_of[t] + 1 if rank_of[t] < 9999 else 0
                if rk > max_r:
                    rk = max_r
                h_rank[c, j, rk] += 1
                h_seed[c, j, 0] += 1

            if n_sel == field:
                for k in range(n_sel):
                    t = seeds[k]
                    j = m_node[t]
                    h_seed[c, j, 0] -= 1
                    h_seed[c, j, k + 1] += 1
                    w = wins[t]
                    if w > max_w:
                        w = max_w
                    h_wins_made[c, j, w] += 1
                    out_metrics[s, M_PLAYOFF, j] = 1
                    if k < n_byes:
                        out_metrics[s, M_TOP4, j] = 1
                    rc = reached[k]
                    if rc >= 2:
                        out_metrics[s, M_QF, j] = 1
                    if rc >= 3:
                        out_metrics[s, M_SF, j] = 1
                    if rc >= 4:
                        out_metrics[s, M_TITLE_GAME, j] = 1
                    if rc >= 5:
                        out_metrics[s, M_NATL, j] = 1


@njit(cache=True, inline="always")
def _play(seeds, i, j, eff, scale, hfa, sigma, home_field):
    """Play seed slot ``i`` against slot ``j``; return the winning slot."""
    a = seeds[i]
    b = seeds[j]
    edge = hfa if home_field else 0.0
    p = _norm_cdf((scale * (eff[a] - eff[b]) + edge) / sigma)
    return i if np.random.random() < p else j


# Massey rating, fitted inside each simulated season

_INV_SQRT_2PI = 0.3989422804014327
_MCLIP = 8.0


@njit(cache=True, inline="always")
def _norm_pdf(x):
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


@njit(cache=True, inline="always")
def _beat(lo, hi, hn, an, hpts, apts, tg_ptr, tg_games, tg_home):
    """Did node ``lo`` beat node ``hi``, and never lose to them?"""
    wins = 0
    losses = 0
    for gi in range(tg_ptr[lo], tg_ptr[lo + 1]):
        i = tg_games[gi]
        if tg_home[gi] == 1:
            if an[i] != hi:
                continue
            won = hpts[i] > apts[i]
        else:
            if hn[i] != hi:
                continue
            won = apts[i] > hpts[i]
        if won:
            wins += 1
        else:
            losses += 1
    return wins > 0 and losses == 0


@njit(cache=True)
def _massey_power(n_g, hn, an, hfix, afix, g_at_home, hpts, apts,
                  hinv, prior, prec, n_nodes,
                  gof_k, gof_c, gof_q, mov_w, mov_flat, n_iter,
                  r, gval, grad, step):
    """Stage one and two: score each game, then fit the power rating.

    ``r`` is warm started from the previous season and updated in place. Only
    FBS teams have an entry in it; a game against anyone else carries that
    opponent's known rating in ``hfix``/``afix`` and contributes to one side
    of the gradient only.

    The Hessian was built once at the curvature bound, so every step is an
    under-step and this converges from anywhere.
    """
    n = n_nodes + 1
    for i in range(n_g):
        hp = hpts[i]
        ap = apts[i]
        gi = _norm_cdf(gof_k * (hp - ap) / ((hp + ap + gof_c) ** gof_q))
        if mov_w < 1.0:
            tgt = mov_flat if hp > ap else 1.0 - mov_flat
            gi = mov_w * gi + (1.0 - mov_w) * tgt
        gval[i] = gi

    for _ in range(n_iter):
        for k in range(n):
            grad[k] = 0.0
        for i in range(n_g):
            h = hn[i]
            a = an[i]
            vh = r[h] if h >= 0 else hfix[i]
            va = r[a] if a >= 0 else afix[i]
            d = vh - va + r[n_nodes] * g_at_home[i]
            if d > _MCLIP:
                d = _MCLIP
            elif d < -_MCLIP:
                d = -_MCLIP
            cdf = _norm_cdf(d)
            if cdf < 1e-12:
                cdf = 1e-12
            omc = 1.0 - cdf
            if omc < 1e-12:
                omc = 1e-12
            sc = _norm_pdf(d) * (gval[i] / cdf - (1.0 - gval[i]) / omc)
            if h >= 0:
                grad[h] += sc
            if a >= 0:
                grad[a] -= sc
            if g_at_home[i] > 0:
                grad[n_nodes] += sc
        for k in range(n):
            grad[k] -= prec[k] * (r[k] - prior[k])
        for k in range(n):
            acc = 0.0
            for j in range(n):
                acc += hinv[k, j] * grad[j]
            step[k] = acc
        for k in range(n):
            r[k] += step[k]


@njit(cache=True)
def _massey_correct(hn, an, hfix, afix, g_at_home, hpts, apts, n_nodes,
                    r, corr_sd, corr_passes, tg_ptr, tg_games, tg_home,
                    ccg_beat, power):
    """Stage three: the Bayesian win-loss correction, taken at the mode.

    The standalone integrates the posterior over a grid; with a prior this
    tight the mode is within a couple of places of the mean and costs a
    fraction as much, which is what a Monte Carlo needs.

    ``ccg_beat`` carries a conference title game win, and only a win. Adding it
    here rather than to the power fit is what lets a title game help the winner
    without the loss touching the loser: the loser simply has no entry.
    """
    hfa_r = r[n_nodes]
    inv_s2 = 1.0 / (corr_sd * corr_sd)
    for k in range(n_nodes):
        power[k] = r[k]
    for _ in range(corr_passes):
        for t in range(n_nodes):
            x = power[t]
            for _newton in range(2):
                f1 = -(x - r[t]) * inv_s2
                f2 = -inv_s2
                for gi in range(tg_ptr[t], tg_ptr[t + 1]):
                    i = tg_games[gi]
                    if tg_home[gi] == 1:
                        on = an[i]
                        ov = power[on] if on >= 0 else afix[i]
                        edge = hfa_r * g_at_home[i]
                        won = hpts[i] > apts[i]
                    else:
                        on = hn[i]
                        ov = power[on] if on >= 0 else hfix[i]
                        edge = -hfa_r * g_at_home[i]
                        won = apts[i] > hpts[i]
                    d = x - ov + edge
                    if d > _MCLIP:
                        d = _MCLIP
                    elif d < -_MCLIP:
                        d = -_MCLIP
                    cdf = _norm_cdf(d)
                    if cdf < 1e-12:
                        cdf = 1e-12
                    omc = 1.0 - cdf
                    if omc < 1e-12:
                        omc = 1e-12
                    pdf = _norm_pdf(d)
                    if won:
                        f1 += pdf / cdf
                    else:
                        f1 -= pdf / omc
                    f2 -= pdf * pdf / (cdf * omc)
                if ccg_beat[t] >= 0:
                    d = x - power[ccg_beat[t]]
                    if d > _MCLIP:
                        d = _MCLIP
                    elif d < -_MCLIP:
                        d = -_MCLIP
                    cdf = _norm_cdf(d)
                    if cdf < 1e-12:
                        cdf = 1e-12
                    omc = 1.0 - cdf
                    if omc < 1e-12:
                        omc = 1e-12
                    pdf = _norm_pdf(d)
                    f1 += pdf / cdf
                    f2 -= pdf * pdf / (cdf * omc)
                if f2 < -1e-12:
                    x = x - f1 / f2
            power[t] = x
