"""Signal registry: a signal maps a data bundle -> a return series.

Contract: signal(data) -> returns, where data = sources.load(...). The gate only
ever sees returns, so it is blind to which plane a signal reads. A plugin adds a
family with register(); the gate, ledger and decay logic never change.
"""
from qnl import backtest, metrics, strategies

# Mutable on purpose: registration happens at import time (public defaults below,
# private plugins later), and existing callers read these directly.
HINTS = {}
FAMILIES = set()
_BUILD = {}
_FMT = {}
_DEFAULTS = []


def register(name, build, fmt, hint, defaults=()):
    """Register one family.

    build(params, fee) -> fn(data) -> returns
    fmt(params)        -> label used in the ledger
    defaults           -> iterable of (name, params) cells to sweep by default
    """
    HINTS[name] = hint
    FAMILIES.add(name)
    _BUILD[name] = build
    _FMT[name] = fmt
    _DEFAULTS.extend(defaults)
    return name


def _price(name):
    def build(params, fee):
        decide = strategies.build((name, tuple(params)))
        return lambda d: metrics.equity_returns(
            backtest.run(d["price"], decide, fee=fee))
    return build


_HINTS = {
    "sma_cross": "fast int, slow int (1<fast<slow)",
    "momentum": "lookback int (bars)",
    "mean_revert": "window int, z float (sd below trailing mean)",
    "vol_target": "window int, target_annual float in 0..1",
}
for _name, _hint in _HINTS.items():
    register(_name, _price(_name),
             lambda params, name=_name: strategies.fmt((name, tuple(params))),
             _hint,
             [s for s in strategies.default_specs() if s[0] == _name])


def build(name, params, fee=0.0):
    """(name, params) -> fn(data) -> returns."""
    if name not in _BUILD:
        raise KeyError(f"unknown signal {name!r}")
    return _BUILD[name](params, fee)


def fmt(name, params):
    if name not in _FMT:
        raise KeyError(f"unknown signal {name!r}")
    return _FMT[name](tuple(params))


def candidates():
    """The default sweep across every registered family."""
    return list(_DEFAULTS)
