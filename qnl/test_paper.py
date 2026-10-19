"""Paper-book self-check: immutable seed, idempotent marks, equity math.
    python3 -m qnl.test_paper"""
from qnl import paper


def main():
    paper._selfcheck()
    print("paper self-check OK")


if __name__ == "__main__":
    main()
