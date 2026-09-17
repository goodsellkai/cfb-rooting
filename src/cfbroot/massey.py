"""Massey ratings.

The model follows the method Kenneth Massey documents at
masseyratings.com/theory. His published description is informal, so the shape
is his and the constants are fitted to reproduce his published output. Where a
number was recovered rather than given, the comment says so.

One default departs from him on purpose: mov_weight discounts margin of
victory, because the playoff committee does. MasseyParams(mov_weight=1.0) is
his model as published.

The model has three stages.

1. Game outcome function. Each game's score becomes a number g in [0, 1]: the
   chance the winner would win a rematch under the same conditions. It reads
   both the margin and the total, because a 30-29 game is closer to a coin flip
   than a 10-9 game. Massey publishes eleven sample values and no formula;
   fit_gof() in the tests recovers one that hits all eleven to within 0.003.

2. Power rating. Each team's performance is normally distributed about its
   rating, so P(A beats B) = Phi(rA - rB + home edge). Maximising

       prod over games of  p^g * (1 - p)^(1 - g)

   gives the maximum likelihood ratings and home edge. Preseason ratings enter
   as a prior, which is what keeps the answer sane in September when the
   schedule graph is barely connected. This is the Pwr column on his site.

3. Bayesian win-loss correction. The power rating is built from scores alone,
   so it misses teams that win without winning big. The power fit becomes a
   prior and the actual wins and losses become the likelihood; the posterior
   mean is the overall rating. This is the Rat column, and it is the one that
   moves an 11-2 BYU up and a 9-4 Alabama down.

Margin of victory is what the BCS banned, so rate_season(use_scores=False)
drops stage 1 and feeds g = 1 for a win and 0 for a loss. That is the shape of
what Massey actually submitted to the BCS, and it needs no scores. It is a
blunt switch, though, and it ranked 11-1 Boise State second in 2024. Turning
mov_weight down instead keeps the scoreboard and only flattens it.

Teams outside FBS share one combined rating. Massey rates them individually
because he has their full schedules; the data here only has their games against
FBS teams, one or two each, which cannot support a rating of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.special import log_ndtr, ndtr
from scipy.stats import norm

# Recovered from Massey's eleven published sample values. See tests.
GOF_K = 0.2004
GOF_C = 0.9610
GOF_Q = 0.2668

_CLIP = 8.0        # keep Phi away from 0 and 1 where the logs blow up
# Fisher information a single evenly matched game carries, phi(0)^2 / 0.25.
# Used to express the prior's strength in games rather than in rating units.
INFO_PER_GAME = 0.6366197723675814


@dataclass
class MasseyParams:
    """Everything tunable. The defaults reproduce his published rankings best."""

    gof_k: float = GOF_K
    gof_c: float = GOF_C
    gof_q: float = GOF_Q

    # Prior on the ratings, in rating units (one unit is one standard deviation
    # of a single game's outcome). Wide on purpose: Massey says preseason
    # ratings are "negligible by the end of the year", and fitting against his
    # published finals agrees, preferring the widest prior tried. It is the
    # early-season fit that leans on this, which is the point of having it.
    prior_sd: float = 16.0
    # Floor on the prior, in games. A team is given at least this many games'
    # worth of prior, so a team that has played fewer than this gets pulled
    # toward the prior mean and one with a full schedule is left alone. Massey
    # says preseason ratings exist to "guarantee a unique solution to the
    # equations early in the season", and without this the fit runs away in
    # September: in week 3 of 2026 it fails to converge at all, and ratings
    # reach 2300 on a scale whose full-season spread is about 4.
    prior_games: float = 4.0
    # Home edge. Massey's per-team values sit in a narrow band around 2.2
    # points, which he gets with a strong prior; one shared value is used here.
    hfa_mean: float = 0.17
    hfa_sd: float = 0.10

    # Exponential decay on a game's weight, in weeks. 0 turns it off.
    half_life_weeks: float = 0.0

    # How much of the margin of victory to keep. 1.0 is Massey's own model:
    # the game outcome function's answer is used as it stands. 0.0 shrinks
    # every game to the same value, so a win is a win and the scoreboard only
    # decides who won. In between, each game's outcome value is pulled that
    # far toward the typical winner's value, which flattens blowouts and close
    # calls toward each other without throwing the scoreboard away.
    #
    # This is a deliberate departure from Massey, tuned for agreement with the
    # playoff committee rather than with him. Sweeping it against the
    # committee's last poll before championship weekend, 2023-25, the mean
    # rank error falls from 3.91 places at 1.0 to about 3.15 around 0.4-0.5,
    # and leaving one season out picks 0.40, 0.40 and 0.45, so the gain is not
    # an artefact of fitting three seasons. It costs fidelity to Massey
    # himself: the mean rank error inside his published top 25 goes from 2.65
    # to 4.00. Set it to 1.0 to get his model back.
    mov_weight: float = 0.45
    # What a win shrinks toward. This is the average outcome value of a winning
    # team over a full season, which came out at 0.836, 0.832 and 0.836 in
    # 2025, 2024 and 2023, so it is fixed rather than measured per fit. Keeping
    # it constant means mov_weight means the same thing whatever games it sees.
    mov_flat: float = 0.835

    # Spread of the prior in the win-loss correction, in rating units. A flat
    # spread fits better than scaling each team's by its own standard error.
    #
    # correction_passes re-runs the correction with opponents read at their
    # corrected ratings rather than their power ratings. Massey's ratings are
    # "totally interdependent", solved together, so one pass was wrong. Three
    # passes cut the mean rank error inside his published top 25 from 3.13 to
    # 2.65 places, and on his standings of 1 Dec 2024 it moves an undefeated
    # Oregon from 4th to 1st, which is where he had them. Pushing the spread up
    # instead of iterating does not work: it drifts toward a pure win-loss
    # rating and floats 11-1 Boise State into the top 2.
    correction_abs: float = 0.20
    correction_passes: int = 3
    correction_nodes: int = 81

    max_iter: int = 60
    tol: float = 1e-10


def gof(home_points, away_points, params: MasseyParams | None = None):
    """Massey's game outcome function: a score becomes a rematch probability."""
    p = params or MasseyParams()
    a = np.asarray(home_points, dtype=np.float64)
    b = np.asarray(away_points, dtype=np.float64)
    z = p.gof_k * (a - b) / np.power(a + b + p.gof_c, p.gof_q)
    return ndtr(z)


