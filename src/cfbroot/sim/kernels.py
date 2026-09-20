"""Numba kernel that simulates full seasons.

Works on flat numpy arrays so numba can compile it. Without numba the same
code runs as plain Python.

Each simulated season:
1. draws a score for every unplayed regular season game
2. adds up overall and conference records
3. fits the Massey power rating, without the title games
4. orders each conference, breaks ties, and plays the title games
5. rates every team the way massey.rate_selection_day() does, adds the
   committee noise, and applies the head to head and title game rules
6. picks and seeds the 12-team playoff
7. plays the bracket

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
                   conf_ccg_pts, conf_ccg_home,
                   fbs_idx,
                   n_chunk_hint, m_node, m_n_fbs, m_in_fit, m_hn, m_an,
                   m_xh, m_xa, m_xhome, m_xg, m_xwon, m_played,
                   m_minv, m_start, m_prior, m_prec, m_tg_ptr, m_tg_ref, m_tg_home,
                   m_gh_t, m_gh_logw,
                   hfa, sigma, tau, scale,
                   total_base, total_slope, total_sd,
                   gof_k, gof_c, gof_q, mov_w, mov_flat,
                   prior_sd, prior_games, fit_tol, fit_max_iter,
                   corr_sd, corr_passes, committee_sd, jump_margin,
                   worst_loss, worst_loss_scale, h2h_depth,
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
        r0 = m_start.copy()          # warm start, carried between seasons
        r1 = m_start.copy()
        fvec, fwts = _fit_work(n_nodes, n_g, m_xh.shape[0], n_conf)
        c0 = np.zeros(n_nodes, dtype=np.float64)
        c1 = np.zeros(n_nodes, dtype=np.float64)
        gval = np.zeros(n_g, dtype=np.float64)
        prec0 = m_prec.copy()
        prec1 = np.zeros(n_nodes + 1, dtype=np.float64)
        cwork = np.zeros(n_nodes, dtype=np.float64)
        ll = np.zeros(m_gh_t.shape[0], dtype=np.float64)
        need = np.zeros(n_nodes, dtype=np.uint8)
        cc_h = np.zeros(n_conf, dtype=np.int32)
        cc_a = np.zeros(n_conf, dtype=np.int32)
        cc_home = np.zeros(n_conf, dtype=np.float64)
        cc_g = np.zeros(n_conf, dtype=np.float64)
        cc_won = np.zeros(n_conf, dtype=np.uint8)
        cc_of = np.full(n_nodes, -1, dtype=np.int32)
        loser = np.zeros(n_nodes, dtype=np.uint8)
        ccg_w = np.full(n_conf, -1, dtype=np.int32)
        ccg_l = np.full(n_conf, -1, dtype=np.int32)
        champ = np.zeros(n_teams, dtype=np.uint8)
        in_ccg = np.zeros(n_teams, dtype=np.uint8)
        rank_of = np.full(n_teams, 9999, dtype=np.int32)
        place = np.zeros(n_nodes, dtype=np.int32)
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

            # 3. Massey power rating without the title games. It breaks ties
            # in the conference standings, and it is the fit a title game
            # loser keeps.
            for i in range(n_g):
                if m_in_fit[i]:
                    gval[i] = _outcome(hpts[i], apts[i], gof_k, gof_c, gof_q,
                                       mov_w, mov_flat)
            _fit(n_g, m_in_fit, m_hn, m_an, g_at_home, gval,
                 m_xh, m_xa, m_xhome, m_xg, cc_h, cc_a, cc_home, cc_g, 0,
                 m_minv, m_prior, prec0, n_nodes, fit_tol, fit_max_iter,
                 r0, m_start, fvec, fwts)
            for j in range(n_fbs):
                t = fbs_idx[j]
                score[t] = r0[m_node[t]]

            # 4. Conference championships
            for t in range(n_teams):
                champ[t] = 0
                in_ccg[t] = 0
            n_cc = 0
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
                if st != 0:
                    p1 = conf_ccg_pts[cf, 0]
                    p2 = conf_ccg_pts[cf, 1]
                    at_home = conf_ccg_home[cf]
                else:
                    # A drawn margin, like any other game. Title games are
                    # treated as neutral site.
                    _m, p1, p2 = _draw_score(scale * (eff[t1] - eff[t2]), sigma,
                                             total_base, total_slope, total_sd)
                    at_home = 0.0
                if p1 > p2:
                    won = t1
                    lost = t2
                else:
                    won = t2
                    lost = t1
                champ[won] = 1
                ccg_w[cf] = won
                ccg_l[cf] = lost
                cc_h[n_cc] = m_node[t1]
                cc_a[n_cc] = m_node[t2]
                cc_home[n_cc] = at_home
                cc_g[n_cc] = _outcome(p1, p2, gof_k, gof_c, gof_q, mov_w, mov_flat)
                cc_won[n_cc] = 1 if p1 > p2 else 0
                n_cc += 1

            # 5. The rating, exactly as massey.rate_selection_day() does it.
            # Two fits: one with the title games, which everyone takes, and
            # the one above without them, which a title game loser keeps. So a
            # title game can only help. A nudge on top stands in for the
            # committee's own variability.
            _selection_rating(n_g, m_in_fit, m_hn, m_an, g_at_home, gval,
                              hpts, apts, m_xh, m_xa, m_xhome, m_xg, m_xwon,
                              cc_h, cc_a, cc_home, cc_g, cc_won, n_cc, cc_of,
                              loser, m_minv, m_prior, m_start, prec0, prec1, m_played,
                              n_nodes, m_n_fbs, prior_sd, prior_games,
                              fit_tol, fit_max_iter, corr_sd, corr_passes,
                              m_gh_t, m_gh_logw, m_tg_ptr, m_tg_ref, m_tg_home,
                              r0, r1, c0, c1, cwork, ll, need, fvec, fwts)
            for j in range(n_fbs):
                t = fbs_idx[j]
                k = m_node[t]
                v = c0[k] if loser[k] else c1[k]
                if committee_sd > 0.0:
                    v += committee_sd * np.random.normal(0.0, 1.0)
                final[t] = v
                sortkey[j] = -v

            ranked = np.argsort(sortkey)

            # Worst loss: a team whose weakest defeat came to a team near the
            # top is treated better than one that lost to nobody in
            # particular. Read off the order the ratings alone give.
            if worst_loss > 0.0:
                for k in range(n_nodes):
                    place[k] = n_fbs + 1
                for k in range(n_fbs):
                    place[m_node[fbs_idx[ranked[k]]]] = k + 1
                for j in range(n_fbs):
                    t = fbs_idx[j]
                    node = m_node[t]
                    low = 0
                    for gi in range(m_tg_ptr[node], m_tg_ptr[node + 1]):
                        i = m_tg_ref[gi]
                        if i >= n_g:
                            continue          # a game outside the schedule
                        if m_tg_home[gi] == 1:
                            lost = apts[i] > hpts[i]
                            opp = m_an[i]
                        else:
                            lost = hpts[i] > apts[i]
                            opp = m_hn[i]
                        if lost and place[opp] > low:
                            low = place[opp]
                    if low == 0:
                        final[t] += worst_loss
                    else:
                        final[t] += worst_loss * math.exp(-(low - 1) / worst_loss_scale)
                    sortkey[j] = -final[t]
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
                    if _beat(m_node[lo], m_node[hi], n_g, m_hn, m_an,
                             hpts, apts, m_tg_ptr, m_tg_ref, m_tg_home):
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
_MAX_STEP = 1.0                      # longest step the fit takes, rating units
_MCLIP = 8.0                         # massey._CLIP
_INFO_PER_GAME = 0.6366197723675814  # massey.INFO_PER_GAME


@njit(cache=True, inline="always")
def _norm_pdf(x):
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


@njit(cache=True, inline="always")
def _beat(lo, hi, n_g, hn, an, hpts, apts, tg_ptr, tg_ref, tg_home):
    """Did node ``lo`` beat node ``hi`` in the season, and never lose to it?"""
    wins = 0
    losses = 0
    for gi in range(tg_ptr[lo], tg_ptr[lo + 1]):
        i = tg_ref[gi]
        if i >= n_g:
            continue                      # a game outside the season's schedule
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


@njit(cache=True, inline="always")
def _ndtr(x):
    """Standard normal CDF, accurate in the lower tail."""
    return 0.5 * math.erfc(-x / _SQRT2)


@njit(cache=True, inline="always")
def _log_ndtr(x):
    if x > 0.0:
        return math.log1p(-0.5 * math.erfc(x / _SQRT2))
    return math.log(0.5 * math.erfc(-x / _SQRT2))


@njit(cache=True, inline="always")
def _outcome(hp, ap, gof_k, gof_c, gof_q, mov_w, mov_flat):
    """massey.gof() for one game, with the margin damped by mov_weight."""
    g = _ndtr(gof_k * (hp - ap) / ((hp + ap + gof_c) ** gof_q))
    if mov_w < 1.0:
        tgt = mov_flat if hp > ap else 1.0 - mov_flat
        g = mov_w * g + (1.0 - mov_w) * tgt
    return g


@njit(cache=True, inline="always")
def _add_score(h, a, at_home, g, r, grad, n_nodes):
    """One game's pull on the gradient; returns its Fisher information."""
    d = r[h] - r[a] + r[n_nodes] * at_home
    if d > _MCLIP:
        d = _MCLIP
    elif d < -_MCLIP:
        d = -_MCLIP
    cdf = _ndtr(d)
    pdf = _norm_pdf(d)
    c = cdf if cdf > 1e-12 else 1e-12
    omc = 1.0 - cdf
    if omc < 1e-12:
        omc = 1e-12
    sc = pdf * (g / c - (1.0 - g) / omc)
    grad[h] += sc
    grad[a] -= sc
    grad[n_nodes] += sc * at_home
    v = cdf * (1.0 - cdf)
    if v < 1e-12:
        v = 1e-12
    return pdf * pdf / v


