"""Self-check for the advisory critic (no network)."""
from qnl import critic


def main():
    critic._selfcheck()
    print("ok")


if __name__ == "__main__":
    main()