@dataclass
class MasseyFit:
    """Result of one fit. Ratings are in node order, node j is team fbs_idx[j]."""

    node: np.ndarray            # team index -> node index
    fbs_idx: np.ndarray
    n_nodes: int                # FBS teams plus the combined non-FBS node
    power: np.ndarray           # (n_nodes,) maximum likelihood rating
    rating: np.ndarray          # (n_nodes,) after the win-loss correction
    power_sd: np.ndarray        # (n_nodes,) standard error of the power fit
    hfa: float
    iterations: int
    converged: bool
    params: MasseyParams = field(default_factory=MasseyParams)
    labels: list[str] = field(default_factory=list)   # name of each rated node

    @property
    def n_fbs(self) -> int:
        return int(self.fbs_idx.size)


def build_nodes(is_fbs):
    """FBS teams get their own node, everyone else shares the last one."""
    is_fbs = np.asarray(is_fbs, dtype=bool)
    fbs_idx = np.flatnonzero(is_fbs).astype(np.int32)
    n_fbs = int(fbs_idx.size)
    node = np.full(is_fbs.size, n_fbs, dtype=np.int32)
    node[fbs_idx] = np.arange(n_fbs, dtype=np.int32)
    return node, fbs_idx, n_fbs + 1


def fit_power(h_node, a_node, at_home, g, weight, n_nodes,
              prior_mean=None, params: MasseyParams | None = None):
    """Maximum likelihood ratings and home edge, by Fisher scoring.

    Returns ``(rating, hfa, standard_error, iterations, converged)``.
    """
    p = params or MasseyParams()
    n = n_nodes + 1                      # the trailing unknown is the home edge
    h_node = np.asarray(h_node, dtype=np.int64)
    a_node = np.asarray(a_node, dtype=np.int64)
    at_home = np.asarray(at_home, dtype=bool)
    g = np.asarray(g, dtype=np.float64)
    weight = np.asarray(weight, dtype=np.float64)
    hv = at_home.astype(np.float64)

    r0 = (np.zeros(n_nodes) if prior_mean is None
          else np.asarray(prior_mean, dtype=np.float64).copy())
    x = np.concatenate([r0, [p.hfa_mean]])
    played = np.zeros(n_nodes)
    np.add.at(played, h_node, weight)
    np.add.at(played, a_node, weight)
    prec = np.zeros(n)                   # prior precision, added to the diagonal
    prec[:n_nodes] = (1.0 / p.prior_sd ** 2
                      + np.maximum(0.0, p.prior_games - played) * INFO_PER_GAME)
    prec[n_nodes] = 1.0 / p.hfa_sd ** 2
    centre = np.concatenate([r0, [p.hfa_mean]])

    converged = False
    it = 0
    hess = np.zeros((n, n))
    for it in range(1, p.max_iter + 1):
        d = np.clip(x[h_node] - x[a_node] + x[n_nodes] * hv, -_CLIP, _CLIP)
        cdf = ndtr(d)
        pdf = norm.pdf(d)
        # score of one game with respect to the rating gap
        s = weight * pdf * (g / np.maximum(cdf, 1e-12)
                            - (1.0 - g) / np.maximum(1.0 - cdf, 1e-12))
        # Fisher information of one game, which keeps the Hessian definite
        w = weight * pdf ** 2 / np.maximum(cdf * (1.0 - cdf), 1e-12)

        grad = np.zeros(n)
        np.add.at(grad, h_node, s)
        np.add.at(grad, a_node, -s)
        grad[n_nodes] = float((s * hv).sum())
        grad -= prec * (x - centre)

        hess[:] = 0.0
        np.add.at(hess, (h_node, h_node), w)
        np.add.at(hess, (a_node, a_node), w)
        np.add.at(hess, (h_node, a_node), -w)
        np.add.at(hess, (a_node, h_node), -w)
        wh = w * hv
        np.add.at(hess, (h_node, n_nodes), wh)
        np.add.at(hess, (n_nodes, h_node), wh)
        np.add.at(hess, (a_node, n_nodes), -wh)
        np.add.at(hess, (n_nodes, a_node), -wh)
        hess[n_nodes, n_nodes] = float((w * hv).sum())
        hess[np.diag_indices(n)] += prec

        step = np.linalg.solve(hess, grad)
        x = x + step
        if np.abs(step).max() < p.tol:
            converged = True
            break

    sd = np.sqrt(np.clip(np.diag(np.linalg.inv(hess)), 0.0, None))
    return x[:n_nodes], float(x[n_nodes]), sd[:n_nodes], it, converged