@njit(cache=True, inline="always")
def _add_curve(h, a, at_home, w, v, out, n_nodes):
    x = w * (v[h] - v[a] + v[n_nodes] * at_home)
    out[h] += x
    out[a] -= x
    out[n_nodes] += x * at_home


@njit(cache=True)
def _fit_work(n_nodes, n_g, n_x, n_conf):
    """Scratch space for _fit_power, one per thread: six vectors, then one
    Fisher information per game (season games, extra games, title games)."""
    return (np.zeros((6, n_nodes + 1), dtype=np.float64),
            np.zeros(n_g + n_x + n_conf, dtype=np.float64))


@njit(cache=True)
def _fit_power(n_g, in_fit, hn, an, g_at_home, gval, x_h, x_a, x_home, x_g,
               cc_h, cc_a, cc_home, cc_g, n_cc,
               minv, prior, prec, n_nodes, tol, max_iter, r, vec, wts):
    """massey.fit_power(): Fisher scoring to the same maximum.

    Each step solves the same linear system the standalone solves, by
    conjugate gradients steered with the curvature of a typical season, which
    is close to every simulated one. ``r`` is the starting point and is
    updated in place.
    """
    n = n_nodes + 1
    grad, step, res, z, pv, ap = vec[0], vec[1], vec[2], vec[3], vec[4], vec[5]
    n_x = x_h.shape[0]
    wg = wts[:n_g]
    wx = wts[n_g:n_g + n_x]
    wc = wts[n_g + n_x:]
    for it in range(max_iter):
        for k in range(n):
            grad[k] = 0.0
        for i in range(n_g):
            if in_fit[i]:
                wg[i] = _add_score(hn[i], an[i], g_at_home[i], gval[i], r,
                                   grad, n_nodes)
        for i in range(n_x):
            wx[i] = _add_score(x_h[i], x_a[i], x_home[i], x_g[i], r, grad, n_nodes)
        for i in range(n_cc):
            wc[i] = _add_score(cc_h[i], cc_a[i], cc_home[i], cc_g[i], r, grad,
                               n_nodes)
        gmax = 0.0
        for k in range(n):
            grad[k] -= prec[k] * (r[k] - prior[k])
            if not abs(grad[k]) < math.inf:
                return -1                  # the ratings are not numbers
            if abs(grad[k]) > gmax:
                gmax = abs(grad[k])

        # Solve (information + prior) step = grad.
        rz = 0.0
        for k in range(n):
            step[k] = 0.0
            res[k] = grad[k]
        for k in range(n):
            acc = 0.0
            for j in range(n):
                acc += minv[k, j] * res[j]
            z[k] = acc
            pv[k] = acc
            rz += res[k] * acc
        for _cg in range(n):
            if not rz > 0.0:
                break
            for k in range(n):
                ap[k] = prec[k] * pv[k]
            for i in range(n_g):
                if in_fit[i]:
                    _add_curve(hn[i], an[i], g_at_home[i], wg[i], pv, ap, n_nodes)
            for i in range(n_x):
                _add_curve(x_h[i], x_a[i], x_home[i], wx[i], pv, ap, n_nodes)
            for i in range(n_cc):
                _add_curve(cc_h[i], cc_a[i], cc_home[i], wc[i], pv, ap, n_nodes)
            pap = 0.0
            for k in range(n):
                pap += pv[k] * ap[k]
            if not pap > 0.0:
                break
            alpha = rz / pap
            rmax = 0.0
            for k in range(n):
                step[k] += alpha * pv[k]
                res[k] -= alpha * ap[k]
                if abs(res[k]) > rmax:
                    rmax = abs(res[k])
            if rmax <= 1e-6 * gmax:
                break
            rz_new = 0.0
            for k in range(n):
                acc = 0.0
                for j in range(n):
                    acc += minv[k, j] * res[j]
                z[k] = acc
                rz_new += res[k] * acc
            beta = rz_new / rz
            rz = rz_new
            for k in range(n):
                pv[k] = z[k] + beta * pv[k]

        big = 0.0
        for k in range(n):
            if abs(step[k]) > big:
                big = abs(step[k])
        if not big < math.inf:
            return -1                      # not a number: give up, see _fit
        # A long step is shortened, so no step can overshoot far enough to
        # leave the fit somewhere it cannot climb back from. Near the answer
        # steps are far below the cap, so where it ends up is unchanged.
        shrink = _MAX_STEP / big if big > _MAX_STEP else 1.0
        for k in range(n):
            r[k] += shrink * step[k]
        if big < tol:
            return it + 1
    return -1


