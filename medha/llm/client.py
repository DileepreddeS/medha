"""Single choke point for every LLM call.

Providers (both OpenAI-compatible, both driven through the OpenAI SDK):
- ``glimmer``: self-hosted Muse Glimmer on Modal (vLLM). Spec-default home
  of all author-side roles: planner, router, author, testwriter, repairer,
  composer.
- ``meta``: Meta Model API, Muse Spark 1.3. Judge + escalation always, so
  the evaluator is a different model than the author. Also the fallback for
  author-side roles (``LLM_AUTHOR_PROVIDER="meta"``) when Glimmer is down.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

Provider = Literal["glimmer", "meta"]

ROLE_PROVIDER: dict[str, Provider] = {
    "judge": "meta",
    "escalation": "meta",
}

ROLE_EFFORT: dict[str, str] = {
    "planner": "medium",
    "router": "low",
    "author": "high",
    "testwriter": "high",
    "judge": "medium",
    "repairer": "high",
    "composer": "high",
    "escalation": "high",
}

# Budgets include reasoning headroom: reasoning models (Spark 1.3, Glimmer)
# spend most of max_tokens thinking, so the cap must cover thinking +
# answer. Observed: a 1-word reply consumed 299 completion tokens; a 2048
# cap length-capped a planner call, and later a router call (635 in,
# 2048 out, finish_reason=length) — low is now 4096. A repair-path
# testwriter call then length-capped at 12288 with zero answer tokens
# (pure reasoning ramble), so high is now 16384.
EFFORT_PARAMS: dict[str, dict[str, Any]] = {
    "low": {"max_tokens": 4096, "temperature": 0.2},
    "medium": {"max_tokens": 8192, "temperature": 0.4},
    "high": {"max_tokens": 16384, "temperature": 0.6},
}

# Per-role temperature overrides. Glimmer's own generation_config sets
# temperature 1.0, which is too chaotic for code and verdicts; we always
# send an explicit temperature, so the server default never applies.
ROLE_TEMPERATURE: dict[str, float] = {
    "author": 0.2,
    "testwriter": 0.2,
    "judge": 0.0,  # deterministic verdicts
}

# Muse Glimmer channel framing (vLLM recipe). The server-side
# `--reasoning-parser muse_glimmer` normally keeps these out of
# message.content; the guard below strips any leftovers.
_CHANNEL_FINAL = ("assistant to=user", "to=user")
_CHANNEL_SELF = ("assistant to=self", "to=self")
_CHANNEL_TOKENS = ("<|start|>", "<|message|>", "<|eom|>", "<|eot|>", "<|end_of_text|>")
_SELF_SPAN_RE = re.compile(r"(?:assistant to=self|to=self).*?<\|eom\|>", re.DOTALL)


def strip_channel_tags(text: str) -> str:
    """Remove Glimmer channel framing leaked into message content.

    Drops ``to=self ... <|eom|>`` reasoning spans, keeps only the text
    after the last final-answer marker, and removes remnant framing
    tokens. Clean text (no markers) passes through unchanged.
    """
    cleaned = _SELF_SPAN_RE.sub("", text)
    cut, cut_end = -1, 0
    for marker in _CHANNEL_FINAL:
        i = cleaned.rfind(marker)
        if i > cut:
            cut, cut_end = i, i + len(marker)
    if cut != -1:
        cleaned = cleaned[cut_end:]
    # Longest markers first: "assistant to=user" must go before "to=user"
    # so no "assistant " prefix is left behind.
    for token in _CHANNEL_SELF + _CHANNEL_FINAL + _CHANNEL_TOKENS:
        cleaned = cleaned.replace(token, "")
    return cleaned.strip()


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


class SchemaFailed(LLMError):
    pass


class EmptyContent(LLMError):
    """The model returned 200 but no content (e.g. reasoning ate max_tokens)."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str,
        tokens_in: int,
        tokens_out: int,
        reasoning_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.reasoning_tokens = reasoning_tokens


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    model: str
    provider: str
    cached: bool = False