def win_loss_correction(power, hfa, h_node, a_node, at_home, outcome, weight,
                        params: MasseyParams | None = None):
    """Massey's Bayesian step: posterior mean given how the games went.

    The power fit is the prior. Each team's results against its opponents (held
    at their power ratings) are the likelihood. Integrating gives a rating that
    rewards winning however it is done.

    ``outcome`` is the game outcome value from the home team's side; only which
    way it fell is used here, so this stage sees wins and losses and nothing
    about the scores.

    That makes the stage asymmetric on purpose. The probit log-likelihood of a
    loss grows as the square of the rating gap, so losing to a weak team costs
    far more than losing to a strong one, while beating a weak team is worth
    close to nothing. Softening the outcome toward the damped value the power
    fit uses was tried and measured worse at every setting: against the
    committee's last poll before championship weekend over 2023-25 the mean
    rank error rose from 3.13 places to 3.40 at a quarter strength and 4.21 at
    full. The harshness is carrying real information, so it stays.
    """
    p = params or MasseyParams()
    n = power.size
    outcome = np.asarray(outcome, dtype=np.float64)
    hv = np.asarray(at_home, dtype=bool).astype(np.float64)
    weight = np.asarray(weight, dtype=np.float64)

    spread = np.full(n, p.correction_abs)
    offs = np.linspace(-4.0, 4.0, p.correction_nodes)
    # log of the prior, one row per team, shared grid in units of its own spread
    log_prior = -0.5 * offs ** 2
    grid = power[:, None] + spread[:, None] * offs[None, :]
    loglik = np.zeros_like(grid)

    for side, (own, opp) in enumerate(((h_node, a_node), (a_node, h_node))):
        own = np.asarray(own, dtype=np.int64)
        opp = np.asarray(opp, dtype=np.int64)
        # Everything in the game except this team's own rating. The home edge
        # helps the home team and hurts the away team, hence the sign.
        edge = hfa * hv if side == 0 else -hfa * hv
        base = -power[opp] + edge
        won = outcome > 0.5 if side == 0 else outcome < 0.5
        for i in range(own.size):
            t = own[i]
            d = np.clip(grid[t] + base[i], -_CLIP, _CLIP)
            loglik[t] += weight[i] * (log_ndtr(d) if won[i] else log_ndtr(-d))

    total = log_prior[None, :] + loglik
    total -= total.max(axis=1, keepdims=True)
    post = np.exp(total)
    post /= post.sum(axis=1, keepdims=True)
    return (post * grid).sum(axis=1)


