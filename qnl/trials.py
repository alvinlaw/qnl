"""Trials ledger.

N — how many independent hypotheses we have evaluated — is the linchpin of
deflation. Without it no multiple-testing correction means anything. Keyed on
(label, params) so re-running the same config does not inflate N; a whole
parameter grid for one family counts as ONE hypothesis, not one per cell (see
cells()).
"""
import datetime as dt
import hashlib
import os
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    label     TEXT    NOT NULL,
    params    TEXT    NOT NULL,
    sharpe    REAL    NOT NULL,   -- per-period
    bars      INTEGER NOT NULL,
    asof_ms   INTEGER NOT NULL,
    ts        TEXT    NOT NULL,
    code_hash TEXT,               -- NULL = measured before provenance existed
    fee       REAL,               -- NULL = not supplied by the caller
    PRIMARY KEY (label, params)
);
"""

# Modules whose content determines a backtest's return series. walk_forward.py is
# deliberately absent: OOS slicing does not change the returns themselves.
_CODE_FILES = ["funding.py", "strategies.py", "backtest.py", "metrics.py",
               "signals.py", "sources.py", "ec.py"]


def add_code_files(*paths):
    """Plugins append their own modules (absolute or package-relative) to the
    provenance fingerprint. Call before the first code_hash()."""
    for p in paths:
        if p not in _CODE_FILES:
            _CODE_FILES.append(p)

_REASONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS reasons (
    reason TEXT NOT NULL,
    ts     TEXT NOT NULL
);
"""

_SWEPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS swept (
    name TEXT PRIMARY KEY,
    ts   TEXT NOT NULL
);
"""

_ATTEMPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    origin TEXT    NOT NULL,   -- github:<query> / arxiv:<query> / codegen
    ok     INTEGER NOT NULL,   -- 1 = a family was proposed AND vetted
    gate   INTEGER,            -- 1 = it also passed the DSR gate; NULL = unknown
    ts     TEXT    NOT NULL
);
"""


