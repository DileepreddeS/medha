"""Secret redaction for logs and stored payloads."""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"LLM\|\d+\|[A-Za-z0-9._-]+"),
    re.compile(r"hf_[A-Za-z0-9]+"),
    re.compile(r"sk-[A-Za-z0-9-]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)((?:api[_-]?key|bearer|hf_token|meta_api_key|medha_token|glimmer_api_key)"
        r"\s*[:=]\s*)\S+"
    ),
]


def redact(text: str) -> str:
    out = text
    for rx in _PATTERNS:
        if rx.groups:
            out = rx.sub(lambda m: m.group(1) + "[REDACTED]", out)
        else:
            out = rx.sub("[REDACTED]", out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact(str(v)) for k, v in record.args.items()}
            else:
                record.args = tuple(redact(str(a)) for a in record.args)  # type: ignore[assignment]
        return True
