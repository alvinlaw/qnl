"""Generator factory tests: backend dispatch and URL shaping, no network."""
from qnl import generator


def _cfg(**llm):
    return {"llm": {"backend": "deepseek", "model": "", "api_key_file": "",
                    "base_url": ""} | llm}


def main():
    g = generator.create(_cfg(backend="openai", model="gpt-x"))
    assert isinstance(g, generator.OpenAIGenerator)
    assert g._model == "gpt-x"
    assert g._url == "https://api.openai.com/v1/chat/completions", g._url

    g = generator.create(_cfg(backend="openai", base_url="http://localhost:11434/v1/"))
    assert g._url == "http://localhost:11434/v1/chat/completions", g._url

    g = generator.create(_cfg(backend="openai",
                              base_url="https://openrouter.ai/api/v1/chat/completions"))
    assert g._url.endswith("/api/v1/chat/completions"), g._url

    assert isinstance(generator.create(_cfg(backend="deepseek")),
                      generator.DeepSeekGenerator)
    assert isinstance(generator.create(_cfg(backend="anthropic")),
                      generator.LLMGenerator)
    try:
        generator.create(_cfg(backend="nope"))
    except ValueError:
        pass
    else:
        raise AssertionError("unknown backend accepted")

    fam, params = generator._parse_spec('{"family": "momentum", "params": [5]}')
    assert fam == "momentum" and params == (5,), (fam, params)
    try:
        generator._parse_spec('{"family": "made_up", "params": [1]}')
    except ValueError:
        pass
    else:
        raise AssertionError("unknown family accepted")


if __name__ == "__main__":
    main()
    print("ok: generator")
