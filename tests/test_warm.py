"""Warm command + smoke wiring: retry-enabled Glimmer calls with headroom."""

from types import SimpleNamespace

import medha.llm.client as client_mod
import medha.sandbox.runner as runner_mod


def _settings():
    return SimpleNamespace(
        GLIMMER_BASE_URL="http://glimmer.test/v1",
        GLIMMER_API_KEY="k",
        GLIMMER_SERVED_MODEL="glimmer",
        GLIMMER_RETRY_BUDGET_S=30.0,
        META_BASE_URL="http://meta.test/v1",
        META_API_KEY="m",
        META_MODEL_ID="muse-spark-1.3",
        LLM_AUTHOR_PROVIDER="meta",
        DAILY_SPEND_CAP_USD=5.0,
        COST_LOG_PATH="/tmp/medha-test-cost.jsonl",
        LLM_CACHE_DIR="/tmp/medha-test-llm",
        DEMO_MODE=False,
    )


class _Backend:
    instances: list = []

    def __init__(self, base_url, api_key, **kw):
        self.kw = kw
        self.chat_kw: dict = {}
        _Backend.instances.append(self)

    def chat(self, **kw):
        self.chat_kw = kw
        return ("pong", 59, 120)


def test_warm_uses_retry_and_headroom(monkeypatch, capsys):
    import medha.config as config_mod
    import medha.warm as warm_mod

    _Backend.instances.clear()
    monkeypatch.setattr(config_mod, "settings", _settings())
    monkeypatch.setattr(client_mod, "OpenAIBackend", _Backend)
    assert warm_mod.main() == 0
    (backend,) = _Backend.instances
    assert backend.kw["retry_503"] is True
    assert backend.kw["retry_budget_s"] == 30.0
    assert backend.chat_kw["max_tokens"] == 2048
    assert "warm ok" in capsys.readouterr().out


def test_warm_fails_cleanly_without_url(monkeypatch):
    import medha.config as config_mod
    import medha.warm as warm_mod

    s = _settings()
    s.GLIMMER_BASE_URL = ""
    monkeypatch.setattr(config_mod, "settings", s)
    assert warm_mod.main() == 1


def test_smoke_glimmer_check_uses_retry_and_budget(monkeypatch):
    import medha.config as config_mod
    import medha.smoke as smoke_mod
    from medha.llm.client import LLMResponse

    _Backend.instances.clear()
    monkeypatch.setattr(config_mod, "settings", _settings())
    monkeypatch.setattr(client_mod, "OpenAIBackend", _Backend)
    monkeypatch.setattr(
        client_mod.LLMClient,
        "complete",
        lambda self, role, messages: LLMResponse(
            content="pong",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
            latency_ms=1,
            model="m",
            provider="meta",
        ),
    )

    class _Runner:
        def run_skill(self, code, inputs):
            if "example.com" in code:
                return SimpleNamespace(ok=True, result={"reached": False, "error": "URLError"})
            return SimpleNamespace(ok=True, result={"hello": "world"})

    monkeypatch.setattr(runner_mod, "ModalSandboxRunner", _Runner)
    assert smoke_mod.main() == 0
    (backend,) = [b for b in _Backend.instances if b.chat_kw]
    assert backend.kw["retry_503"] is True
    assert backend.chat_kw["max_tokens"] == 2048
