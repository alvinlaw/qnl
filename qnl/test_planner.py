"""Self-check for the bounded planner (no network)."""
from qnl import planner


def main():
    planner._selfcheck()
    print("ok")


if __name__ == "__main__":
    main()
