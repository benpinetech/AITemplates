"""RTF rendering utility.

Tries the .NET server's rtf-to-html endpoint first.
Falls back to striprtf if it's unavailable.
"""
import re
import requests
from striprtf.striprtf import rtf_to_text

_dotnet_url: str | None = None
_dotnet_available: bool = False


def init_renderer(dotnet_base_url: str = "http://localhost:5000") -> bool:
    """Probe the .NET server. Returns True if reachable."""
    global _dotnet_url, _dotnet_available
    _dotnet_url = dotnet_base_url.rstrip("/")
    try:
        resp = requests.post(
            f"{_dotnet_url}/api/migration/rtf-to-html",
            json={"content": "{\\rtf1 test}"},
            timeout=2,
        )
        _dotnet_available = resp.status_code == 200
    except Exception:
        _dotnet_available = False
    return _dotnet_available


def render_rtf(content: str) -> tuple[str, str]:
    """Render RTF to displayable content.

    Returns:
        (output, method) where method is 'dotnet', 'striprtf', or 'raw'.
        When method is 'dotnet', output is HTML.
        Otherwise output is plain text.
    """
    if not content:
        return "", "raw"

    if _dotnet_available and _dotnet_url:
        try:
            resp = requests.post(
                f"{_dotnet_url}/api/migration/rtf-to-html",
                json={"content": content},
                timeout=10,
            )
            if resp.status_code == 200:
                html = resp.json().get("html", "")
                if html:
                    return html, "dotnet"
        except Exception:
            pass

    try:
        text = rtf_to_text(content)
        return text, "striprtf"
    except Exception:
        return content, "raw"


def highlight_legacy(text: str) -> str:
    """Wrap %[...] tokens in an orange highlight (legacy)."""
    return re.sub(
        r"(%\[[^\]]*\])",
        r'<mark style="background:#ffb347;color:#000;padding:1px 3px;border-radius:2px">\1</mark>',
        text,
    )


def highlight_pine(text: str) -> str:
    """Wrap @[...] tokens in a yellow highlight (generated)."""
    return re.sub(
        r"(@\[[^\]]*\])",
        r'<mark style="background:#f5ff6e;color:#000;padding:1px 3px;border-radius:2px">\1</mark>',
        text,
    )


def highlight_ground_truth(text: str) -> str:
    """Wrap @[...] tokens in a green highlight (ground truth)."""
    return re.sub(
        r"(@\[[^\]]*\])",
        r'<mark style="background:#6ee89a;color:#000;padding:1px 3px;border-radius:2px">\1</mark>',
        text,
    )
