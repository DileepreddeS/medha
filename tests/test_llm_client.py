import json

import pytest
from pydantic import BaseModel

from medha.llm.client import (
    BudgetExceeded,
    ChatMessage,
    LLMClient,
    LLMError,
    OpenAIBackend,
    SchemaFailed,
    strip_channel_tags,
)
from tests.conftest import StubBackend


class Pair(BaseModel):
    a: int
    b: str


AUTHOR_ROLES = ("planner", "router", "author", "testwriter", "repairer", "composer")


def test_provider_routing_judge_is_meta_author_is_glimmer(settings):
    settings.LLM_AUTHOR_PROVIDER = "glimmer"
    c = LLMClient(settings)
    assert c.provider_for("judge") == "meta"
    assert c.provider_for("escalation") == "meta"
    for role in AUTHOR_ROLES:
        assert c.provider_for(role) == "glimmer", role


def test_author_fallback_routes_all_author_roles_to_meta(settings):
    # Temporary: Spark 1.3 covers author roles until Glimmer boots (DECISIONS D2).
    # Judge/escalation stay on meta either way.
    settings.LLM_AUTHOR_PROVIDER = "meta"
    c = LLMClient(settings)
    for role in AUTHOR_ROLES:
        assert c.provider_for(role) == "meta", role
    assert c.provider_for("judge") == "meta"
    assert c.provider_for("escalation") == "meta"


def test_judge_model_differs_from_author_model(settings):
    settings.LLM_AUTHOR_PROVIDER = "glimmer"  # evaluator/author split needs Glimmer up
    c = LLMClient(settings)
    assert c.models[c.provider_for("judge")] != c.models[c.provider_for("author")]


def test_complete_uses_backend_and_counts_cost(settings):
    settings.META_PRICE_IN_PER_1K = 1.0
    settings.META_PRICE_OUT_PER_1K = 3.0
    meta = StubBackend([("pong", 1000, 500)])
    c = LLMClient(settings, meta_backend=meta)
    r = c.complete("judge", [ChatMessage(role="user", content="hi")])
    assert r.content == "pong"
    assert r.provider == "meta" and r.model == "muse-spark-1.3"
    assert r.cost_usd == pytest.approx(2.5)
    assert meta.calls[0]["model"] == "muse-spark-1.3"


def test_glimmer_calls_are_zero_token_cost(settings):
    settings.LLM_AUTHOR_PROVIDER = "glimmer"
    glimmer = StubBackend([("pong", 100, 50)])
    c = LLMClient(settings, glimmer_backend=glimmer)
    r = c.complete("author", [{"role": "user", "content": "hi"}])
    assert r.cost_usd == 0.0
    assert r.model == "glimmer"


def test_fallback_author_calls_bill_meta_tokens(settings):
    settings.LLM_AUTHOR_PROVIDER = "meta"
    settings.META_PRICE_IN_PER_1K = 1.0
    settings.META_PRICE_OUT_PER_1K = 3.0
    meta = StubBackend([("pong", 1000, 500)])
    c = LLMClient(settings, meta_backend=meta)
    r = c.complete("author", [{"role": "user", "content": "hi"}])
    assert (r.provider, r.model) == ("meta", "muse-spark-1.3")
    assert r.cost_usd == pytest.approx(2.5)


def test_glimmer_backend_sends_configured_api_key(settings):
    settings.GLIMMER_API_KEY = "g-secret"
    c = LLMClient(settings)
    backend = c.backends["glimmer"]
    assert isinstance(backend, OpenAIBackend) and backend.api_key == "g-secret"


def test_unknown_role_raises(settings):
    c = LLMClient(settings)
    with pytest.raises(LLMError):
        c.complete("nope", [])


def test_budget_cap_refuses_work(settings, tmp_path):
    log = tmp_path / "cost.jsonl"
    log.write_text(json.dumps({"day": "2999-01-01", "cost_usd": 99}) + "\n")
    settings.COST_LOG_PATH = str(log)
    settings.DAILY_SPEND_CAP_USD = 5.0
    c = LLMClient(settings)
    assert c.budget.spent_today(today="2999-01-01") == 99.0
    with pytest.raises(BudgetExceeded):
        c.budget.check.__self__.cap_usd = -1  # force over-cap deterministically
        c.budget.check()


def test_demo_cache_replays_without_backend(settings):
    settings.DEMO_MODE = True
    c = LLMClient(settings)
    msgs = [{"role": "user", "content": "hello"}]
    path = c._cache_path("router", msgs, False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "content": "cached!",
                "tokens_in": 1,
                "tokens_out": 2,
                "cost_usd": 0.0,
                "latency_ms": 3,
                "model": "glimmer",
                "provider": "glimmer",
                "cached": False,
            }
        )
    )

    def boom(**kwargs):
        raise AssertionError("backend must not be called on cache hit")

    c.backends["glimmer"] = boom
    r = c.complete("router", msgs)
    assert (r.content, r.cached) == ("cached!", True)


def test_complete_json_ok_and_retry(settings):
    settings.LLM_AUTHOR_PROVIDER = "glimmer"
    good = StubBackend([("not json", 5, 5), ('{"a": 1, "b": "x"}', 5, 5)])
    c = LLMClient(settings, glimmer_backend=good)
    out = c.complete_json("planner", [ChatMessage(role="user", content="go")], Pair)
    assert out == Pair(a=1, b="x")
    assert len(good.calls) == 2
    assert good.calls[0]["json_mode"] is True
    assert good.calls[1]["json_mode"] is False  # retry drops response_format


