"""Tests for the secret-redaction helper.

Anything that surfaces in the UI or a log file goes through ``scrub``
first; if these tests stop covering a real-world key shape, real keys
will leak into error messages.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the converter_app package importable when this file is run via
# pytest from the repo root.
_AGENT_DIR = Path(__file__).resolve().parent.parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from converter_app.secrets_redact import REDACTED, scrub


class TestScrub:
    def test_openai_key_redacted(self):
        text = "auth failed for sk-proj_AAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert "sk-proj_" not in scrub(text)
        assert REDACTED in scrub(text)

    def test_openai_service_account_key_redacted(self):
        text = "Authorization: Bearer sk-svcacct_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        out = scrub(text)
        assert "sk-svcacct" not in out
        assert "Bearer" not in out or REDACTED in out

    def test_anthropic_key_redacted(self):
        text = "key=sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert "sk-ant" not in scrub(text)

    def test_authorization_header_redacted(self):
        text = "request failed with header Authorization: Bearer abc-1234567890_XYZ"
        assert "Authorization: Bearer" not in scrub(text)

    def test_idempotent(self):
        text = "error: sk-AAAAAAAAAAAAAAAAAAAAAAAA"
        once = scrub(text)
        twice = scrub(once)
        assert once == twice

    def test_empty_safe(self):
        assert scrub("") == ""
        assert scrub(None) is None    # type: ignore[arg-type]

    def test_innocuous_text_passes_through(self):
        # Things that LOOK secret-ish but aren't a real shape stay put.
        assert scrub("the user clicked Save") == "the user clicked Save"
        assert scrub("see error #1234") == "see error #1234"

    def test_short_sk_not_falsely_matched(self):
        # ``sk-foo`` is below our minimum length — leave alone.
        assert "sk-foo" in scrub("the variable sk-foo holds something")


class TestEnvNotPolluted:
    """Verify the conversion worker doesn't write the API key to
    ``os.environ`` (which could leak via crash reports or subprocess
    inspection)."""

    def test_worker_passes_key_via_constructor(self, monkeypatch):
        # Capture what gets written to os.environ during a worker run.
        import os
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        from converter_app.conversion_worker import ConversionWorker

        # Stub out the v2 imports so we don't actually call the LLM.
        # We only care that the worker's run() path doesn't leak the
        # key into os.environ.
        import importlib
        import sys as _sys

        captured_args = {}

        class _FakeOpenAILlmClient:
            def __init__(self, **kwargs):
                captured_args.update(kwargs)

        class _FakeLlmFallback:
            def __init__(self, **kwargs): pass
            def convert(self, *_a, **_kw): return None

        # Build a fake v2.engine.llm_fallback module.
        fake_module = type(_sys)("v2.engine.llm_fallback")
        fake_module.OpenAILlmClient = _FakeOpenAILlmClient
        fake_module.LlmFallback = _FakeLlmFallback
        monkeypatch.setitem(_sys.modules, "v2.engine.llm_fallback", fake_module)

        # Stub out the rest of the v2 entry points the worker imports.
        fake_pipeline = type(_sys)("v2.pipeline")
        fake_pipeline.convert_template = lambda *a, **kw: object()
        monkeypatch.setitem(_sys.modules, "v2.pipeline", fake_pipeline)

        fake_grammar = type(_sys)("v2.grammar.loaders")
        fake_grammar.load_org_overrides = lambda *_a, **_kw: None
        monkeypatch.setitem(_sys.modules, "v2.grammar.loaders", fake_grammar)

        fake_loader = type(_sys)("v2.patterns.loader")
        class _FakeReport:
            patterns = []
        fake_loader.load_library = lambda *_a, **_kw: _FakeReport()
        monkeypatch.setitem(_sys.modules, "v2.patterns.loader", fake_loader)

        worker = ConversionWorker(
            rtf="%[X]",
            org="oba",
            api_key="sk-test_PLEASE_DO_NOT_LEAK_ME_AAAAAAAAAA",
            model="gpt-4o-mini",
            base_url="https://example.invalid",
        )
        # Run synchronously (worker.run() is plain Python, no thread needed).
        worker.run()

        # The key is in our fake client's constructor — that's how it
        # SHOULD travel. It must NOT be in os.environ.
        assert captured_args.get("api_key") == "sk-test_PLEASE_DO_NOT_LEAK_ME_AAAAAAAAAA"
        assert "OPENAI_API_KEY" not in os.environ
        assert "OPENAI_MODEL" not in os.environ
        assert "OPENAI_BASE_URL" not in os.environ
