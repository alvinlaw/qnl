"""Run every framework self-check in the package: python3 -m qnl.selftest."""
import glob
import importlib
import os
import sys
import traceback


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    mods = sorted(os.path.basename(p)[:-3]
                  for p in glob.glob(os.path.join(here, "test_*.py")))
    failed = []
    for m in mods:
        try:
            importlib.import_module("qnl." + m).main()
        except SystemExit as e:
            if e.code:
                failed.append((m, f"exit {e.code}"))
        except Exception as e:  # noqa: BLE001 -- one test must not hide the rest
            failed.append((m, f"{type(e).__name__}: {e}"))
            traceback.print_exc()
    print(f"\n{len(mods) - len(failed)}/{len(mods)} passed")
    for m, e in failed:
        print(f"FAIL {m}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
