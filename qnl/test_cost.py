"""Self-check for LLM cost accounting (no network)."""
from qnl import cost


def main():
    cost._selfcheck()
    print("ok")


if __name__ == "__main__":
    main()
