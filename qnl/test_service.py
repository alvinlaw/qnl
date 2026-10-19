"""Service self-check: lock exclusivity, stage isolation/retries, run history.
    python3 -m qnl.test_service"""
import os
import sqlite3
import tempfile

from qnl import config, notify, runs, service


def main():
    config._selfcheck()
    notify._selfcheck()
    runs._selfcheck()
    d = tempfile.mkdtemp()
    path = os.path.join(d, "qnl.lock")

    fd = service._lock(path)
    assert fd is not None
    assert service._lock(path) is None, "a held lock must exclude a second tick"
    assert service._lock_holder(path) not in (None, ""), "holder pid must be readable"
    os.close(fd)
    assert service._lock_holder(path) is None
    fd = service._lock(path)  # released on close, reacquirable
    assert fd is not None
    os.close(fd)

    con = sqlite3.connect(":memory:")
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("first attempt fails")
        return "recovered"

    ok, detail = service._stage(con, "flaky", flaky, retries=1)
    assert ok and detail == "recovered", (ok, detail)
    assert len(calls) == 2

    def broken():
        raise ValueError("nope")

    ok, detail = service._stage(con, "broken", broken, retries=1)
    assert not ok and "nope" in detail
    st = runs.last_status(con)
    assert st["broken"]["status"] == "failed"
    assert st["flaky"]["status"] == "ok"

    print("service self-check OK")


if __name__ == "__main__":
    main()