class Budget:
    """File-backed daily spend guard shared by all live calls."""

    def __init__(self, cap_usd: float, log_path: str | Path) -> None:
        self.cap_usd = cap_usd
        self.log_path = Path(log_path)

    def spent_today(self, today: str | None = None) -> float:
        day = today or date.today().isoformat()
        if not self.log_path.exists():
            return 0.0
        total = 0.0
        for line in self.log_path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("day") == day:
                try:
                    total += float(row.get("cost_usd", 0.0))
                except (TypeError, ValueError):
                    continue
        return total

    def check(self) -> None:
        if self.spent_today() >= self.cap_usd:
            raise BudgetExceeded(f"daily spend cap ${self.cap_usd:.2f} reached")

    def record(self, cost_usd: float, meta: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        row = {"day": date.today().isoformat(), "cost_usd": cost_usd, **meta}
        with self.log_path.open("a") as f:
            f.write(json.dumps(row) + "\n")


class ChatBackend(Protocol):
    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, int, int]:
        """Return (content, prompt_tokens, completion_tokens)."""
        ...


# HTTP statuses worth retrying: the Modal proxy answers 503 while the
# Glimmer server scales from zero (boot takes minutes); 502/504 appear
# on the same path. Anything else fails fast.
RETRYABLE_BOOT_STATUS = (502, 503, 504)


def _is_boot_status(err: BaseException) -> bool:
    return getattr(err, "status_code", None) in RETRYABLE_BOOT_STATUS


def retry_booting_call(create_fn: Any, *, budget_s: float, sleep: Any = None) -> Any:
    """Call create_fn(), retrying boot statuses with exponential backoff.

    Total wait is bounded by budget_s; the last error raises afterward.
    Non-boot errors raise immediately with no sleep.
    """
    snooze = sleep or time.sleep
    deadline = time.monotonic() + budget_s
    delay = 2.0
    attempts = 0
    while True:
        attempts += 1
        try:
            return create_fn()
        except Exception as e:  # noqa: BLE001 - error classification below
            if not _is_boot_status(e) or time.monotonic() + delay > deadline:
                raise
            status = getattr(e, "status_code", "?")
            print(
                f"backend HTTP {status} (attempt {attempts}); retrying in {delay:.0f}s...",
                flush=True,
            )
            snooze(delay)
            delay = min(delay * 2, 30.0)


