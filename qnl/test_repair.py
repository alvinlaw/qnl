"""Self-check for the codegen source-repair loop (no network)."""
from qnl import codegen


class G:
    def __init__(self, text):
        self.text = text

    def _chat(self, *_a, **_k):
        return self.text


def main():
    bad = "def build(params, data):\n    return [0.0] * 2\n"
    good = ("def build(params, data):\n    return [0.01] * (len(data['price']) - 1)\n")
    bundle = {"price": list(range(1, 51))}
    fixed, err = codegen._try_repair(G(good), bad, [], bundle, "length 2 != 49")
    assert fixed and err is None, (fixed, err)
    assert codegen._try_repair(object(), bad, [], bundle, "x")[0] is None

    same, err = codegen._try_repair(G(bad), bad, [], bundle, "length 2 != 49")
    assert same is None and err, (same, err)
    print("ok")


if __name__ == "__main__":
    main()
