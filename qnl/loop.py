"""Stage 4: the usage->improvement loop, with the gate in charge.

generate -> evaluate (deterministic, per asset) -> record trial (N grows) ->
feed back. The generator only proposes; only the gate accepts. Acceptance is
PER ASSET: carry can clear ETH while failing BTC, and that still counts.

The proposer works over the whole signal registry (price/carry/flow/model), so a
new plane plugged into signals.py is instantly reachable -- a local change.

    python3 -m qnl.loop --symbols BTCUSDT,ETHUSDT,SOLUSDT --rounds 12
"""
import argparse
import os

from qnl import (benchmark, experience, generator, metrics, signals, sources,
                 stats, store, trials)

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qnl.sqlite")
_ACCEPT_DSR0 = 0.95  # P[edge > best-of-N luck]
_ACCEPT_DSRB = 0.90  # P[beats buy-and-hold]


def passes(res):
    return res["sr"] > 0 and res["dsr0"] >= _ACCEPT_DSR0 and res["dsrb"] >= _ACCEPT_DSRB


def evaluate(con, symbol, bundle, bench_rets, spec, fee, asof_ms):
    rets = signals.build(spec[0], spec[1], fee)(bundle)
    sr = stats.sharpe_period(rets)
    trials.record(con, signals.fmt(*spec), symbol, sr, len(rets), asof_ms, fee=fee)
    n, var = trials.count(con), trials.variance(con)
    res = {
        "symbol": symbol,
        "sr": sr,
        "dsr0": stats.dsr(rets, 0.0, n, var),
        "dsrb": stats.dsr(rets, stats.sharpe_period(bench_rets), n, var),
    }
    res["accept"] = passes(res)
    planes = [k for k, v in bundle.items() if v]
    experience.record(con, "signal", signals.fmt(*spec), spec[1], planes,
                      "pass" if res["accept"] else "gate",
                      "" if res["accept"] else "gate rejected",
                      f"{sum(1 for x in rets if x != 0.0)} non-zero bars")
    return res


def run(con, data, gen, rounds, fee, asof_ms):
    history = []
    for _ in range(rounds):
        ledger = trials.digest(con)
        mem = experience.format(experience.recall(
            con, " ".join(str(h["spec"]) for h in history[-3:]) or "signal", 5))
        if mem:
            ledger += "\nPAST LESSONS:\n" + mem
        spec = gen.propose(history, ledger)
        rows = [evaluate(con, sym, b, br, spec, fee, asof_ms)
                for sym, (b, br) in data.items()]
        history.append({
            "spec": spec,
            "mean_sr": sum(r["sr"] for r in rows) / len(rows),
            "rows": rows,
        })
        cells = "  ".join(
            f"{r['symbol'][:3]} {r['sr']:+.3f}/{r['dsrb']:.2f}{'*' if r['accept'] else ' '}"
            for r in rows)
        print(f"  {signals.fmt(*spec):<22} {cells}")
    return history


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT")
    p.add_argument("--asof", default=None, help="UTC date YYYY-MM-DD (default: now)")
    p.add_argument("--rounds", type=int, default=12)
    p.add_argument("--backend",
                   choices=["offline", "llm", "openai", "deepseek"],
                   default="offline")
    p.add_argument("--fee", type=float, default=0.0009)
    p.add_argument("--budget", type=float, default=2.0, help="daily LLM budget, USD")
    p.add_argument("--db", default=_DEFAULT_DB)
    a = p.parse_args()

    asof = store.asof_ms(a.asof)
    con = store.connect(a.db)
    data = {}
    for sym in a.symbols.split(","):
        b = sources.load(con, sym, asof)
        if not b["price"]:
            raise SystemExit(f"no price for {sym} asof that date")
        data[sym] = (b, metrics.equity_returns(benchmark.buy_and_hold(b["price"], fee=a.fee)))

    if a.backend == "llm":
        gen = generator.LLMGenerator()
    elif a.backend == "openai":
        gen = generator.OpenAIGenerator()
    elif a.backend == "deepseek":
        gen = generator.DeepSeekGenerator()
    else:
        # a (spec, symbol) cell already in the ledger is done -- do not re-backtest
        # it. Skipped only when every requested symbol is done; if the whole grid
        # is done (nothing new to sweep) fall back to it rather than propose none.
        syms = a.symbols.split(",")
        done = set(trials.pairs(con))
        untried = [s for s in signals.candidates()
                   if not all((signals.fmt(*s), sym) in done for sym in syms)]
        gen = generator.OfflineGenerator(untried or signals.candidates())
    gen.cost_con = con
    gen.budget_usd = a.budget
    print(f"{a.symbols}  grid={sources.GRID}  backend={a.backend}  rounds={a.rounds}  fee={a.fee}")
    history = run(con, data, gen, a.rounds, a.fee, asof)

    accepted = [(h["spec"], r["symbol"]) for h in history for r in h["rows"] if r["accept"]]
    print(f"accepted {len(accepted)} (spec, asset) pairs "
          f"(gate: sr>0, DSR0>={_ACCEPT_DSR0}, DSRb&h>={_ACCEPT_DSRB})")
    for spec, sym in accepted:
        print(f"  ACCEPT {signals.fmt(*spec):<22} {sym}")


if __name__ == "__main__":
    main()