def _corrected(power, hfa, h, a, at_home, outcome, weight, params):
    """Run the correction, optionally re-reading opponents at their new rating."""
    out = power
    for _ in range(max(1, params.correction_passes)):
        out = win_loss_correction(out, hfa, h, a, at_home, outcome, weight,
                                  params)
    return out


def fit(is_fbs, g_home, g_away, g_neutral, home_points=None, away_points=None,
        home_won=None, week=None, prior_mean=None,
        params: MasseyParams | None = None) -> MasseyFit:
    """Fit both stages to one set of games.

    Pass scores for the published Massey rating. Pass ``home_won`` alone for the
    BCS-legal version, which has no margin of victory in it.
    """
    p = params or MasseyParams()
    node, fbs_idx, n_nodes = build_nodes(is_fbs)
    h = node[np.asarray(g_home, dtype=np.int64)].astype(np.int64)
    a = node[np.asarray(g_away, dtype=np.int64)].astype(np.int64)
    at_home = ~np.asarray(g_neutral, dtype=bool)

    if home_points is not None and away_points is not None:
        hp = np.asarray(home_points, dtype=np.float64)
        ap = np.asarray(away_points, dtype=np.float64)
        g = gof(hp, ap, p)
        won = hp > ap
        if p.mov_weight < 1.0:
            # Pull every game toward a generic win, keeping only mov_weight of
            # what the scoreboard said.
            target = np.where(won, p.mov_flat, 1.0 - p.mov_flat)
            g = p.mov_weight * g + (1.0 - p.mov_weight) * target
    else:
        if home_won is None:
            raise ValueError("pass scores or home_won")
        won = np.asarray(home_won, dtype=bool)
        g = won.astype(np.float64)

    weight = np.ones(h.size, dtype=np.float64)
    if p.half_life_weeks > 0 and week is not None:
        wk = np.asarray(week, dtype=np.float64)
        weight = 0.5 ** ((wk.max() - wk) / p.half_life_weeks)

    prior = None
    if prior_mean is not None:
        prior = np.zeros(n_nodes)
        prior[:fbs_idx.size] = np.asarray(prior_mean, dtype=np.float64)

    power, hfa, sd, it, ok = fit_power(h, a, at_home, g, weight, n_nodes,
                                       prior, p)
    rating = _corrected(power, hfa, h, a, at_home, g, weight, p)
    return MasseyFit(node=node, fbs_idx=fbs_idx, n_nodes=n_nodes, power=power,
                     rating=rating, power_sd=sd, hfa=hfa, iterations=it,
                     converged=ok, params=p)


def title_game_week(state) -> int | None:
    """The week the conference title games are in, if they are scheduled."""
    weeks = [g["week"] for g in state.games if g["is_ccg"]]
    return min(weeks) if weeks else None


def season_games(state, include_ccg: bool = False, through_week: int | None = None):
    """The finished games a rating should see."""
    return [g for g in state.games
            if g["status"] != 0 and (include_ccg or not g["is_ccg"])
            and (through_week is None or g["week"] <= through_week)]


def _extra_arrays(state, extra_games, through_week):
    """Fold FCS games into the team index, adding teams that are not in it."""
    from .data.season import normalise_name   # late: season.py imports this one

    by_key = {normalise_name(t.school): t.idx for t in state.teams}
    names = [t.school for t in state.teams]
    rows = []
    for g in extra_games or []:
        hp, ap = g.get("home_points"), g.get("away_points")
        if not g.get("completed") or hp is None or ap is None or hp == ap:
            continue
        week = int(g.get("week") or 0)
        if through_week is not None and week > through_week:
            continue
        idxs = []
        for side in ("home", "away"):
            name = g.get(f"{side}_team") or ""
            key = normalise_name(name)
            if key not in by_key:
                by_key[key] = len(names)
                names.append(name)
            idxs.append(by_key[key])
        rows.append((idxs[0], idxs[1], bool(g.get("neutral_site")), hp, ap, week))
    return names, rows


