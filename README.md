# cfbroot

Which games this week help your team reach the College Football Playoff, and
by how much.

**cfbroot.com**

The site simulates the rest of the season 2.5 million times and reports, for
every remaining game, how much each result moves your team's odds. It is
rebuilt hourly on game days.

## The idea

Each simulated season plays out every unplayed game, rates all 138 FBS teams
on the results, runs a model of the selection committee, and picks and plays
the 12-team bracket. Nothing about a season depends on which team you asked
about, so one set of simulations answers for every team.

The same run also answers what each game is worth. Unplayed games are
simulated independently, so splitting the seasons by who won one game gives
that game's effect: the odds in the seasons where the home team won, against
the odds in the seasons where it lost. Both halves share the other games'
draws, so their difference is less noisy than two separate runs would be.

## Game outcomes

A game's margin is drawn from

    margin ~ Normal(rating gap + home edge, sigma)

with a home edge of 2.75 points and sigma 13.86 points, both measured against
closing betting lines and past seasons. The rating gap comes from ESPN's FPI.

FPI is not exactly right about a team, and its error persists: a team it
misjudges in September is misjudged in November. So each simulated season
draws one rating error per team and uses it for that team's whole season:

    rating error(w)^2 = 4.68^2 + (7.18^2 - 4.68^2) * exp(-w / 4.60)

with w the weeks of results FPI has seen. Measured over 2023-25, that error
starts near 7.2 points and settles near 4.7 once a few weeks are played, while
the game noise holds between 13.5 and 14 all season. Fitted on two seasons and
tested on the third, the curve beat a constant error in every case tried.

Drawing the margin rather than flipping a weighted coin leaves each game's win
probability unchanged, since it is the distribution that probability came from.
The total is then drawn around the margin, averaging 50.2 points plus 0.18 per
point of margin, and the two scores fall out of the margin and the total. A
drawn score lands on a total football actually produces, so 1 and 4 never come
up and 24, 17 and 31 come up most.

## Rating a simulated season

Every simulated season is rated from scratch with Massey's model, the same way
the real one is.

**Game outcome value.** Each game becomes a number in (0, 1) from the margin
and the total points:

    gof = Phi(k * (home points - away points) / (total points + c)^q)

**Power rating.** The ratings are the maximum likelihood fit of

    gof(i vs j) ~ Phi(r_i - r_j + home edge)

solved by Newton's method. A wide normal prior (sd 16) keeps the September fit
from running away, and a floor of four games' worth of prior information pulls
teams that have barely played toward the middle.

**Win-loss correction.** Massey's second stage is Bayesian: each team's power
rating is the prior, its wins and losses against opponents held at their own
ratings are the likelihood, and the posterior mean is the rating. The integral
is a 10-point Gauss-Hermite quadrature, repeated three times so the ratings are
solved together rather than in one pass over fixed opponents. This stage is
asymmetric on purpose: the log-likelihood of a loss grows as the square of the
rating gap, so losing to a weak team costs far more than losing to a strong one.

**Margin discount.** The committee does not reward blowouts the way a pure
margin model does, so each game's outcome value is pulled 55% of the way toward
a generic win. Swept against the committee's polls, that discount cuts the mean
rank error from 3.9 places to about 3.2. It costs fidelity to Massey himself,
whose model keeps the full margin.

**Title games.** Selection day runs two fits, one with the conference title
games and one without. Everyone takes the first except the teams that lost a
title game, who take the second, so reaching a title game can only help.

## The committee

The rating is not the ranking. Four things happen on top of it:

1. **Noise.** A nudge of 0.05 rating units per team, drawn once per season,
   stands in for the committee's own variability. The committee's polls sit
   2.99 places from the model's ranking, which would justify a much larger
   number, but a third of that gap is predictable bias rather than noise, so
   the smaller figure is used.
2. **Worst loss and best win.** A team whose worst defeat came against a
   highly ranked team is treated better than one that lost to nobody in
   particular, and a team that beat a highly ranked team better than one that
   did not. Both are worth up to 0.02, decaying as exp(-(place - 1) / 25).
3. **Head to head.** A team directly below one it beat swaps with it.
4. **Title game jump.** A title game winner sitting just behind the team it
   beat moves ahead of it, when the ratings are within 0.1.

## Conference races and the playoff field

Conference standings go by conference winning percentage, and ties run through
each conference's own published steps: head to head, records against common
opponents, records against common opponents from the top of the standings
down, opponents' conference records, and so on. The model's rating stands in
for the last step, which is otherwise a SportSource rating, a CFP ranking or a
computer composite. The Sun Belt still plays in divisions and sends its two
division winners; everyone else sends its top two. Group of Six title games
other than the MAC's are played at the higher seed's stadium.

The 2026 field is the four power conference champions, the highest ranked team
from the other six conferences whether or not it won its conference, Notre
Dame if it is ranked in the top 12, and the rest at large. Seeding is straight
off the ranking and the top four seeds get byes. Earlier seasons ran different
rules, which the code keeps for backtests.

## Turning simulations into a guide

For each remaining game and each metric, the guide reports the probability if
the home team wins, the probability if it loses, and the difference. Each
probability is a Wilson interval; each difference is a Newcombe interval with
a two-proportion p-value.

Every team's guide tests hundreds of games at once, so the p-values go through
Benjamini-Hochberg false discovery rate control at q = 0.05, both within a
week and across the rest of the season. A swing survives as a real one, is
marked as a direction the simulations lean toward, or is called too thin to
tell.

## Sim a season

The site also plays out single simulated seasons: every score week by week,
the standings, the title games, the committee's ranking, selection day and
the bracket. Each one is a draw from the same distribution the odds come
from, rated the same way.

## How well it does

Backtested against every committee poll from 2023-25, from the first poll
through selection day, the model's ranking sits 2.9 places from the
committee's on average, and its playoff field contains 23 of the 28 teams the
committee actually picked. The disagreements are mostly one kind: the model
rates teams with fewer losses from weaker leagues higher than the committee
does, and 3-loss teams from the SEC and ACC lower.

Parameters are calibrated once and not re-fit during a season. Where a choice
was tuned on 2023-25, it was checked by leaving one season out and scoring the
one left out.
