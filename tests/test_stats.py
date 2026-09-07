import numpy as np
import pytest
from scipy import stats as sps

from cfbroot.stats import (Estimate, benjamini_hochberg, mc_stderr,
                           newcombe_diff_ci, prop_diff_pvalue,
                           sims_for_resolution, wilson_ci)


def test_wilson_matches_textbook_value():
    lo, hi = wilson_ci(50, 100, 0.05)
    assert lo == pytest.approx(0.4038, abs=1e-4)
    assert hi == pytest.approx(0.5962, abs=1e-4)


def test_wilson_stays_inside_unit_interval_at_the_boundary():
    for k, n in [(0, 1000), (1000, 1000), (0, 5), (1, 1)]:
        lo, hi = wilson_ci(k, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_narrows_as_the_square_root_of_n():
    w1 = np.subtract(*reversed(wilson_ci(500, 1000)))
    w2 = np.subtract(*reversed(wilson_ci(50_000, 100_000)))
    assert w1 / w2 == pytest.approx(10.0, rel=0.02)


def test_newcombe_interval_contains_the_point_estimate():
    d, lo, hi = newcombe_diff_ci(600, 1000, 500, 1000)
    assert lo < d < hi
    assert d == pytest.approx(0.1)


def test_newcombe_covers_the_truth_at_the_nominal_rate():
    """Empirical coverage of the interval should be at least 95%."""
    rng = np.random.default_rng(0)
    p1, p2, n = 0.30, 0.25, 400
    k1 = rng.binomial(n, p1, size=4000)
    k2 = rng.binomial(n, p2, size=4000)
    _, lo, hi = newcombe_diff_ci(k1, n, k2, n)
    covered = np.mean((lo <= p1 - p2) & (p1 - p2 <= hi))
    assert covered >= 0.95


def test_newcombe_handles_a_tiny_arm_without_exploding():
    d, lo, hi = newcombe_diff_ci(1, 20, 5000, 100_000)
    assert -1.0 <= lo <= d <= hi <= 1.0
    assert hi - lo > 0.05  # a 20-simulation arm must stay visibly uncertain


def test_pvalue_agrees_with_scipy_two_proportion_z():
    k1, n1, k2, n2 = 620, 1000, 550, 1000
    got = prop_diff_pvalue(k1, n1, k2, n2)
    pooled = (k1 + k2) / (n1 + n2)
    se = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (k1 / n1 - k2 / n2) / se
    assert got == pytest.approx(2 * sps.norm.sf(abs(z)))


def test_pvalue_is_one_when_the_proportions_are_identical():
    assert prop_diff_pvalue(500, 1000, 500, 1000) == pytest.approx(1.0)


def test_bh_rejects_nothing_under_the_null():
    """Uniform p-values are the null; BH should almost never fire."""
    rng = np.random.default_rng(1)
    p = rng.uniform(size=500)
    rejected, _ = benjamini_hochberg(p, 0.05)
    assert rejected.sum() <= 2


def test_bh_is_monotone_and_bounded():
    p = np.array([0.001, 0.01, 0.02, 0.2, 0.5, 0.99])
    rejected, q = benjamini_hochberg(p, 0.05)
    assert np.all(np.diff(q[np.argsort(p)]) >= -1e-12)
    assert np.all((q >= 0) & (q <= 1))
    assert rejected[0]


def test_bh_is_less_permissive_than_uncorrected_testing():
    rng = np.random.default_rng(2)
    p = np.concatenate([rng.uniform(size=300), np.full(5, 1e-6)])
    rejected, _ = benjamini_hochberg(p, 0.05)
    assert rejected.sum() < (p < 0.05).sum()
    assert rejected.sum() >= 5  # but it still finds the real signals


def test_sims_for_resolution_scales_with_the_inverse_square_of_delta():
    a = sims_for_resolution(0.02, 0.5)
    b = sims_for_resolution(0.01, 0.5)
    assert b / a == pytest.approx(4.0, rel=1e-9)


def test_mc_stderr_matches_the_binomial_formula():
    assert mc_stderr(0.25, 10_000) == pytest.approx(np.sqrt(0.25 * 0.75 / 10_000))


def test_estimate_from_counts_round_trips():
    e = Estimate.from_counts(250, 1000)
    assert e.p == 0.25 and e.n == 1000 and e.lo < 0.25 < e.hi
