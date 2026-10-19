# qnl

A deliberately small, PIT-disciplined research loop for trading strategies:
ingest data, generate candidates, backtest with costs, and pass them through a
deflation gate before anything is called a signal. AI proposers are optional
plug-ins, not the centre of the design.

Pure standard library (Python >= 3.11): `urllib` for HTTP, `sqlite3` for the
store, `tomllib` for config. No dependencies, no services to run.

## Why

Most backtest workflows optimise until the equity curve looks good, then forget
how many things were tried. qnl is built around three rules:

1. **Point-in-time data.** Every read is bounded by `close_time <= asof`.
   Signals may only use bars up to the current one, and every series must be
   prefix-invariant: appending new data never changes old values.
2. **Costs on.** Fees are charged on turnover in every backtest, by default.
3. **Count what you tried.** Every evaluated `(family, params)` cell is written
   to a trials ledger. The gate deflates the Sharpe by that trial count using
   the Deflated Sharpe Ratio (Bailey & Lopez de Prado): a hypothesis passes only
   if both the nominal and the best-of-N-corrected DSR clear 0.95 / 0.90.

Walk-forward selection is the default: choose on the first fraction of history,
report on the held-out remainder. A number that only exists in-sample is not a
result.

## Quickstart

Offline, no network, no database:

```bash
python3 -m pip install -e .      # optional: makes `import qnl` global
python3 examples/sma.py
```

Real data from Binance (8h bars, perp marks, funding):

```bash
python3 -m qnl.ingest --symbol BTCUSDT --interval 8h --start 2021-01-01
python3 -m qnl.ingest --symbol BTCUSDT --interval 8h --start 2021-01-01 --mark
python3 -m qnl.ingest --symbol BTCUSDT --funding --start 2021-01-01
python3 -m qnl.loop --symbols BTCUSDT --backend offline --rounds 12
```

Walk-forward one family with the gate:

```bash
python3 -m qnl.walk_forward --signal sma_cross
python3 -m qnl.verifier --symbol BTCUSDT --family sma_cross --params 20,50
```

Use an LLM proposer instead of the offline one:

```bash
python3 -m qnl.loop --symbols BTCUSDT --backend deepseek --rounds 12 --budget 2.0
python3 -m qnl.loop --symbols BTCUSDT --backend openai --rounds 12 --budget 2.0
python3 -m qnl.loop --symbols BTCUSDT --backend llm --rounds 12 --budget 2.0
```

Backends: `deepseek`, `openai` (ChatGPT), `llm` (Anthropic). Any
OpenAI-compatible endpoint — OpenRouter, Ollama, vLLM — works with
`llm.backend = "openai"` and `llm.base_url` set.

## Architecture

```
Binance / plugin sources
        |
        v
store.py (sqlite, PIT) ---> sources.py (planes: price, funding, basis, ...)
                                   |
                                   v
                          signals.py (registered families)
                                   |
                 loop.py / research.py / generator.py (proposers)
                                   |
                          verifier.py -> stats.py (DSR)
                                   |            ^
                                   v            |
                            backtest.py    trials.py (ledger)
                                   |
              live.py (frozen series)  paper.py (simulated capital)
                                   |
                       service.py (tick, one lock, run history)
                                   |
                    dashboard.py (static reports/index.html)
```

`service.py tick` runs the stages in order — snapshot, live, paper, pool, the
planner cycle, then the report — isolating failures and recording each one in a
`runs` table. A single `flock` keeps cycles from overlapping.

### Extension points

The framework knows no strategy names beyond baseline price families. A plugin
registers what it needs and calls `qnl.service.cli()`:

- `signals.register(name, build, fmt, hint, defaults)` — a signal family
- `sources.register_plane(name, fn, doc)` — a causal data plane
- `live.register_kind(kind, series_fn)` — a live/paper book provider
- `planner.register_task(kind, plan_fn, run_fn, order)` — an autonomous task
- `snapshot.register_provider(name, fn)` — a periodic collector
- `service.register_stage(name, fn, order, network)` — a tick stage
- `trials.add_code_files(*paths)` — include files in the code hash

See [docs/plugin.md](docs/plugin.md) for a worked example. Dependency direction
is strictly one-way: plugins import `qnl`; `qnl` never imports a plugin.

### LLM-built extensions

The same registries can be driven from a prompt. The LLM writes a module plus
its own self-check; the framework vets the AST, runs a deterministic contract
(shape, finiteness, prefix invariance), retries at most three times with the
failure text, and only then activates it. The model never sees Sharpe or DSR.

```bash
python3 -m qnl.extend "add PENDLEUSDT on binance, plus a funding z-score factor"
python3 -m qnl.extend --list
python3 -m qnl.extend --sync     # fetch source/exchange data into the store
```

Kinds: `token`, `indicator`, `factor`, `source`, `exchange`. Modules and
manifests live in `paths.extensions` (default `extensions/`); failed items are
written as `quarantined` with the contract error. Generated code has no
imports; HTTP is injected as `api.get` with a host allowlist, timeout and size
cap.

## Configuration

Copy `qnl.example.toml` to `qnl.local.toml` (gitignored). Precedence is
CLI > `QNL_*` environment > config file > defaults; relative paths resolve
against the config file's directory.

`llm.api_key_file` is a *path* to a file containing the key; never put key
material in the config. Without it, keys come from `DEEPSEEK_API_KEY`,
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. `llm.base_url` points an OpenAI-shaped
backend at any compatible endpoint. `[alerts] webhook_url` is optional.

## Deployment

```bash
./deploy/install.sh
systemctl --user start qnl-autoresearch.service
loginctl enable-linger "$(id -un)"
python3 -m qnl.service status --config qnl.local.toml
python3 -m qnl.service doctor --config qnl.local.toml
```

A systemd timer fires four times a day; a missed run catches up. Logs go to
`reports/qnl.log` and the journal. See [docs/deploy.md](docs/deploy.md).

## Security

- No trade endpoints and no exchange keys: this is a research system. The only
  outbound calls are public market-data and optional LLM APIs.
- LLM-written extensions are AST-vetted and execute without imports, but in the
  same interpreter — not a hard sandbox. Review `extensions/` and use
  `--dry-run` before enabling those modules on an unattended tick.
- API keys live in files outside the repo; the config stores paths only and is
  gitignored. Logs never print key material.
- The reports directory is a static file; if exposed, put authentication in
  front of it.

## Tests

```bash
python3 -m qnl.selftest
```

Every test is a plain `main()` with asserts — no test framework required. CI
runs the same command on Python 3.11 and 3.13.

## Disclaimer

Nothing here is investment advice. A passing gate means a hypothesis survived
the deflation test on the data you gave it; it does not mean it will make money.
Past performance, survivorship in the trial ledger, and data errors remain
real risks. Paper tracking is simulation only.

## License

MIT.