class OpenAIBackend:
    """OpenAI-SDK backend. Used for both providers (both are compatible)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        reasoning_guard: bool = False,
        retry_503: bool = False,
        retry_budget_s: float = 360.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.reasoning_guard = reasoning_guard
        self.retry_503 = retry_503
        self.retry_budget_s = retry_budget_s
        self._client: Any = None

    def _client_or_raise(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise LLMError("openai package is not installed") from e
            if not self.base_url:
                raise LLMError("empty base_url for OpenAI backend")
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key or "not-needed")
        return self._client

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> tuple[str, int, int]:
        client = self._client_or_raise()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            if self.retry_503:
                resp = retry_booting_call(
                    lambda: client.chat.completions.create(**kwargs),
                    budget_s=self.retry_budget_s,
                )
            else:
                resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError(f"chat call failed ({model}): {e}") from e
        if not resp.choices:
            raise LLMError(f"chat call failed ({model}): response had no choices")
        usage = resp.usage
        tok_in = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        tok_out = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tok = int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        content = resp.choices[0].message.content or ""
        if self.reasoning_guard:
            content = strip_channel_tags(content)
        if not content.strip():
            finish_reason = getattr(resp.choices[0], "finish_reason", "") or ""
            detail = f"finish_reason={finish_reason!r}, tokens={tok_in}+{tok_out}"
            if reasoning_tok:
                detail += f" ({reasoning_tok} reasoning)"
            raise EmptyContent(
                f"empty content from {model} ({detail})",
                finish_reason=finish_reason,
                tokens_in=tok_in,
                tokens_out=tok_out,
                reasoning_tokens=reasoning_tok,
            )
        return content, tok_in, tok_out


class LLMClient:
    def __init__(
        self,
        settings: Any,
        *,
        glimmer_backend: ChatBackend | None = None,
        meta_backend: ChatBackend | None = None,
    ) -> None:
        self.settings = settings
        self.last: LLMResponse | None = None
        self.budget = Budget(settings.DAILY_SPEND_CAP_USD, settings.COST_LOG_PATH)
        self.backends: dict[str, ChatBackend] = {
            "glimmer": glimmer_backend
            or OpenAIBackend(
                settings.GLIMMER_BASE_URL,
                settings.GLIMMER_API_KEY,
                reasoning_guard=True,
                retry_503=True,
                retry_budget_s=getattr(settings, "GLIMMER_RETRY_BUDGET_S", 360.0),
            ),
            "meta": meta_backend or OpenAIBackend(settings.META_BASE_URL, settings.META_API_KEY),
        }
        self.models = {"glimmer": settings.GLIMMER_SERVED_MODEL, "meta": settings.META_MODEL_ID}

    def provider_for(self, role: str) -> Provider:
        if role in ROLE_PROVIDER:
            return ROLE_PROVIDER[role]
        # Author-side roles follow the flag; getattr default is the spec
        # target ("glimmer") for settings objects that predate the flag.
        return getattr(self.settings, "LLM_AUTHOR_PROVIDER", "glimmer")

    def _cache_path(self, role: str, messages: list[dict[str, str]], json_mode: bool) -> Path:
        digest = hashlib.sha1(
            json.dumps(
                {"role": role, "messages": messages, "json": json_mode}, sort_keys=True
            ).encode()
        ).hexdigest()[:16]
        return Path(self.settings.LLM_CACHE_DIR) / f"{role}_{digest}.json"

    def complete(
        self,
        role: str,
        messages: list[ChatMessage] | list[dict[str, str]],
        *,
        effort: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        if role not in ROLE_EFFORT:
            raise LLMError(f"unknown role {role!r}")
        provider = self.provider_for(role)
        params = EFFORT_PARAMS[effort or ROLE_EFFORT[role]]
        temperature = ROLE_TEMPERATURE.get(role, params["temperature"])
        wire = [
            m if isinstance(m, dict) else {"role": m.role, "content": m.content} for m in messages
        ]
        cache_path = self._cache_path(role, wire, json_mode)
        if self.settings.DEMO_MODE and cache_path.exists():
            data = json.loads(cache_path.read_text())
            data["cached"] = True
            self.last = LLMResponse(**data)
            return self.last
        self.budget.check()
        backend = self.backends[provider]
        model = self.models[provider]
        start = time.monotonic()
        content, tok_in, tok_out = backend.chat(
            model=model,
            messages=wire,
            max_tokens=params["max_tokens"],
            temperature=temperature,
            json_mode=json_mode,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if provider == "meta":
            cost = tok_in / 1000 * self.settings.META_PRICE_IN_PER_1K + tok_out / 1000 * float(
                self.settings.META_PRICE_OUT_PER_1K
            )
        else:
            cost = 0.0  # self-hosted GPU; billed per second outside token accounting
        resp = LLMResponse(
            content=content,
            tokens_in=tok_in,
            tokens_out=tok_out,
            cost_usd=cost,
            latency_ms=latency_ms,
            model=model,
            provider=provider,
        )
        if self.settings.DEMO_MODE:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(resp.__dict__))
        elif cost:
            self.budget.record(cost, {"role": role, "model": model})
        self.last = resp
        return resp

    def complete_json(
        self,
        role: str,
        messages: list[ChatMessage] | list[dict[str, str]],
        schema: type[BaseModel],
        **kwargs: Any,
    ) -> BaseModel:
        """Structured call: validate JSON, retry once on schema failure.

        The retry drops the response_format constraint and relies on an
        explicit ONLY-JSON instruction instead, so a systematic failure of
        one mechanism (e.g. a provider that mishandles json_object mode)
        does not doom both attempts.
        """
        strict = ChatMessage(
            role="user",
            content=(
                "Your last reply was not valid JSON for the required schema. "
                "Reply with ONLY the JSON object, no prose or fences."
            ),
        )
        attempts: list[tuple[list[Any], bool]] = [
            (list(messages), True),
            ([*messages, strict], False),
        ]
        last_error: ValidationError | None = None
        last_empty: EmptyContent | None = None
        for wire, use_json_mode in attempts:
            try:
                resp = self.complete(role, wire, json_mode=use_json_mode, **kwargs)
            except EmptyContent as e:
                last_empty, last_error = e, None
                continue
            try:
                return schema.model_validate_json(resp.content)
            except ValidationError as e:
                last_error, last_empty = e, None
                continue
        if last_empty is not None:
            raise SchemaFailed(
                f"{role} JSON failed schema {schema.__name__}: empty reply "
                f"(finish_reason={last_empty.finish_reason!r}, "
                f"tokens={last_empty.tokens_in}+{last_empty.tokens_out})"
            )
        assert last_error is not None  # loop always sets exactly one of the two
        raise SchemaFailed(
            f"{role} JSON failed schema {schema.__name__}: {last_error}"
        ) from last_error
