"""Return / risk metrics. Pure, no I/O."""
import math

TRADING_DAYS = 365  # crypto trades every day


def equity_returns(equity):
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]


def max_drawdown(equity):
    peak = equity[0]
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return mdd


def annualized_sharpe(rets, periods_per_year=TRADING_DAYS):
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return mean / sd * math.sqrt(periods_per_year) if sd > 0 else 0.0


def cagr(equity, periods_per_year=TRADING_DAYS):
    years = len(equity) / periods_per_year
    return (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 else 0.0


def report(equity, periods_per_year=TRADING_DAYS):
    return {
        "bars": len(equity),
        "total_return": equity[-1] / equity[0] - 1,
        "cagr": cagr(equity, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "sharpe": annualized_sharpe(equity_returns(equity), periods_per_year),
    }
