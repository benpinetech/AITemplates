"""Defensive redaction of secrets from text the user may see.

The user's OpenAI key never leaves the OS keychain except to be passed
to the SDK in-memory. But software failures are creative — a network
error inside the SDK might include the auth header, an HTTP redirect
might echo the key, an unexpected traceback might show a request
URL with the key in it. To make sure none of that ever ends up in a
QMessageBox / status bar / log file, every string that surfaces to
the user goes through ``scrub`` first.

The pattern matches OpenAI-style secrets (``sk-…``), Anthropic-style
secrets (``sk-ant-…``), and bearer tokens. It's deliberately generous
on the upper bound so future key formats are also caught.
"""

from __future__ import annotations

import re

# Match common API-key shapes:
#   OpenAI:    sk-…proj_…   sk-svcacct…   etc., 20+ characters
#   Anthropic: sk-ant-api…
#   Bearer:    Authorization: Bearer …
_SECRET_PATTERNS = [
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"Authorization:\s*[^\s]+", re.IGNORECASE),
]

REDACTED = "[REDACTED]"


def scrub(text: str) -> str:
    """Replace any secret-shaped substring with ``[REDACTED]``.

    Idempotent — calling it twice has no extra effect.
    """
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out
