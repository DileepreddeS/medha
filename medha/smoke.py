"""P0 connectivity checks.

Run: ``uv run python -m medha.smoke``
Pass: Glimmer replies, Meta (Spark 1.3) replies, sandbox runs hello-world,
and a network probe from inside the sandbox FAILS (egress blocked).
"""

from __future__ import annotations

import sys
import traceback

HELLO_SKILL = '''"""Trusted smoke-test skill (written by us, not generated)."""
def run(inputs, workdir):
    return {"hello": "world"}
'''

NET_PROBE_SKILL = '''"""Trusted probe: must NOT reach the network (block_network=True)."""
import urllib.request


def run(inputs, workdir):
    try:
        with urllib.request.urlopen("https://example.com", timeout=5) as r:
            return {"reached": True, "status": r.status}
    except Exception as e:
        return {"reached": False, "error": type(e).__name__}
'''


def check(name: str, fn: object) -> bool:
    try:
        detail = fn()  # type: ignore[operator]
    except Exception:
        print(f"[FAIL] {name}")
        traceback.print_exc()
        return False
    print(f"[ok] {name}: {detail}")
    return True


def main() -> int:
    import time

    from medha.config import settings
    from medha.llm.client import ChatMessage, LLMClient, OpenAIBackend

    ok = True
    client = LLMClient(settings)

    def glimmer() -> str:
        # Bypass role routing on purpose: the planner role may temporarily
        # point at Meta (LLM_AUTHOR_PROVIDER), but this check must probe the
        # real Glimmer endpoint. Retry rides out scale-to-zero cold boots;
        # the token budget covers reasoning (64 length-capped: 59+64).
        backend = OpenAIBackend(
            settings.GLIMMER_BASE_URL,
            settings.GLIMMER_API_KEY,
            reasoning_guard=True,
            retry_503=True,
            retry_budget_s=settings.GLIMMER_RETRY_BUDGET_S,
        )
        start = time.monotonic()
        content, tok_in, tok_out = backend.chat(
            model=settings.GLIMMER_SERVED_MODEL,
            messages=[{"role": "user", "content": "Reply: pong"}],
            max_tokens=2048,
            temperature=0.0,
            json_mode=False,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        assert "pong" in content.lower(), f"unexpected reply: {content!r}"
        return f"{settings.GLIMMER_SERVED_MODEL} {tok_in}+{tok_out}tok {latency_ms}ms"

    def meta() -> str:
        r = client.complete("judge", [ChatMessage(role="user", content="Reply: pong")])
        assert "pong" in r.content.lower(), f"unexpected reply: {r.content!r}"
        return f"{r.model} {r.tokens_in}+{r.tokens_out}tok ${r.cost_usd:.4f}"

    ok &= check("glimmer (self-hosted vLLM)", glimmer)
    ok &= check("meta api (muse-spark-1.3)", meta)

    from medha.sandbox.runner import ModalSandboxRunner

    runner = ModalSandboxRunner()

    def sandbox_hello() -> str:
        out = runner.run_skill(HELLO_SKILL, {})
        assert out.ok, f"sandbox run failed: {out.error}\n{out.stderr}"
        assert out.result.get("hello") == "world", f"bad result: {out.result!r}"
        return "hello=world"

    def sandbox_blocked() -> str:
        out = runner.run_skill(NET_PROBE_SKILL, {})
        assert out.ok, f"probe skill crashed: {out.error}\n{out.stderr}"
        assert out.result.get("reached") is False, (
            f"NETWORK NOT BLOCKED: {out.result!r} (block_network=True required)"
        )
        return f"egress blocked ({out.result.get('error')})"

    ok &= check("sandbox hello-world", sandbox_hello)
    ok &= check("sandbox network blocked", sandbox_blocked)

    print("SMOKE PASS" if ok else "SMOKE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