def _ensure(con):
    """Create the ledger, adding provenance columns to pre-existing databases."""
    con.executescript(_SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(trials)")}
    if "code_hash" not in cols:
        con.execute("ALTER TABLE trials ADD COLUMN code_hash TEXT")
    if "fee" not in cols:
        con.execute("ALTER TABLE trials ADD COLUMN fee REAL")
    con.executescript(_ATTEMPTS_SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(attempts)")}
    if "gate" not in cols:  # legacy rows stay NULL: unknown, never a win
        con.execute("ALTER TABLE attempts ADD COLUMN gate INTEGER")


def connect(path):
    con = sqlite3.connect(path)
    _ensure(con)
    return con


_HASH = None


def code_hash():
    """Fingerprint of the code that determines the recorded return series.
    Cached per process: every record in one run sees the same code."""
    global _HASH
    if _HASH is None:
        h = hashlib.sha1()
        root = os.path.dirname(os.path.abspath(__file__))
        for fn in _CODE_FILES:
            path = fn if os.path.isabs(fn) else os.path.join(root, fn)
            try:
                with open(path, "rb") as f:
                    h.update(f.read())
            except OSError:
                pass
        _HASH = h.hexdigest()[:12]
    return _HASH


def record(con, label, params, sharpe, bars, asof_ms, fee=None):
    _ensure(con)  # caller may hold a connection built for another table
    con.execute(
        "INSERT OR REPLACE INTO trials(label,params,sharpe,bars,asof_ms,ts,"
        "code_hash,fee) VALUES (?,?,?,?,?,?,?,?)",
        (label, params, sharpe, bars, asof_ms,
         dt.datetime.now(dt.timezone.utc).isoformat(), code_hash(), fee),
    )
    con.commit()


def stale(con):
    """Rows measured by a different version of the signal code. NULL hash means
    the row predates provenance; it is unknown, not provably stale."""
    _ensure(con)
    cur = code_hash()
    return [(r[0], r[1]) for r in con.execute(
        "SELECT label, params FROM trials"
        " WHERE code_hash IS NOT NULL AND code_hash != ?", (cur,))]


def audit(con):
    """One line for logs: raw rows, effective cells, stale, unknown-version."""
    _ensure(con)
    raw = con.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    cur = code_hash()
    st = con.execute("SELECT COUNT(*) FROM trials WHERE code_hash IS NOT NULL"
                     " AND code_hash != ?", (cur,)).fetchone()[0]
    unk = con.execute("SELECT COUNT(*) FROM trials WHERE code_hash IS NULL"
                      ).fetchone()[0]
    return (f"trials: {raw} rows, {len(cells(con))} effective cells, "
            f"{st} stale, {unk} unknown-version")


def reason(con, code):
    """Count why a proposal slot failed, so the loop can see its own bottleneck."""
    con.executescript(_REASONS_SCHEMA)
    con.execute("INSERT INTO reasons(reason, ts) VALUES (?,?)",
                (code, dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()


def reason_counts(con):
    con.executescript(_REASONS_SCHEMA)
    return {r: n for r, n in con.execute(
        "SELECT reason, COUNT(*) FROM reasons GROUP BY reason"
        " ORDER BY COUNT(*) DESC")}


def _family(label):
    """'carry(-9.0,-9.0,30)' -> 'carry'. Generated families keep their own name
    ('gen:foo'), so each is a separate hypothesis."""
    return label.split("(", 1)[0]


def cells(con):
    """One Sharpe per independent hypothesis: the best config of a family on one
    `params` cell (for loop trials params is the asset). A parameter grid is a
    search INSIDE one hypothesis, not k independent trials -- counting every row
    inflated N and the deflation bar until nothing could pass.

    A key counts even when it was last measured by an older code version: a code
    change does not un-evaluate the hypothesis, and dropping old keys made N fall
    when a new module was added -- flattering every later DSR. The VALUE for a key
    comes from the current version when it has one (stale measurements describe a
    model no longer under test); otherwise the old value is all we know.

    shortcut: takes the best config per cell and ignores correlation between
    configs, so a genuinely decorrelated grid is under-counted; upgrade by
    clustering stored return series if that matters."""
    _ensure(con)
    cur = code_hash()
    best, best_cur = {}, {}
    for label, params, sharpe, ch in con.execute(
            "SELECT label, params, sharpe, code_hash FROM trials"):
        key = (_family(label), params)
        if key not in best or sharpe > best[key]:
            best[key] = sharpe
        if ch is None or ch == cur:
            if key not in best_cur or sharpe > best_cur[key]:
                best_cur[key] = sharpe
    return [best_cur.get(k, v) for k, v in best.items()]


def count(con):
    """Effective N: independent (family, params) cells, not raw ledger rows."""
    return len(cells(con))


def labels(con, prefix=""):
    """Recorded labels, optionally by prefix (e.g. 'gen:' for proposer families).
    Lets a proposer see what directions are already spent without seeing scores."""
    _ensure(con)
    return [r[0] for r in con.execute(
        "SELECT label FROM trials WHERE label LIKE ? || '%'", (prefix,))]


def pairs(con):
    """The (label, params) cells already recorded. For loop trials params holds the
    ASSET, so a driver can ask which (spec, asset) cells are done before re-running
    them -- skipping a cell only when it is done for every symbol it will run."""
    _ensure(con)
    return [(r[0], r[1]) for r in con.execute("SELECT label, params FROM trials")]


def sweep(con, name):
    """Blacklist a family name a proposer should never spend a slot on again.
    For ideas that produced NO trial (vet/shape rejects): they never enter the
    trials ledger, so without this they would be re-proposed every run."""
    con.executescript(_SWEPT_SCHEMA)
    con.execute("INSERT OR IGNORE INTO swept(name, ts) VALUES (?, ?)",
                (name, dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()


def swept_names(con):
    con.executescript(_SWEPT_SCHEMA)
    return [r[0] for r in con.execute("SELECT name FROM swept")]


def attempt(con, origin, ok, gate=None):
    """Log one proposed slot. `ok` = a family was proposed AND vetted; `gate` =
    that family also passed the DSR gate (NULL = never reached the gate, or a
    legacy row that predates this column). The source bandit scores on `gate`."""
    _ensure(con)
    con.execute("INSERT INTO attempts(origin, ok, gate, ts) VALUES (?,?,?,?)",
                (origin, 1 if ok else 0, None if gate is None else int(bool(gate)),
                 dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit()


def origin_stats(con):
    """-> {origin: (slots, gate_passes)} for the source bandit. Legacy rows with
    no gate value count as slots, not wins (unknown is not evidence of edge)."""
    _ensure(con)
    return {o: (n, k) for o, n, k in con.execute(
        "SELECT origin, COUNT(*), COALESCE(SUM(gate), 0)"
        " FROM attempts GROUP BY origin")}


def digest(con):
    """A compact, INFORMATIONAL view of the ledger for the proposer's prompt:
    how many rows were tried and which families were seen. Deliberately does NOT
    rank or score -- telling the model what to chase would optimise it against the
    gate, which is just overfitting faster."""
    from qnl import signals  # function-local: avoids an import cycle at module load
    _ensure(con)  # caller may hold a store connection (no trials table)
    rows = list(con.execute("SELECT label FROM trials"))
    if not rows:
        return "(ledger empty -- nothing tried yet)"
    # Only families the registry still knows: the ledger keeps rows from renamed
    # families, and advertising a name the proposer cannot use -- on a prompt that
    # says "ONLY these are valid" -- invites a reply _parse_spec must reject.
    fams = sorted({label.split("(", 1)[0] for (label,) in rows} & signals.FAMILIES)
    return (f"{len(rows)} (config, asset) rows already tried; families seen: "
            f"{', '.join(fams) or '(none)'}")


def variance(con):
    """Sample variance of per-period Sharpe across effective cells (0 if < 2)."""
    xs = cells(con)
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
