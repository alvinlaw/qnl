"""Self-check for depth parsing and sizing."""
from qnl import depth


def main():
    depth._selfcheck()
    # walk past the 10bp band and stop
    hl = [{"px": "100.0", "sz": "1"}, {"px": "99.8", "sz": "100"}]
    assert depth.cap10(hl, 100.0) == 100.0
    bn = [["100.0", "2"], ["99.8", "50"]]
    assert depth.cap10(bn, 100.0) == 200.0
    print("ok")


if __name__ == "__main__":
    main()
