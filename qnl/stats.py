"""Pure statistics for the deflation gate (stdlib only)."""
import math
from statistics import NormalDist

_ND = NormalDist()
EULER = 0.5772156649015329  # Euler-Mascheroni


def sharpe_period(rets):
    """Per-period (not annualized) Sharpe: mean / sd."""
    n = len(rets)
    if n < 2:
        return 0.0
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (n - 1))
    return m / sd if sd > 0 else 0.0


def _moments(rets):
    """Population mean, sd, skewness, Pearson kurtosis (normal = 3)."""
    n = len(rets)
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / n)
    if sd == 0:
        return m, 0.0, 0.0, 3.0
    skew = sum((r - m) ** 3 for r in rets) / n / sd ** 3
    kurt = sum((r - m) ** 4 for r in rets) / n / sd ** 4
    return m, sd, skew, kurt


def expected_max_sr(var_sr, n_trials):
    """E[max Sharpe] under the null across n_trials independent trials."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    a = _ND.inv_cdf(1 - 1.0 / n_trials)
    b = _ND.inv_cdf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_sr) * ((1 - EULER) * a + EULER * b)


def dsr(rets, sr0, n_trials, var_sr):
    """Deflated Sharpe Ratio: P(true per-period SR > sr0) after n_trials.

    Bailey & Lopez de Prado (2014), non-normal returns. sr0 is a per-period
    Sharpe benchmark; the bar actually used is max(sr0, expected max under null).
    """
    n = len(rets)
    if n < 2:
        return 0.0
    _, sd, skew, kurt = _moments(rets)
    if sd == 0:
        return 0.0
    sr = (sum(rets) / n) / sd
    bar = max(sr0, expected_max_sr(var_sr, n_trials))
    denom = math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    if denom <= 0:
        return 0.0
    return _ND.cdf((sr - bar) * math.sqrt(n - 1) / denom)