def fit_season(state, include_ccg: bool = False, through_week: int | None = None,
               use_scores: bool = True, prior_from_rating: bool = False,
               extra_games=None, min_games: int = 4,
               params: MasseyParams | None = None) -> MasseyFit | None:
    """Fit a SeasonState's finished games.

    ``use_scores=False`` gives the BCS-legal win-loss-only rating.
    ``prior_from_rating`` seeds the prior with each team's published rating,
    which is what keeps early-season numbers from running wild.
    ``extra_games`` takes raw FCS payloads so the fit covers all of Division I
    the way Massey's does; without them every non-FBS opponent shares one
    rating, which costs about three places of accuracy per team.
    """
    p = params or MasseyParams()
    games = season_games(state, include_ccg, through_week)
    if not games:
        return None

    names, extra = _extra_arrays(state, extra_games, through_week)
    h = [g["home_idx"] for g in games] + [r[0] for r in extra]
    a = [g["away_idx"] for g in games] + [r[1] for r in extra]
    neutral = [g["neutral"] for g in games] + [r[2] for r in extra]
    week = [g["week"] for g in games] + [r[5] for r in extra]

    have_scores = all(g["home_points"] is not None and g["away_points"] is not None
                      for g in games)
    if use_scores and have_scores:
        hp = [g["home_points"] for g in games] + [r[3] for r in extra]
        ap = [g["away_points"] for g in games] + [r[4] for r in extra]
        won = None
    else:
        hp = ap = None
        won = ([g["status"] == 1 for g in games]
               + [r[3] > r[4] for r in extra])

    # Rate a team on its own if it has a real schedule here. Everyone else --
    # a Division II team that showed up once -- shares the last node.
    played = np.zeros(len(names), dtype=np.int64)
    np.add.at(played, np.asarray(h), 1)
    np.add.at(played, np.asarray(a), 1)
    rated = played >= min_games
    for t in state.teams:
        if t.is_fbs:
            rated[t.idx] = True

    prior = None
    if prior_from_rating:
        prior = np.zeros(int(rated.sum()))
        pts = np.array([state.teams[i].rating for i in np.flatnonzero(rated)
                        if i < len(state.teams) and state.teams[i].is_fbs])
        if pts.size:
            centre, scale = pts.mean(), float(state.params.sigma)
            for j, i in enumerate(np.flatnonzero(rated)):
                if i < len(state.teams) and state.teams[i].is_fbs:
                    prior[j] = (state.teams[i].rating - centre) / scale

    f = fit(rated, h, a, neutral, home_points=hp, away_points=ap, home_won=won,
            week=week, prior_mean=prior, params=p)
    f.labels = [names[int(i)] for i in f.fbs_idx]
    return f


def rate_season(state, include_ccg: bool = False, through_week: int | None = None,
                use_scores: bool = True, which: str = "rating",
                **kwargs) -> dict[str, float]:
    """Massey rating per FBS school. ``which`` picks "rating" or "power"."""
    f = fit_season(state, include_ccg, through_week, use_scores, **kwargs)
    if f is None:
        return {}
    vals = f.rating if which == "rating" else f.power
    n_state = len(state.teams)
    return {f.labels[j]: float(vals[j]) for j in range(f.n_fbs)
            if int(f.fbs_idx[j]) < n_state and state.teams[int(f.fbs_idx[j])].is_fbs}


# A cheap stand-in for use inside the Monte Carlo

@dataclass
class LinearSystem:
    """Least squares on win-loss, the linear approximation to fit_power.

    The maximum likelihood fit above needs Newton iterations, which is far too
    slow to run once per simulated season. Replacing the probit link with a
    straight line turns it into one linear solve whose matrix depends only on
    the schedule, so the inverse is built once and each season costs a single
    matrix-vector product. It is not the Massey rating and does not claim to be
    -- see the accuracy note in the tests for how far apart they are.
    """

    node: np.ndarray
    fbs_idx: np.ndarray
    n_nodes: int
    normal: np.ndarray
    solve: np.ndarray
    g_hnode: np.ndarray
    g_anode: np.ndarray
    g_at_home: np.ndarray

    @property
    def n_fbs(self) -> int:
        return int(self.fbs_idx.size)

    def rhs(self, home_won) -> np.ndarray:
        s = np.where(np.asarray(home_won, dtype=bool), 1.0, -1.0)
        b = np.zeros(self.n_nodes + 1, dtype=np.float64)
        np.add.at(b, self.g_hnode, s)
        np.add.at(b, self.g_anode, -s)
        b[self.n_nodes] = float(s[self.g_at_home].sum())
        return b

    def ratings(self, home_won) -> np.ndarray:
        return self.solve @ self.rhs(home_won)

    def home_field(self, home_won) -> float:
        full = np.linalg.pinv(self.normal, rcond=1e-10) @ self.rhs(home_won)
        return float(full[self.n_nodes])


