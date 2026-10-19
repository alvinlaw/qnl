# Writing a plugin

The framework exposes five extension points. A plugin is an ordinary Python
package that imports `qnl`, registers itself at import time, and exposes an
entry point that runs the framework CLI after registering:

```python
# myplugin/__init__.py
from myplugin import signal, book           # noqa: F401 -- import registers
```

```python
# myplugin/service.py
import myplugin  # noqa: F401
from qnl import service


def main(argv=None):
    return service.cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
```

```bash
python -m myplugin.service tick --config my.toml
```

Dependency direction is one-way: your plugin imports `qnl`; the framework never
imports your plugin.

## 1. A signal family

A signal is a pure function of the data bundle the framework hands it. It must
be causal: bar `i` may use bars `<= i` only.

```python
from qnl import backtest, metrics, signals, strategies


def build(params, fee):
    decide = strategies.build(("sma_cross", tuple(params)))
    return lambda bundle: metrics.equity_returns(
        backtest.run(bundle["price"], decide, fee=fee))


signals.register(
    "my_sma", build,
    fmt=lambda p: f"my_sma({p[0]},{p[1]})",
    hint="fast int, slow int",
    defaults=[("my_sma", (10, 50))],
)
```

`defaults` feeds the offline sweep and `walk_forward --signal`. The ledger
records every evaluated `(family, params)` cell, so do not register overlapping
grids for the same idea.

## 2. A data plane

A plane returns one list aligned to the price grid, newest last, using only
data with `close_time <= asof`.

```python
from qnl import sources, store


def _plane(con, symbol, asof):
    return [v for _t, v in store.load_series_asof(con, symbol, "my_metric", asof)]


sources.register_plane("my_metric", _plane, '"my_metric" : one float per bar')
```

Fill it with `store.upsert_series(con, [(symbol, "my_metric", close_time, value)])`.
The plane is then present in every bundle and available to codegen prompts.

## 3. A live book kind

A live book freezes a causal return series and marks it forward. The series must
be prefix-invariant: `rets[:n]` may not change when new bars arrive.

```python
from qnl import live
from myplugin import my_series


def _series(con, spec, fee, asof):
    return my_series(con, spec["symbol"], fee=fee, asof=asof)


live.register_kind("my_book", _series)
```

Freeze with `live.freeze(con, book, "my_book", spec, fee, asof)`. Paper books
reuse the same providers: `paper.seed(con, book, "my_book", spec, fee)`.

## 4. A planner task

`plan` runs every cycle; `run` executes one task. Keep `plan` cheap and
deterministic.

```python
from qnl import planner


def plan(con, symbols, asof):
    return [{"kind": "mine", "symbol": s} for s in symbols]


def run(con, task, gen, asof):
    ...
    return True


planner.register_task("mine", plan, run, order=50)
```

Lower `order` runs first. A failed task is logged and skipped; it never aborts
the cycle.

## 5. A service stage

```python
from qnl import service


def stage(con, cfg, asof):
    ...
    return "detail for the runs table"


service.register_stage("mine", stage, order=60, network=False)
```

`network=True` stages are recorded as `skipped` when `tick --no-network` runs.

## Provenance

Register any module that affects a return series with the trials ledger:

```python
from qnl import trials
trials.add_code_files("/abs/path/to/myplugin/model.py")
```

The code hash recorded with each trial then covers it.
