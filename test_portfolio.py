"""
test_portfolio.py — sanity tests for portfolio_analyzer.py pure maths.

Known weights/covariances with analytic answers, so the risk decomposition can't
silently drift. Runs standalone or under pytest.
"""

import numpy as np
import pandas as pd

import portfolio_analyzer as P


def test_effective_n_equal_weights():
    w = np.array([0.25, 0.25, 0.25, 0.25])
    assert abs(P.effective_n(w) - 4.0) < 1e-9
    assert abs(P.hhi(w) - 0.25) < 1e-9


def test_portfolio_vol_identity_cov():
    # Equal weights, identity covariance (vol 1 each, uncorrelated): port var = sum w^2.
    w = np.array([0.5, 0.5])
    cov = np.eye(2)
    assert abs(P.portfolio_vol(w, cov) - np.sqrt(0.5)) < 1e-9


def test_risk_contributions_sum_to_one():
    rng = np.random.default_rng(0)
    a = rng.normal(size=(5, 5))
    cov = a @ a.T                                   # PSD covariance
    w = np.full(5, 0.2)
    rc = P.risk_contributions(w, cov)
    assert abs(rc.sum() - 1.0) < 1e-9


def test_inverse_vol_downweights_risky():
    vols = np.array([0.10, 0.20, 0.40])
    w = P.inverse_vol_weights(vols)
    assert w[0] > w[1] > w[2]                       # calmest gets the most weight
    assert abs(w.sum() - 1.0) < 1e-9


def test_avg_pairwise_corr_perfect():
    corr = pd.DataFrame([[1.0, 1.0], [1.0, 1.0]])
    assert abs(P.avg_pairwise_corr(corr) - 1.0) < 1e-9


def test_diversification_ratio_ge_one_when_uncorrelated():
    cov = np.diag([0.04, 0.04])                     # vols 0.2, uncorrelated
    vols = np.array([0.2, 0.2])
    w = np.array([0.5, 0.5])
    assert P.diversification_ratio(w, vols, cov) > 1.0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