def build_linear(is_fbs, g_home, g_away, g_neutral) -> LinearSystem:
    """Build and invert the least squares normal equations for one schedule."""
    node, fbs_idx, n_nodes = build_nodes(is_fbs)
    h = node[np.asarray(g_home, dtype=np.int64)].astype(np.int64)
    a = node[np.asarray(g_away, dtype=np.int64)].astype(np.int64)
    at_home = ~np.asarray(g_neutral, dtype=bool)
    hf = n_nodes
    n = n_nodes + 1

    normal = np.zeros((n, n), dtype=np.float64)
    np.add.at(normal, (h, h), 1.0)
    np.add.at(normal, (a, a), 1.0)
    np.add.at(normal, (h, a), -1.0)
    np.add.at(normal, (a, h), -1.0)
    w = at_home.astype(np.float64)
    np.add.at(normal, (h, hf), w)
    np.add.at(normal, (hf, h), w)
    np.add.at(normal, (a, hf), -w)
    np.add.at(normal, (hf, a), -w)
    normal[hf, hf] = float(w.sum())

    solve = np.ascontiguousarray(np.linalg.pinv(normal, rcond=1e-10)[:fbs_idx.size])
    return LinearSystem(node=node, fbs_idx=fbs_idx, n_nodes=n_nodes,
                        normal=normal, solve=solve,
                        g_hnode=h, g_anode=a, g_at_home=at_home)


# Conference title games

def title_game_results(state, through_week: int | None = None):
    """Who won and who lost each conference title game that has been played."""
    won, lost = set(), set()
    for g in state.games:
        if not g["is_ccg"] or g["status"] == 0:
            continue
        if through_week is not None and g["week"] > through_week:
            continue
        w, l = ((g["home"], g["away"]) if g["status"] == 1
                else (g["away"], g["home"]))
        won.add(w)
        lost.add(l)
    return won, lost


def rate_selection_day(state, extra_games=None, params: MasseyParams | None = None,
                       **kwargs) -> tuple[dict[str, float], dict]:
    """Massey rating once the title games are in, counted one way only.

    A title game is added to the winner's record and left out of the loser's, so
    reaching one can only help. One game cannot be present for one team and
    absent for the other inside a single fit, so this runs two: everyone takes
    the fit that includes title games, except the teams that lost one, who take
    the fit that stops at the end of the regular season.

    The two fits differ by about ten games in seventeen hundred, so they sit on
    effectively the same scale. The returned diagnostics say how far apart they
    actually are for the teams that played no title game, which is the check
    that mixing them is safe.
    """
    ccg_week = title_game_week(state)
    through = None if ccg_week is None else ccg_week
    with_ccg = fit_season(state, include_ccg=True, through_week=through,
                          extra_games=extra_games, params=params, **kwargs)
    without = fit_season(state, include_ccg=False, through_week=through,
                         extra_games=extra_games, params=params, **kwargs)
    if with_ccg is None or without is None:
        return {}, {}

    n_state = len(state.teams)

    def as_dict(f):
        return {f.labels[j]: float(f.rating[j]) for j in range(f.n_fbs)
                if int(f.fbs_idx[j]) < n_state
                and state.teams[int(f.fbs_idx[j])].is_fbs}

    full, base = as_dict(with_ccg), as_dict(without)
    won, lost = title_game_results(state, through)

    untouched = [t for t in full if t in base and t not in won and t not in lost]
    drift = np.array([full[t] - base[t] for t in untouched]) if untouched else np.zeros(1)
    spread = np.ptp(list(full.values())) if full else 1.0

    merged = {t: (base[t] if t in lost and t in base else v)
              for t, v in full.items()}
    return merged, {
        "title_game_winners": len(won), "title_game_losers": len(lost),
        "bystanders": len(untouched),
        "max_drift": float(np.abs(drift).max()),
        "mean_drift": float(np.abs(drift).mean()),
        "rating_spread": float(spread),
    }
