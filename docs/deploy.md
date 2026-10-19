# Deployment

One process, one lock, one timer. `tick` runs every stage in order, isolates
failures, and writes one row per stage to the `runs` table.

```
snapshot -> live -> paper-seed -> paper -> pool -> planner -> report
```

## Configure

```bash
cp qnl.example.toml qnl.local.toml
```

Relative paths resolve against the config file. Precedence is
CLI > `QNL_*` environment > file > defaults. `llm.api_key_file` is a **path**
to a file containing the key; never put key material in the config.

`python3 -m qnl.service doctor --config qnl.local.toml` checks the config, DB
schema, data freshness, key-file existence (size only), disk space and units.

## Install the timer

The units run the package from `~/qnl` (`%h` expands to your home directory);
edit them if your checkout lives elsewhere.

```bash
./deploy/install.sh
systemctl --user start qnl-autoresearch.service
systemctl --user status qnl-autoresearch.timer
loginctl enable-linger "$(id -un)"     # keep the timer running when logged out
```

`deploy/qnl-autoresearch.timer` fires at 00/06/12/18:17 with a randomized delay
and `Persistent=true`, so a missed run catches up.

## Operate

```bash
python3 -m qnl.service status --config qnl.local.toml   # nonzero if stale/unhealthy
python3 -m qnl.service doctor --config qnl.local.toml
python3 -m qnl.service tick --config qnl.local.toml --no-network
python3 -m qnl.service report --config qnl.local.toml
```

Logs go to `<reports>/qnl.log` (rotated, 1 MB x 3) and to the journal:

```bash
journalctl --user -u qnl-autoresearch.service -n 100 --no-pager
```

The dashboard is a static file at `<reports>/index.html`, rewritten every tick.
Serve it read-only with `python3 -m http.server` and put it behind your own auth
if the machine is reachable from the network.

## Tooling

`tick` holds `<reports>/qnl.lock` for its whole run. A second tick exits with
code 3 and logs the holding pid, so a slow run never overlaps the next.

If a stage fails, it is retried `service.retries` times (default 1) and the tick
exits nonzero. Set `alerts.webhook_url` to receive a JSON `{"text": ...}` POST.

## Backup and upgrade

```bash
python3 - <<'PY'
import sqlite3
src = sqlite3.connect("qnl.sqlite")
dst = sqlite3.connect("qnl.sqlite.bak")
src.backup(dst)              # consistent copy while the service is idle
PY
```

Upgrade: stop the timer (`systemctl --user stop qnl-autoresearch.timer`), pull,
run `python3 -m qnl.selftest`, start the timer. Schema changes are additive.