@njit(cache=True)
def _fit(n_g, in_fit, hn, an, g_at_home, gval, x_h, x_a, x_home, x_g,
         cc_h, cc_a, cc_home, cc_g, n_cc,
         minv, prior, prec, n_nodes, tol, max_iter, r, start, vec, wts):
    """_fit_power(), started again from ``start`` if it does not converge.

    Each season starts from the one before, which is close and saves steps.
    A fit that fails must not hand its answer on, or every season after it
    starts from somewhere bad; so it is thrown away and redone from the
    typical season.
    """
    it = _fit_power(n_g, in_fit, hn, an, g_at_home, gval, x_h, x_a, x_home, x_g,
                    cc_h, cc_a, cc_home, cc_g, n_cc,
                    minv, prior, prec, n_nodes, tol, max_iter, r, vec, wts)
    if it < 0:
        for k in range(n_nodes + 1):
            r[k] = start[k]
        it = _fit_power(n_g, in_fit, hn, an, g_at_home, gval, x_h, x_a, x_home,
                        x_g, cc_h, cc_a, cc_home, cc_g, n_cc,
                        minv, prior, prec, n_nodes, tol, max_iter, r, vec, wts)
    return it


@njit(cache=True)
def _correct(n_g, hn, an, g_at_home, hpts, apts, x_h, x_a, x_home, x_won,
             cc_h, cc_a, cc_home, cc_won, cc_of, use_cc,
             tg_ptr, tg_ref, tg_home, n_nodes, power, hfa, sd, passes,
             gh_t, gh_logw, need, prev, out, ll):
    """massey.win_loss_correction(), repeated ``passes`` times.

    Each pass averages every team's rating over its prior, centred where the
    last pass left it, weighted by how likely its wins and losses were against
    opponents also read where the last pass left them. On the last pass only
    the teams flagged in ``need`` are worked out, since nobody reads the rest.
    """
    n_q = gh_t.shape[0]
    for k in range(n_nodes):
        prev[k] = power[k]
    for ps in range(passes):
        last = ps == passes - 1
        for t in range(n_nodes):
            if last and need[t] == 0:
                out[t] = prev[t]
                continue
            for q in range(n_q):
                ll[q] = gh_logw[q]
            centre = prev[t]
            for gi in range(tg_ptr[t], tg_ptr[t + 1]):
                ref = tg_ref[gi]
                home = tg_home[gi] == 1
                if ref < n_g:
                    if home:
                        opp = an[ref]
                        edge = hfa * g_at_home[ref]
                        won = hpts[ref] > apts[ref]
                    else:
                        opp = hn[ref]
                        edge = -hfa * g_at_home[ref]
                        won = apts[ref] > hpts[ref]
                else:
                    k = ref - n_g
                    if home:
                        opp = x_a[k]
                        edge = hfa * x_home[k]
                        won = x_won[k] == 1
                    else:
                        opp = x_h[k]
                        edge = -hfa * x_home[k]
                        won = x_won[k] == 0
                base = centre - prev[opp] + edge
                for q in range(n_q):
                    d = base + sd * gh_t[q]
                    if d > _MCLIP:
                        d = _MCLIP
                    elif d < -_MCLIP:
                        d = -_MCLIP
                    ll[q] += _log_ndtr(d) if won else _log_ndtr(-d)
            if use_cc and cc_of[t] >= 0:
                k = cc_of[t]
                if cc_h[k] == t:
                    opp = cc_a[k]
                    edge = hfa * cc_home[k]
                    won = cc_won[k] == 1
                else:
                    opp = cc_h[k]
                    edge = -hfa * cc_home[k]
                    won = cc_won[k] == 0
                base = centre - prev[opp] + edge
                for q in range(n_q):
                    d = base + sd * gh_t[q]
                    if d > _MCLIP:
                        d = _MCLIP
                    elif d < -_MCLIP:
                        d = -_MCLIP
                    ll[q] += _log_ndtr(d) if won else _log_ndtr(-d)
            top = ll[0]
            for q in range(1, n_q):
                if ll[q] > top:
                    top = ll[q]
            num = 0.0
            den = 0.0
            for q in range(n_q):
                wq = math.exp(ll[q] - top)
                num += wq * (centre + sd * gh_t[q])
                den += wq
            out[t] = num / den
        for k in range(n_nodes):
            prev[k] = out[k]


