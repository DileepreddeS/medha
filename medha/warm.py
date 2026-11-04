"""Warm the Glimmer endpoint (and verify it answers).

Run: ``uv run python -m medha.warm`` — use before benches and demos so the
first real call never pays the scale-from-zero boot. Retries 502/503/504
for up to GLIMMER_RETRY_BUDGET_S (default ~6 min), same as every Glimmer
backend call.
"""

from __future__ import annotations

import sys
import time


def main() -> int:
    from medha.config import settings
    from medha.llm.client import OpenAIBackend

    if not settings.GLIMMER_BASE_URL:
        print("GLIMMER_BASE_URL is not set; nothing to warm.")
        return 1
    backend = OpenAIBackend(
        settings.GLIMMER_BASE_URL,
        settings.GLIMMER_API_KEY,
        reasoning_guard=True,
        retry_503=True,
        retry_budget_s=settings.GLIMMER_RETRY_BUDGET_S,
    )
    start = time.monotonic()
    try:
        content, tok_in, tok_out = backend.chat(
            model=settings.GLIMMER_SERVED_MODEL,
            messages=[{"role": "user", "content": "Reply: ok"}],
            max_tokens=2048,  # reasoning headroom: small caps length-cap
            temperature=0.0,
            json_mode=False,
        )
    except Exception as e:
        print(f"warm FAILED: {e}")
        return 1
    latency_ms = int((time.monotonic() - start) * 1000)
    print(f"warm ok: {settings.GLIMMER_SERVED_MODEL} {tok_in}+{tok_out}tok {latency_ms}ms")
    print(f"reply: {content.strip()[:120]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