def test_complete_json_fails_loudly_after_retry(settings):
    settings.LLM_AUTHOR_PROVIDER = "glimmer"
    bad = StubBackend([("nope", 5, 5), ("still nope", 5, 5)])
    c = LLMClient(settings, glimmer_backend=bad)
    with pytest.raises(SchemaFailed):
        c.complete_json("planner", [ChatMessage(role="user", content="go")], Pair)
    assert [call["json_mode"] for call in bad.calls] == [True, False]


def test_complete_json_empty_reply_reports_finish_reason(settings):
    from types import SimpleNamespace

    seen: list[dict] = []

    def create(**kw):
        seen.append(kw)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="   "), finish_reason="length")
            ],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=2048,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=2000),
            ),
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    c = LLMClient(settings)
    c.backends["glimmer"] = OpenAIBackend("http://x/v1", "k")
    c.backends["glimmer"]._client = fake
    with pytest.raises(SchemaFailed) as exc:
        c.complete_json("planner", [ChatMessage(role="user", content="go")], Pair)
    assert "length" in str(exc.value)
    assert "2048" in str(exc.value)
    assert len(seen) == 2  # initial + retry
    assert "response_format" in seen[0] and "response_format" not in seen[1]


LEAK_OBSERVED = "to=selfReply: pong, thinking it over assistant to=userpong"
LEAK_FULL_FRAME = (
    "to=self<|message|>chain of thought<|eom|>\n<|start|>assistant to=user<|message|>pong<|eot|>"
)


def test_strip_channel_tags_observed_leak():
    assert strip_channel_tags(LEAK_OBSERVED) == "pong"


def test_strip_channel_tags_full_framing():
    assert strip_channel_tags(LEAK_FULL_FRAME) == "pong"


def test_strip_channel_tags_clean_passthrough():
    clean = '{"totals": {"grand_total": 16.0}}'
    assert strip_channel_tags(clean) == clean
    prose = "The user asked for a summary."
    assert strip_channel_tags(prose) == prose


def test_strip_channel_tags_reasoning_only_yields_empty():
    assert strip_channel_tags("to=self<|message|>still thinking<|eom|>") == ""


def test_glimmer_backend_applies_reasoning_guard():
    from types import SimpleNamespace

    leaked = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=LEAK_FULL_FRAME))],
                    usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
                )
            )
        )
    )
    backend = OpenAIBackend("http://x/v1", "k", reasoning_guard=True)
    backend._client = leaked
    content, tok_in, tok_out = backend.chat(
        model="glimmer",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        temperature=0.0,
        json_mode=False,
    )
    assert (content, tok_in, tok_out) == ("pong", 10, 5)


def test_reasoning_guard_wired_glimmer_only(settings):
    c = LLMClient(settings)
    assert c.backends["glimmer"].reasoning_guard is True
    assert c.backends["meta"].reasoning_guard is False


def test_low_temperatures_for_author_and_testwriter(settings):
    settings.LLM_AUTHOR_PROVIDER = "glimmer"
    stub = StubBackend([("ok", 1, 1)] * 3)
    c = LLMClient(settings, glimmer_backend=stub)
    c.complete("author", [{"role": "user", "content": "hi"}])
    c.complete("testwriter", [{"role": "user", "content": "hi"}])
    c.complete("planner", [{"role": "user", "content": "hi"}])
    assert [call["temperature"] for call in stub.calls] == [0.2, 0.2, 0.4]


def test_judge_temperature_is_zero(settings):
    meta = StubBackend([("ok", 1, 1)])
    c = LLMClient(settings, meta_backend=meta)
    c.complete("judge", [{"role": "user", "content": "hi"}])
    assert meta.calls[0]["temperature"] == 0.0


class _HttpErr(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _always(status_code: int):
    def create():
        raise _HttpErr(status_code)

    return create


def test_retry_rides_out_boot_503s_then_succeeds():
    from medha.llm.client import retry_booting_call

    calls = []

    def create():
        calls.append(1)
        if len(calls) <= 2:
            raise _HttpErr(503)
        return "warm"

    sleeps: list[float] = []
    assert retry_booting_call(create, budget_s=60, sleep=sleeps.append) == "warm"
    assert len(calls) == 3 and sleeps == [2.0, 4.0]


def test_retry_fails_fast_on_non_boot_errors():
    from medha.llm.client import retry_booting_call

    sleeps: list[float] = []
    with pytest.raises(_HttpErr):
        retry_booting_call(_always(401), budget_s=60, sleep=sleeps.append)
    assert sleeps == []


def test_retry_budget_bounds_total_wait():
    import time

    from medha.llm.client import retry_booting_call

    start = time.monotonic()
    with pytest.raises(_HttpErr):
        retry_booting_call(_always(503), budget_s=3, sleep=time.sleep)
    assert time.monotonic() - start < 10  # one 2s sleep, then raise


def test_glimmer_backend_wires_retry_from_settings(settings):
    c = LLMClient(settings)
    backend = c.backends["glimmer"]
    assert isinstance(backend, OpenAIBackend) and backend.retry_503 is True
    assert backend.retry_budget_s == settings.GLIMMER_RETRY_BUDGET_S
    assert c.backends["meta"].retry_503 is False


def test_low_budget_covers_router_reasoning():
    """The low effort budget must survive router reasoning headroom.

    Regression: a live router call length-capped at 2048 (635 in, 2048
    out, finish_reason=length), failing the reuse task outright. The
    router is the only low-effort role; keep its floor at 4096.
    """
    from medha.llm.client import EFFORT_PARAMS, ROLE_EFFORT

    assert ROLE_EFFORT["router"] == "low"
    assert EFFORT_PARAMS["low"]["max_tokens"] >= 4096
    # A repair-path testwriter call length-capped at 12288 with zero
    # answer tokens; high covers author/testwriter/repairer/composer.
    assert EFFORT_PARAMS["high"]["max_tokens"] >= 16384
