"""LLM fallback for chunks the pattern engine doesn't match.

The fallback runs on AST-only inputs — never prose. The prompt the
LLM sees is assembled from four constrained sources:

  1. The unmatched JDA token's text (its ``.unparse()``).
  2. The org's vocabulary allow-list.
  3. A handful of verified-similar (JDA, Pine) example patterns
     retrieved from the active library.
  4. A relevant grammar fragment.

This file ships:

  - ``LlmClient``  — Protocol every client conforms to.
  - ``MockLlmClient`` — canned-response client for tests.
  - ``AnthropicLlmClient`` — adapter for the Anthropic SDK,
    activated only when the SDK is importable AND an API key is set.
  - ``FallbackRequest`` — immutable bundle of (input + context),
    with ``assemble_prompt()`` and ``parse_response()`` methods that
    are unit-testable independently of any real LLM.
  - ``LlmFallback`` — top-level callable. Holds the client and the
    static context (library, vocabulary), produces ``PineToken``
    outputs (or None when parsing fails).

The privacy invariant — "the prompt contains only AST, vocabulary,
patterns, grammar, and the constant framing text" — is enforced
structurally by ``assemble_prompt()`` (it doesn't take any other
inputs). A test in ``test_engine_llm_fallback.py`` audits the
assembled prompt for an expected shape.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Sequence

from ..grammar.loaders import OrgRoot, OrgVocabulary
from ..parser import jda_parser, pine_parser
from ..parser.jda_ast import JdaToken
from ..parser.pine_ast import PineToken
from ..patterns.schema import Pattern


# ─────────────────────────────────────────────────────────────────────────────
# Client interface and implementations
# ─────────────────────────────────────────────────────────────────────────────


class LlmClient(Protocol):
    """Minimal interface a client must implement.

    A complete prompt goes in; a single string response comes out. The
    LlmFallback layer handles parsing the response into a PineToken
    and returns None if parsing fails.
    """

    def complete(self, prompt: str) -> str: ...


class MockLlmClient:
    """Stub client for unit tests. Pass a function that maps a prompt
    to a canned response, or a dict of substring-keyed responses."""

    def __init__(self, responder):
        if callable(responder):
            self._fn = responder
        elif isinstance(responder, dict):
            def fn(prompt: str) -> str:
                for key, val in responder.items():
                    if key in prompt:
                        return val
                raise KeyError(
                    f"MockLlmClient has no canned response for prompt; "
                    f"keys tried: {list(responder)}"
                )
            self._fn = fn
        else:
            raise TypeError(
                "MockLlmClient takes either a callable or a dict mapping "
                "prompt-substring → response"
            )

    def complete(self, prompt: str) -> str:
        return self._fn(prompt)


class OpenAILlmClient:
    """Adapter for the OpenAI Python SDK (and any OpenAI-API-compatible
    endpoint such as Anyscale, Together, Fireworks, etc.).

    Defaults:

      - model:   ``gpt-4o-mini`` (cheap, fast). Override via
                 ``OPENAI_MODEL`` env var or constructor arg.
      - api key: read from ``OPENAI_API_KEY`` by default.
      - base url: read from ``OPENAI_BASE_URL`` if set; otherwise the
                  SDK default (api.openai.com). Set ``OPENAI_BASE_URL``
                  to point at an OpenAI-compatible third-party endpoint.

    Imports the ``openai`` package lazily so it only has to be
    installed when this client is actually used.
    """

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        try:
            import openai  # noqa: F401 — availability check
        except ImportError as e:
            raise RuntimeError(
                "OpenAILlmClient requires the `openai` SDK. "
                "Install with: pip install openai"
            ) from e
        self._model = model or os.environ.get("OPENAI_MODEL", self.DEFAULT_MODEL)
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "OpenAILlmClient needs an API key. Pass api_key=… or set "
                "OPENAI_API_KEY."
            )
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL") or None

    def complete(self, prompt: str) -> str:
        from openai import OpenAI
        kwargs = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = OpenAI(**kwargs)
        # max_tokens / max_completion_tokens both work depending on
        # SDK version; use the modern name and let the SDK raise if
        # unsupported. Temperature kept low: this is structural
        # translation, not creative writing.
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=1024,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""


# ─────────────────────────────────────────────────────────────────────────────
# Few-shot retrieval
# ─────────────────────────────────────────────────────────────────────────────


def _tokenize_text(s: str) -> List[str]:
    """Cheap token split for similarity scoring — letters and digits
    runs separated by anything else."""
    out: List[str] = []
    cur: List[str] = []
    for c in s:
        if c.isalnum():
            cur.append(c)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return [t for t in out if t]


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity over tokenized strings. Cheap, deterministic,
    no embedding model required. Good enough to bias few-shot retrieval
    toward patterns with overlapping function/field names."""
    sa = set(_tokenize_text(a.lower()))
    sb = set(_tokenize_text(b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _select_few_shot(
    library: Sequence[Pattern],
    target: str,
    org: str,
    k: int = 5,
) -> List[Pattern]:
    """Pick up to ``k`` patterns most similar to ``target`` (the
    unparsed JDA expression). Filtered to applicable patterns for
    ``org``, sorted by Jaccard score on the match string."""
    candidates = [
        p for p in library
        if p.org_context in ("any", org) and not p.is_chunk_pattern()
    ]
    scored = []
    for p in candidates:
        match_text = p.match if isinstance(p.match, str) else " ".join(p.match)
        score = _similarity(target, match_text)
        scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [p for score, p in scored[:k] if score > 0]


# ─────────────────────────────────────────────────────────────────────────────
# Prompt assembly
# ─────────────────────────────────────────────────────────────────────────────


# The framing text is constant across all calls so the prompt cache
# can amortise it. Keep this stable; if you change wording, plan a
# cache flush.
_FRAMING_HEADER = """\
You are a syntactic translator from JDA template syntax (%[...]) to
Pine template syntax (@[...]). You see ONLY bracketed expressions —
never prose, names, or document content.

Output a single Pine @[...] expression that converts the input JDA
expression. Use only names that appear in the allowed-vocabulary list.
Do not invent new entity names. Do not wrap the output in any
explanatory text.

The verified examples below use angle-bracket placeholders like
<entity> to mark where a real name slots in — DO NOT copy those
placeholders or angle brackets verbatim into your output. Substitute
the actual name from the input expression.
"""

# Pattern source files use ``$entity`` to mark a named hole. If we
# drop that syntax verbatim into the LLM prompt, the model picks it
# up and emits ``@[$Foo.first.…]`` — the dollar leaks into the
# converted Pine output. Replacing ``$name`` with ``<name>`` tells
# the model it's a placeholder, not a literal.
_HOLE_PLACEHOLDER_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")


def _scrub_pattern_holes(text: str) -> str:
    return _HOLE_PLACEHOLDER_RE.sub(r"<\1>", text)


_SECTION_INPUT = "INPUT (JDA expression to convert):"
_SECTION_VOCAB = "ALLOWED PINE VOCABULARY (entity / builtin / prompt names):"
_SECTION_FEWSHOT = "VERIFIED EXAMPLES (use these as reference for shape and style):"
_SECTION_GRAMMAR = "RELEVANT PINE GRAMMAR FRAGMENT:"
_SECTION_OUTPUT = "OUTPUT (one Pine @[...] expression, no other text):"


@dataclass(frozen=True)
class FallbackRequest:
    """One LLM request, fully assembled. ``assemble_prompt()`` is
    deterministic — same fields produce the same prompt — which makes
    the privacy and shape invariants testable."""

    jda_token: JdaToken
    org: str
    vocabulary: OrgVocabulary
    few_shot: List[Pattern] = field(default_factory=list)
    grammar_fragment: str = ""

    def assemble_prompt(self) -> str:
        parts: List[str] = [_FRAMING_HEADER, ""]
        parts.append(_SECTION_INPUT)
        parts.append(self.jda_token.unparse())
        parts.append("")
        parts.append(_SECTION_VOCAB)
        parts.append("entities: " + ", ".join(self.vocabulary.entities))
        parts.append("builtins: " + ", ".join(self.vocabulary.builtins))
        parts.append("prompt_variables: " + ", ".join(self.vocabulary.prompt_variables))
        parts.append("")
        if self.few_shot:
            parts.append(_SECTION_FEWSHOT)
            for p in self.few_shot:
                match_text = p.match if isinstance(p.match, str) else " | ".join(p.match)
                rewrite_text = (
                    p.rewrite if isinstance(p.rewrite, str)
                    else " | ".join(p.rewrite or [])
                ) if p.rewrite else f"<rewrite_function: {p.rewrite_function}>"
                # Convert pattern-source $holes to <placeholders> so
                # the LLM doesn't echo the $ back in its output.
                match_text = _scrub_pattern_holes(match_text)
                rewrite_text = _scrub_pattern_holes(rewrite_text)
                parts.append(f"  {match_text}  →  {rewrite_text}")
            parts.append("")
        if self.grammar_fragment:
            parts.append(_SECTION_GRAMMAR)
            parts.append(self.grammar_fragment.rstrip())
            parts.append("")
        parts.append(_SECTION_OUTPUT)
        return "\n".join(parts)

    @staticmethod
    def parse_response(response: str) -> Optional[PineToken]:
        """Pull a single ``@[...]`` token out of the LLM's reply.

        Tolerates leading / trailing whitespace, fenced code blocks, and
        explanatory paragraphs (we take the first balanced ``@[...]``
        substring). Returns None if no balanced expression is found or
        if the Pine parser rejects it."""
        text = response.strip()
        # Strip markdown fences if present.
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        start = text.find("@[")
        if start < 0:
            return None
        depth = 1
        i = start + 2
        while i < len(text) and depth > 0:
            c = text[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            i += 1
        if depth != 0:
            return None
        candidate = text[start:i]
        try:
            return pine_parser.parse(candidate)
        except pine_parser.PineParseError:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Top-level fallback
# ─────────────────────────────────────────────────────────────────────────────


class LlmFallback:
    """Convert one unmatched JDA token via the LLM.

    Construct once with the static library + org override; call
    ``convert(jda_token)`` for each unmatched chunk. Returns None if
    the LLM response can't be parsed as a single Pine token — the
    caller treats that as "still unmatched" and surfaces it to the
    mapper.
    """

    def __init__(
        self,
        client: LlmClient,
        library: Sequence[Pattern],
        org_overrides: OrgRoot,
        few_shot_count: int = 5,
        grammar_fragment: str = "",
    ):
        self._client = client
        self._library = list(library)
        self._org = org_overrides
        self._few_shot_count = few_shot_count
        self._grammar_fragment = grammar_fragment

    def build_request(self, jda_token: JdaToken) -> FallbackRequest:
        few_shot = _select_few_shot(
            self._library,
            jda_token.unparse(),
            self._org.id,
            k=self._few_shot_count,
        )
        return FallbackRequest(
            jda_token=jda_token,
            org=self._org.id,
            vocabulary=self._org.vocabulary,
            few_shot=few_shot,
            grammar_fragment=self._grammar_fragment,
        )

    def convert(self, jda_token: JdaToken) -> Optional[PineToken]:
        req = self.build_request(jda_token)
        prompt = req.assemble_prompt()
        response = self._client.complete(prompt)
        return FallbackRequest.parse_response(response)