@njit(cache=True)
def _selection_rating(n_g, in_fit, hn, an, g_at_home, gval, hpts, apts,
                      x_h, x_a, x_home, x_g, x_won,
                      cc_h, cc_a, cc_home, cc_g, cc_won, n_cc, cc_of, loser,
                      minv, prior, start, prec0, prec1, played, n_nodes, n_fbs,
                      prior_sd, prior_games, tol, max_iter,
                      corr_sd, corr_passes, gh_t, gh_logw,
                      tg_ptr, tg_ref, tg_home,
                      r0, r1, c0, c1, work, ll, need, fvec, fwts):
    """massey.rate_selection_day() for one season.

    ``r0`` holds the power fit without the title games, already solved. This
    adds the fit with them, runs the correction on both, and leaves the
    with-title-games rating in ``c1`` and the without in ``c0``. ``loser``
    flags who takes ``c0``.
    """
    for k in range(n_nodes):
        cc_of[k] = -1
        loser[k] = 0
    for i in range(n_cc):
        cc_of[cc_h[i]] = i
        cc_of[cc_a[i]] = i
        loser[cc_a[i] if cc_won[i] == 1 else cc_h[i]] = 1

    if n_cc == 0:
        for k in range(n_nodes):
            need[k] = 1 if k < n_fbs else 0
        _correct(n_g, hn, an, g_at_home, hpts, apts, x_h, x_a, x_home, x_won,
                 cc_h, cc_a, cc_home, cc_won, cc_of, False,
                 tg_ptr, tg_ref, tg_home, n_nodes, r0, r0[n_nodes], corr_sd,
                 corr_passes, gh_t, gh_logw, need, work, c1, ll)
        return

    # The fit with the title games starts from the one without.
    for k in range(n_nodes + 1):
        r1[k] = r0[k]
        prec1[k] = prec0[k]
    for i in range(n_cc):
        for t in (cc_h[i], cc_a[i]):
            short = prior_games - played[t] - 1.0
            prec1[t] = 1.0 / (prior_sd * prior_sd)
            if short > 0.0:
                prec1[t] += short * _INFO_PER_GAME
    _fit(n_g, in_fit, hn, an, g_at_home, gval, x_h, x_a, x_home, x_g,
         cc_h, cc_a, cc_home, cc_g, n_cc,
         minv, prior, prec1, n_nodes, tol, max_iter, r1, start, fvec, fwts)

    for k in range(n_nodes):
        need[k] = 1 if k < n_fbs else 0
    _correct(n_g, hn, an, g_at_home, hpts, apts, x_h, x_a, x_home, x_won,
             cc_h, cc_a, cc_home, cc_won, cc_of, True,
             tg_ptr, tg_ref, tg_home, n_nodes, r1, r1[n_nodes], corr_sd,
             corr_passes, gh_t, gh_logw, need, work, c1, ll)
    _correct(n_g, hn, an, g_at_home, hpts, apts, x_h, x_a, x_home, x_won,
             cc_h, cc_a, cc_home, cc_won, cc_of, False,
             tg_ptr, tg_ref, tg_home, n_nodes, r0, r0[n_nodes], corr_sd,
             corr_passes, gh_t, gh_logw, loser, work, c0, ll)
