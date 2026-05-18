"""LLM converter — core conversion engine for JDA → Pine translation.

The converter runs on AST-only inputs — never prose. The prompt the
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
  - ``ConversionRequest`` — immutable bundle of (input + context),
    with ``assemble_prompt()`` and ``parse_response()`` methods that
    are unit-testable independently of any real LLM.
  - ``LlmConverter`` — top-level callable. Holds the client and the
    static context (library, vocabulary), produces ``PineToken``
    outputs (or None when parsing fails).

The privacy invariant — "the prompt contains only AST, vocabulary,
patterns, grammar, and the constant framing text" — is enforced
structurally by ``assemble_prompt()`` (it doesn't take any other
inputs). A test in ``test_engine_llm_converter.py`` audits the
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
from . import audience as _audience
from .audience import classify_document_audience


# ─────────────────────────────────────────────────────────────────────────────
# Client interface and implementations
# ─────────────────────────────────────────────────────────────────────────────


class LlmClient(Protocol):
    """Minimal interface a client must implement.

    A complete prompt goes in; a single string response comes out. The
    LlmConverter layer handles parsing the response into a PineToken
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

      - model:   ``gpt-5.5`` — the production-default. Set ``OPENAI_MODEL``
                 to ``gpt-5.4-mini`` for cheap/fast runs, or to a specific
                 dated model id when you need reproducibility.
      - api key: read from ``OPENAI_API_KEY`` by default.
      - base url: read from ``OPENAI_BASE_URL`` if set; otherwise the
                  SDK default (api.openai.com). Set ``OPENAI_BASE_URL``
                  to point at an OpenAI-compatible third-party endpoint.

    Imports the ``openai`` package lazily so it only has to be
    installed when this client is actually used.
    """

    DEFAULT_MODEL = "gpt-5.5"

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
        return self.complete_with_tools(prompt, tools=None)

    def complete_with_tools(
        self,
        prompt: str,
        tools: Optional[dict] = None,
        max_turns: int = 10,
        response_format: Optional[dict] = None,
    ) -> str:
        """Run an OpenAI chat completion, optionally with tool-calling.

        ``tools`` is a dict mapping ``function_name → callable(query: str) -> str``.
        When set, the model can request tool calls and the client iterates
        until no more tool calls are made (or ``max_turns`` is reached).

        This is how v1's mapping_call works: the LLM sees the prompt,
        decides what to look up, calls ``search_pine_syntax(query)``,
        gets results, and may call again before producing a final
        answer. Returns the LLM's final assistant message text.
        """
        from openai import OpenAI
        kwargs = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = OpenAI(**kwargs)

        tool_specs = None
        if tools:
            tool_specs = []
            for name, fn in tools.items():
                tool_specs.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (fn.__doc__ or "").strip().split("\n", 1)[0],
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description":
                                        "Search query for the Pine syntax reference. "
                                        "Pass a JDA token, entity name, or function name.",
                                },
                            },
                            "required": ["query"],
                        },
                    },
                })

        messages = [{"role": "user", "content": prompt}]
        for _ in range(max_turns):
            req = {
                "model": self._model,
                "messages": messages,
            }
            # gpt-5 family uses max_completion_tokens and ignores temperature.
            # Older models use max_tokens + temperature. We pass both forms
            # via try-and-fallback to stay compatible across model lines.
            try:
                response = client.chat.completions.create(
                    **req,
                    max_completion_tokens=4096,
                    tools=tool_specs,
                    response_format=response_format,
                )
            except Exception:
                # Older models reject either ``response_format`` strict
                # mode or the gpt-5-style param. Drop both and retry.
                response = client.chat.completions.create(
                    **req,
                    max_tokens=4096,
                    temperature=0.0,
                    tools=tool_specs,
                )

            if not response.choices:
                return ""
            choice = response.choices[0].message

            # No tool calls → final answer.
            if not getattr(choice, "tool_calls", None):
                return choice.content or ""

            # Append the assistant's tool-call message and the tool
            # results, then loop. Need to convert to dict form for the
            # next request because the SDK's ChatCompletionMessage
            # objects don't always round-trip cleanly.
            messages.append({
                "role": "assistant",
                "content": choice.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.tool_calls
                ],
            })
            for tc in choice.tool_calls:
                fn = tools.get(tc.function.name)
                if fn is None:
                    result = f"error: unknown tool {tc.function.name!r}"
                else:
                    import json as _json
                    try:
                        args = _json.loads(tc.function.arguments or "{}")
                        result = str(fn(**args))
                    except Exception as e:
                        result = f"error: {e}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        # Hit the iteration cap — return whatever the last assistant said.
        return choice.content or ""


# ─────────────────────────────────────────────────────────────────────────────
# RAG search tool — port of v1's search_pine_syntax
# ─────────────────────────────────────────────────────────────────────────────
# Lazy-loaded singleton: building / loading the chroma DB is slow and
# every LlmConverter instance shares the same backing store.

_RAG_DB = None
_RAG_DB_LOAD_FAILED = False


def _get_rag_db():
    """Return the v1 chroma vector store, loading it on first use.
    Returns None if the load fails (chroma not installed, DB missing, etc.).
    """
    global _RAG_DB, _RAG_DB_LOAD_FAILED
    if _RAG_DB is not None or _RAG_DB_LOAD_FAILED:
        return _RAG_DB
    try:
        # Import lazily to keep the import-time cost off the v2 hot
        # path. v1 already provides the loader; reuse it.
        import sys as _sys
        from pathlib import Path as _Path
        agent_dir = _Path(__file__).resolve().parent.parent.parent
        src_dir = agent_dir / "src"
        if str(src_dir) not in _sys.path:
            _sys.path.insert(0, str(src_dir))
        from utils import load_vector_store  # type: ignore
        _RAG_DB = load_vector_store()
    except Exception as e:  # noqa: BLE001
        import sys as _sys
        print(f"warning: RAG DB unavailable, LLM will run without it: {e}",
              file=_sys.stderr)
        _RAG_DB_LOAD_FAILED = True
    return _RAG_DB


def search_pine_syntax(query: str) -> str:
    """Search the Pine syntax reference database to find the correct
    Pine syntax for a legacy template variable or function.

    Returns the top 5 matching reference chunks joined by separators.
    Empty string if RAG is unavailable.
    """
    db = _get_rag_db()
    if db is None:
        return "(RAG unavailable)"
    try:
        results = db.similarity_search(query, k=5)
    except Exception as e:  # noqa: BLE001
        return f"(RAG query failed: {e})"
    if not results:
        return "(no results)"
    return "\n\n---\n\n".join(doc.page_content for doc in results)


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

Pine has a UNIVERSAL system enum of involvement and assignment role
codes (listed below) — same across all deployments. When you see a
JDA entity prefix that isn't in the per-org translation table, find
the closest semantic match in this universal enum and use its Pine
display name. ``DEFENDANT`` / ``DEFENSEATTORNEY`` / ``PROSECUTINGATTORNEY``
etc. are standardized codes; the Pine variable name is the same
unless a deployment renames it.

You may have a tool named ``search_pine_syntax(query)`` available.
When the enum doesn't disambiguate or you need field-level syntax,
USE IT — pass the JDA entity name, field name, or function name,
and the tool returns Pine syntax reference excerpts. Multiple search
calls are fine.

Output Pine ``@[...]`` expressions. Use only names that appear in
the allowed-vocabulary list (or in RAG-returned reference text). Do
not invent new entity names. Do not wrap the output in any
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


_SECTION_ROLE_ENUM = (
    "PINE SYSTEM ROLE ENUM (UNIVERSAL — same across all deployments).\n"
    "\n"
    "Pine separates people on a case into two tables:\n"
    "  - **CaseAssignment** — legal-side actors (attorneys, judges, clerks,\n"
    "    law enforcement, investigators, court staff). These USUALLY have\n"
    "    Pine CMS user accounts. Child records (addresses, phones, emails)\n"
    "    hang off the Personnel table → child CreateVars filter by\n"
    "    `PersonnelID`.\n"
    "  - **CaseInvolvement** — public parties on the case (victims,\n"
    "    witnesses, defendants, complainants, claimants). Child records\n"
    "    hang off the Name table → child CreateVars filter by `NameID`.\n"
    "\n"
    "Each row has a TYPE CODE (the unique identifier of the role) and\n"
    "a MASTERCODE (a coarser grouping label, e.g. \"Law Enforcement\"\n"
    "or \"Prosecution\"). A Pine variable can filter rows two ways:\n"
    "  - by specific type:  `\"Type\":\"DEPPROS\"` — one row family\n"
    "  - by master group:   `\"MasterCode\":\"Law Enforcement\"` — any\n"
    "    row whose type code lives in that MasterCode group\n"
    "\n"
    "When the JDA token names a SPECIFIC role (e.g. `JW_DistrictAtty`),\n"
    "prefer the Type filter. When the JDA token names a GENERIC role\n"
    "that could match multiple specific types (e.g. `JW_Officer` —\n"
    "could be any Law Enforcement assignment, `JW_Vic` — any victim-class\n"
    "involvement), prefer the MasterCode filter.\n"
    "\n"
    "The Pine VARIABLE NAME is typically a CamelCase or shorthand\n"
    "derivation of the code/MasterCode (e.g. `DISTRICTATTY` →\n"
    "`DistrictAtty` / `DistrictAttorney`; MasterCode \"Law Enforcement\"\n"
    "→ `Officer` / `LawEnforcement`). The display name in parentheses\n"
    "below is descriptive, NOT necessarily the variable name — deployments\n"
    "choose. When in doubt, use a short CamelCase derivation\n"
    "(`ProsAtty`, `DefAtty`, `Officer`).\n"
)
_SECTION_VOCAB = "ALLOWED PINE VOCABULARY (entity / builtin / prompt names):"
_SECTION_SHAPE_EXAMPLES = (
    "PINE SYNTAX SHAPE TEMPLATES (use the shape, substitute "
    "the real entity for the <X> placeholder — the entity hint "
    "below tells you which entity to use):"
)
_SECTION_FEWSHOT = "VERIFIED EXAMPLES (use these as reference for shape and style):"
_SECTION_GRAMMAR = "RELEVANT PINE GRAMMAR FRAGMENT:"
_SECTION_HINT = "ENTITY HINT (the entity in the input token resolves to this Pine entity):"
_SECTION_DOC_CONTEXT = (
    "DOCUMENT CONTEXT (counts of entity prefixes among siblings in this batch — "
    "use this to disambiguate which entity an ambiguous reference belongs to):"
)
_SECTION_DOC_AUDIENCE = (
    "DOCUMENT AUDIENCE (the primary subject of this document; ambiguous "
    "address / pronoun / role references SHOULD bind to this entity):"
)
_SECTION_INPUT = "INPUT (JDA expression to convert):"
_SECTION_OUTPUT = "OUTPUT (one Pine @[...] expression, no other text):"


def _input_entity_hint(jda_token: "JdaToken", jda_to_pine: dict) -> Optional[str]:
    """If the input's leading entity is a known JDA name, return a hint
    line resolving it to the Pine entity. Saves the LLM the lookup and
    eliminates the most common error mode (wrong entity rename)."""
    text = jda_token.unparse()
    # Pull the leading bare-identifier from the inner expression.
    m = re.search(r"%\[\s*(?:[A-Za-z]+\(\s*)?([A-Za-z_][A-Za-z0-9_]*)", text)
    if not m:
        return None
    candidate = m.group(1)
    pine = jda_to_pine.get(candidate)
    if pine is None:
        return None
    return f"  {candidate}  →  {pine}"


def _leading_entity(jda_token: "JdaToken") -> Optional[str]:
    """Extract the leading bare-identifier from a JDA token's source.
    Returns None when the token doesn't start with an entity reference."""
    return _audience._leading_entity(jda_token)


def _document_context_summary(
    jda_tokens: Sequence["JdaToken"], jda_to_pine: dict
) -> Optional[str]:
    """Summarise which Pine entities dominate the batch.

    Helps the LLM disambiguate context-dependent references — when the
    same JDA token text could plausibly map to ``Complainant`` or
    ``Respondent``, the dominant Pine entity in the surrounding batch
    is usually the right answer. Returns None when the batch has no
    recognisable entities.
    """
    counts: dict = {}
    for tok in jda_tokens:
        leading = _leading_entity(tok)
        if leading is None:
            continue
        pine = jda_to_pine.get(leading)
        if pine is None:
            continue
        counts[pine] = counts.get(pine, 0) + 1
    if not counts:
        return None
    # Sort high-to-low for readability; Python sort is stable so ties
    # come out alphabetically.
    items = sorted(sorted(counts.items()), key=lambda kv: -kv[1])
    return "\n".join(f"  {n:3d} × {pine}" for pine, n in items)


@dataclass(frozen=True)
class ConversionRequest:
    """One LLM request, fully assembled. ``assemble_prompt()`` is
    deterministic — same fields produce the same prompt — which makes
    the privacy and shape invariants testable."""

    jda_token: JdaToken
    org: str
    vocabulary: OrgVocabulary
    few_shot: List[Pattern] = field(default_factory=list)
    grammar_fragment: str = ""
    shape_examples: tuple = ()    # tuple of (match, rewrite) pairs

    def assemble_prompt(self) -> str:
        # Section ordering is deliberate: ALL constant-per-org content
        # comes BEFORE per-token variable content, so the OpenAI prompt
        # cache hits the rules+vocab+entity-table prefix on every
        # subsequent call. The variable suffix is just the entity hint,
        # few-shots, and the input token.
        parts: List[str] = [_FRAMING_HEADER, ""]

        # ── constant prefix ───────────────────────────────────────────
        # The OBA-specific 10-rule translation block and the JDA→Pine
        # entity translation table were removed after the H1 prompt-
        # strip experiment showed they were diluting the prompt: the
        # universal role enum + vocabulary + few-shot already cover the
        # same information in a more general form, and removing them
        # lifted macro F1 by 0.020 and cut unmatched count by 67% on a
        # 16-template pure-LLM eval. See LLM_CAPABILITY_FINDINGS.md.
        # ``_JDA_TO_PINE_ENTITY`` is still imported below for the
        # per-input entity hint section.
        from ..grammar import role_enum as _role_enum
        parts.append(_SECTION_ROLE_ENUM)
        parts.append(_role_enum.render_for_prompt())
        parts.append("")

        parts.append(_SECTION_VOCAB)
        parts.append("entities: " + ", ".join(self.vocabulary.entities))
        parts.append("builtins: " + ", ".join(self.vocabulary.builtins))
        parts.append("prompt_variables: " + ", ".join(self.vocabulary.prompt_variables))
        parts.append("")

        if self.grammar_fragment:
            parts.append(_SECTION_GRAMMAR)
            parts.append(self.grammar_fragment.rstrip())
            parts.append("")

        # Shape examples (constant per call — cache-safe prefix).
        if self.shape_examples:
            parts.append(_SECTION_SHAPE_EXAMPLES)
            for m, r in self.shape_examples:
                if r:
                    parts.append(f"  {m}  →  {r}")
                else:
                    parts.append(f"  {m}  →  <drop — no Pine output>")
            parts.append("")

        # ── variable suffix ───────────────────────────────────────────
        if self.few_shot:
            parts.append(_SECTION_FEWSHOT)
            for p in self.few_shot:
                match_text = p.match if isinstance(p.match, str) else " | ".join(p.match)
                rewrite_text = (
                    p.rewrite if isinstance(p.rewrite, str)
                    else " | ".join(p.rewrite or [])
                ) if p.rewrite else f"<rewrite_function: {p.rewrite_function}>"
                match_text = _scrub_pattern_holes(match_text)
                rewrite_text = _scrub_pattern_holes(rewrite_text)
                parts.append(f"  {match_text}  →  {rewrite_text}")
            parts.append("")

        from ..patterns.transforms import _JDA_TO_PINE_ENTITY
        hint = _input_entity_hint(self.jda_token, _JDA_TO_PINE_ENTITY)
        if hint is not None:
            parts.append(_SECTION_HINT)
            parts.append(hint)
            parts.append("")

        parts.append(_SECTION_INPUT)
        parts.append(self.jda_token.unparse())
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
# Batched conversion request — all tokens of one document in one call
# ─────────────────────────────────────────────────────────────────────────────


_SECTION_BATCH_INPUTS = (
    "INPUTS (numbered list of JDA expressions to convert — siblings from "
    "the same document; use them to disambiguate context):"
)
_SECTION_BATCH_OUTPUTS = (
    "OUTPUTS — return a single JSON object matching this schema (and no\n"
    "other text). Each ``results[i].slot`` corresponds to the matching\n"
    "input number above; ``pine_tokens`` is a list of zero or more\n"
    "Pine ``@[...]`` token strings for that slot. Use an empty list\n"
    "(``[]``) when a token should be dropped with no Pine output (e.g.,\n"
    "internal IDs, attorney titles); use ``null`` only when you genuinely\n"
    "cannot translate it.\n"
    "\n"
    "Schema:\n"
    "  {\n"
    "    \"results\": [\n"
    "      {\"slot\": <int>, \"pine_tokens\": [\"@[...]\", ...] | null},\n"
    "      ...\n"
    "    ]\n"
    "  }"
)


@dataclass(frozen=True)
class BatchConversionRequest:
    """One LLM request that translates many JDA tokens at once.

    Mirrors :class:`ConversionRequest`, but the variable suffix is a
    numbered LIST of input tokens instead of one. The response format
    is a numbered list of Pine outputs in matching order; each output
    slot can contain multiple Pine tokens (so `bare X.FullName` →
    `@[Y.first.NameFirstName] @[Y.first.NameLastName]` works).

    ``template_name``, when supplied, is used to classify the document
    audience (Complainant / Respondent) and surface that as a strong
    disambiguation hint. Pass it through from the eval / pipeline.
    """

    jda_tokens: tuple
    org: str
    vocabulary: OrgVocabulary
    few_shot: List[Pattern] = field(default_factory=list)
    grammar_fragment: str = ""
    template_name: Optional[str] = None
    shape_examples: tuple = ()    # tuple of (match, rewrite) pairs
    contexts: tuple = ()           # parallel to jda_tokens: (before, after) prose

    def assemble_batch_prompt(self) -> str:
        # Same constant prefix as the single-token assembly so the
        # OpenAI prompt cache can hit. Variable suffix differs.
        # OBA rules block + entity translation table were stripped
        # post-H1 experiment — see ``assemble_prompt`` for the rationale.
        parts: List[str] = [_FRAMING_HEADER, ""]

        # Universal Pine role enum (constant per call; cached prefix).
        from ..grammar import role_enum as _role_enum
        parts.append(_SECTION_ROLE_ENUM)
        parts.append(_role_enum.render_for_prompt())
        parts.append("")

        parts.append(_SECTION_VOCAB)
        parts.append("entities: " + ", ".join(self.vocabulary.entities))
        parts.append("builtins: " + ", ".join(self.vocabulary.builtins))
        parts.append("prompt_variables: " + ", ".join(self.vocabulary.prompt_variables))
        parts.append("")

        if self.grammar_fragment:
            parts.append(_SECTION_GRAMMAR)
            parts.append(self.grammar_fragment.rstrip())
            parts.append("")

        # Shape examples — generic <X>-templated mappings. Loaded from
        # engine/llm_examples.toml and threaded in by convert.py.
        # Stable across calls; come before similarity-driven few-shot
        # so the cache prefix stays consistent.
        if self.shape_examples:
            parts.append(_SECTION_SHAPE_EXAMPLES)
            for m, r in self.shape_examples:
                if r:
                    parts.append(f"  {m}  →  {r}")
                else:
                    parts.append(f"  {m}  →  <drop — no Pine output>")
            parts.append("")

        # ── variable suffix ───────────────────────────────────────────
        if self.few_shot:
            parts.append(_SECTION_FEWSHOT)
            for p in self.few_shot:
                match_text = p.match if isinstance(p.match, str) else " | ".join(p.match)
                rewrite_text = (
                    p.rewrite if isinstance(p.rewrite, str)
                    else " | ".join(p.rewrite or [])
                ) if p.rewrite else f"<rewrite_function: {p.rewrite_function}>"
                match_text = _scrub_pattern_holes(match_text)
                rewrite_text = _scrub_pattern_holes(rewrite_text)
                parts.append(f"  {match_text}  →  {rewrite_text}")
            parts.append("")

        # Per-input entity hints — give the LLM a directly-resolved
        # JDA→Pine entity for each input where the leading identifier
        # is in the table. Especially helpful in batch mode because
        # the LLM can't see all hints at once otherwise.
        from ..patterns.transforms import _JDA_TO_PINE_ENTITY
        hint_lines: List[str] = []
        for i, tok in enumerate(self.jda_tokens, start=1):
            h = _input_entity_hint(tok, _JDA_TO_PINE_ENTITY)
            if h is not None:
                hint_lines.append(f"  for input {i}: {h.strip()}")
        if hint_lines:
            parts.append(_SECTION_HINT)
            parts.extend(hint_lines)
            parts.append("")

        # Document-audience classification — explicit hint about who
        # the document is addressed to. Highest-leverage signal for
        # ambiguous tokens like ``Cust_Address.City`` that could bind
        # to ``Complainant`` or ``Respondent``.
        audience = classify_document_audience(
            self.template_name, self.jda_tokens, _JDA_TO_PINE_ENTITY,
        )
        if audience is not None:
            audience_pine = "Complainant" if audience == "complainant" else "Respondent"
            parts.append(_SECTION_DOC_AUDIENCE)
            parts.append(f"  primary subject: {audience_pine}")
            parts.append(
                f"  → ambiguous address fields, pronouns, and role tokens "
                f"should bind to {audience_pine}."
            )
            parts.append("")

        # Document-context summary — helps disambiguate context-dependent
        # tokens by surfacing the dominant Pine entity in the batch.
        ctx = _document_context_summary(self.jda_tokens, _JDA_TO_PINE_ENTITY)
        if ctx is not None:
            parts.append(_SECTION_DOC_CONTEXT)
            parts.append(ctx)
            parts.append("")

        parts.append(_SECTION_BATCH_INPUTS)
        for i, tok in enumerate(self.jda_tokens, start=1):
            parts.append(f"{i}. {tok.unparse()}")
            # Inline per-token context (surrounding prose) when we
            # have it. Length is already trimmed upstream so the
            # prompt budget stays predictable.
            if i - 1 < len(self.contexts):
                before, after = self.contexts[i - 1]
                if before or after:
                    parts.append(
                        f"   context: \"…{before}\" ⟵ here ⟶ \"{after}…\""
                    )
        parts.append("")
        parts.append(_SECTION_BATCH_OUTPUTS)
        return "\n".join(parts)

    @staticmethod
    def parse_json_response(response: str, n: int) -> List[List[PineToken]]:
        """Parse a JSON-schema'd LLM response into per-slot Pine tokens.

        Schema (what the prompt asks for):
            {"results": [{"slot": int, "pine_tokens": [str, ...] | null}, ...]}

        Always returns a list of length ``n``. Missing slots, slots with
        ``null`` pine_tokens, and slots whose strings don't parse as
        valid Pine tokens get an empty list.
        """
        import json as _json
        text = response.strip()
        # Strip markdown fences if a model wrapped its JSON in ```json.
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()
        try:
            data = _json.loads(text)
        except Exception:
            return [[] for _ in range(n)]
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return [[] for _ in range(n)]
        out: List[List[PineToken]] = [[] for _ in range(n)]
        for entry in results:
            if not isinstance(entry, dict):
                continue
            slot = entry.get("slot")
            tokens = entry.get("pine_tokens")
            if not isinstance(slot, int) or slot < 1 or slot > n:
                continue
            if tokens is None or not isinstance(tokens, list):
                continue
            parsed: List[PineToken] = []
            for s in tokens:
                if not isinstance(s, str) or not s.strip():
                    continue
                tok = ConversionRequest.parse_response(s)
                if tok is not None:
                    parsed.append(tok)
            out[slot - 1] = parsed
        return out

    @staticmethod
    def parse_json_drops(response: str, n: int) -> set:
        """Return the set of slot indices the LLM intentionally dropped.

        A slot is "dropped" when the JSON response set its
        ``pine_tokens`` to ``[]`` (the explicit "no Pine output for
        this JDA, please remove the source token from the document"
        signal). Distinct from ``null`` (LLM couldn't translate — leave
        the JDA in place and mark unmatched) and from a parse failure
        (same effect as null for the user). Returns 0-indexed slots."""
        import json as _json
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()
        try:
            data = _json.loads(text)
        except Exception:
            return set()
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return set()
        drops = set()
        for entry in results:
            if not isinstance(entry, dict):
                continue
            slot = entry.get("slot")
            tokens = entry.get("pine_tokens")
            if not isinstance(slot, int) or slot < 1 or slot > n:
                continue
            if isinstance(tokens, list) and len(tokens) == 0:
                drops.add(slot - 1)
        return drops

    @staticmethod
    def parse_batch_response(response: str, n: int) -> List[List[PineToken]]:
        """Parse a numbered-list LLM response into per-slot Pine tokens.

        Tolerates: markdown fences, blank lines, `1.` / `1)` / `1:`
        numbering styles, multi-line slots, and slots that say
        `<no mapping found>` (returned as empty list for that slot).
        Always returns a list of length ``n``; missing slots get [].
        """
        text = response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        # Group lines by the leading number marker. A slot starts at
        # any line that begins with `^\d+[.):]`.
        slot_re = re.compile(r"^\s*(\d+)\s*[.):]\s*(.*)$")
        slots: dict = {}
        current_idx: Optional[int] = None
        for line in text.splitlines():
            m = slot_re.match(line)
            if m:
                current_idx = int(m.group(1))
                slots[current_idx] = [m.group(2)]
            elif current_idx is not None and line.strip():
                slots[current_idx].append(line)

        # Tolerance: when no numbered slots are detected and there's a
        # single input, treat the whole response as the single slot's
        # output. This covers callers that hand-write a one-line LLM
        # response without numbering.
        if not slots and n == 1:
            return [_extract_pine_tokens(text)]

        out: List[List[PineToken]] = []
        for i in range(1, n + 1):
            chunk_lines = slots.get(i, [])
            chunk = "\n".join(chunk_lines).strip()
            out.append(_extract_pine_tokens(chunk))
        return out


def _extract_pine_tokens(text: str) -> List[PineToken]:
    """Find all balanced ``@[...]`` substrings and parse each. Drop
    parse failures silently — a partially-parsed slot is better than
    no slot at all."""
    tokens: List[PineToken] = []
    i = 0
    while i < len(text):
        start = text.find("@[", i)
        if start < 0:
            break
        depth = 1
        j = start + 2
        while j < len(text) and depth > 0:
            c = text[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            j += 1
        if depth != 0:
            break
        candidate = text[start:j]
        try:
            tokens.append(pine_parser.parse(candidate))
        except pine_parser.PineParseError:
            pass
        i = j
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# Retry-candidate detection
# ─────────────────────────────────────────────────────────────────────────────
# A slot is a retry candidate iff it parsed to an empty list AND the
# LLM clearly *tried* to fill it (i.e., the raw JSON either set the
# slot to ``null`` or to a non-empty array whose strings failed to
# parse as Pine). Slots that the LLM explicitly returned as ``[]`` are
# intentional drops (e.g., for ``%[<X>.Title]``) and shouldn't be
# retried.


def _extract_retry_candidates(
    response: str,
    parsed: List[List[PineToken]],
    n: int,
) -> List[int]:
    import json as _json
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()
    try:
        data = _json.loads(text)
    except Exception:
        # Non-JSON response — the numbered-list parser already ran; we
        # can't distinguish "tried but failed" from "intentional drop",
        # so don't retry anything.
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    retry: List[int] = []
    seen_slots: set = set()
    for entry in results:
        if not isinstance(entry, dict):
            continue
        slot = entry.get("slot")
        if not isinstance(slot, int) or slot < 1 or slot > n:
            continue
        idx = slot - 1
        seen_slots.add(idx)
        if parsed[idx]:
            continue          # already parsed something — fine
        tokens = entry.get("pine_tokens")
        if tokens is None:
            retry.append(idx)
        elif (
            isinstance(tokens, list)
            and any(isinstance(s, str) and s.strip() for s in tokens)
        ):
            # Non-empty list of strings, none parsed.
            retry.append(idx)
        # else: explicit ``[]`` = intentional drop, do not retry
    # Slots not in the response at all also get retried (the model
    # silently skipped them).
    for idx in range(n):
        if idx not in seen_slots and not parsed[idx]:
            retry.append(idx)
    return retry


# ─────────────────────────────────────────────────────────────────────────────
# Top-level converter
# ─────────────────────────────────────────────────────────────────────────────


class LlmConverter:
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
        org_overrides: Optional[OrgRoot],
        few_shot_count: int = 5,
        grammar_fragment: str = "",
        shape_examples: Sequence[tuple] = (),
    ):
        self._client = client
        self._library = list(library)
        self._org = org_overrides
        self._few_shot_count = few_shot_count
        self._grammar_fragment = grammar_fragment
        # tuple-ize so the field can sit on the frozen request dataclass.
        self._shape_examples = tuple((m, r) for (m, r) in shape_examples)

    def build_request(self, jda_token: JdaToken) -> ConversionRequest:
        # When the caller passes ``org="any"`` the pipeline gives us
        # ``org_overrides=None``. Fall back to a generic identity and
        # an empty vocabulary so the LLM still gets few-shots and
        # grammar context, just without org-specific filtering.
        org_id = self._org.id if self._org is not None else "any"
        vocabulary = (
            self._org.vocabulary if self._org is not None
            else OrgVocabulary(entities=[], builtins=[], prompt_variables=[])
        )
        few_shot = _select_few_shot(
            self._library,
            jda_token.unparse(),
            org_id,
            k=self._few_shot_count,
        )
        return ConversionRequest(
            jda_token=jda_token,
            org=org_id,
            vocabulary=vocabulary,
            few_shot=few_shot,
            grammar_fragment=self._grammar_fragment,
            shape_examples=self._shape_examples,
        )

    def convert(self, jda_token: JdaToken) -> Optional[PineToken]:
        req = self.build_request(jda_token)
        prompt = req.assemble_prompt()
        response = self._client.complete(prompt)
        return ConversionRequest.parse_response(response)

    def build_batch_request(
        self,
        jda_tokens: Sequence[JdaToken],
        template_name: Optional[str] = None,
        contexts: Optional[Sequence[tuple]] = None,
    ) -> "BatchConversionRequest":
        org_id = self._org.id if self._org is not None else "any"
        vocabulary = (
            self._org.vocabulary if self._org is not None
            else OrgVocabulary(entities=[], builtins=[], prompt_variables=[])
        )
        # Few-shots: aggregate across all input tokens. We pick the
        # globally-best k by max similarity to any input token, then
        # de-duplicate. With many siblings the LLM gets a richer
        # example set than per-token retrieval would give.
        seen_ids: set = set()
        merged: List[Pattern] = []
        for tok in jda_tokens:
            for p in _select_few_shot(self._library, tok.unparse(), org_id, k=self._few_shot_count):
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    merged.append(p)
                if len(merged) >= self._few_shot_count * 2:
                    break
            if len(merged) >= self._few_shot_count * 2:
                break
        return BatchConversionRequest(
            jda_tokens=tuple(jda_tokens),
            org=org_id,
            vocabulary=vocabulary,
            few_shot=merged,
            grammar_fragment=self._grammar_fragment,
            template_name=template_name,
            shape_examples=self._shape_examples,
            contexts=tuple(contexts) if contexts else (),
        )

    def convert_batch(
        self,
        jda_tokens: Sequence[JdaToken],
        template_name: Optional[str] = None,
        contexts: Optional[Sequence[tuple]] = None,
        return_drops: bool = False,
    ) -> List[List[PineToken]]:
        """Translate every unmatched token in one LLM call.

        ``template_name`` (filename or any descriptor) is used by the
        prompt's audience-classification heuristic to bias ambiguous
        translations toward the right entity.

        If the underlying client supports tool-calling (``OpenAILlmClient``
        does), the LLM also has access to ``search_pine_syntax(query)``
        — the same RAG tool v1 uses to look up Pine syntax dynamically.
        The model can call it multiple times before producing the
        final numbered output list.

        Returns a list of length ``len(jda_tokens)``. Each element is
        the list of Pine tokens for that input slot — empty list when
        the LLM didn't produce a parseable output for that slot. The
        plural-output shape supports rules like "bare X.FullName splits
        into two Pine tokens".
        """
        if not jda_tokens:
            return []
        req = self.build_batch_request(
            jda_tokens,
            template_name=template_name,
            contexts=contexts,
        )
        prompt = req.assemble_batch_prompt()

        # Structured output — strict JSON schema enforced server-side
        # when the model supports it. Eliminates the numbered-list
        # parse heuristics; per-slot drop vs no-mapping is now
        # explicitly distinguished (empty list vs null).
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "batch_pine_results",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["results"],
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["slot", "pine_tokens"],
                                "properties": {
                                    "slot": {"type": "integer"},
                                    "pine_tokens": {
                                        "anyOf": [
                                            {"type": "null"},
                                            {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        ],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

        if hasattr(self._client, "complete_with_tools"):
            response = self._client.complete_with_tools(
                prompt,
                tools={"search_pine_syntax": search_pine_syntax},
                response_format=response_format,
            )
        else:
            response = self._client.complete(prompt)

        # Try the JSON path first; fall back to the legacy numbered-
        # list parser if the model returned non-JSON (older models that
        # rejected ``response_format`` and the client dropped it).
        n = len(jda_tokens)
        parsed = BatchConversionRequest.parse_json_response(response, n)
        # Slots the LLM explicitly returned as ``[]`` — intentional
        # drops (e.g., %[<X>.Title]). Captured BEFORE retry so a retry
        # for an unrelated slot doesn't overwrite the drop signal.
        drop_slots = BatchConversionRequest.parse_json_drops(response, n)
        if not any(parsed):
            parsed = BatchConversionRequest.parse_batch_response(response, n)

        # Validate-and-retry. For each slot the parser emitted as
        # empty even though the LLM clearly *tried* (it sent
        # ``null`` or non-empty strings that didn't survive the Pine
        # parser), re-ask just for those slots with a stronger
        # "previous output didn't parse" prefix. One retry only —
        # if it still fails after that, we surface the slot as
        # unmatched and let the user fix it in the GUI.
        retry_idx = _extract_retry_candidates(response, parsed, n)
        # Don't retry intentional drops — those are correct as-is.
        retry_idx = [i for i in retry_idx if i not in drop_slots]
        if retry_idx and len(retry_idx) < n:
            retry_results = self._retry_failed_slots(
                jda_tokens, parsed, retry_idx, template_name, contexts,
            )
            for idx, out in zip(retry_idx, retry_results):
                if out:
                    parsed[idx] = out
        if return_drops:
            return parsed, drop_slots
        return parsed

    def _retry_failed_slots(
        self,
        jda_tokens: Sequence[JdaToken],
        parsed: List[List[PineToken]],
        retry_idx: Sequence[int],
        template_name: Optional[str],
        contexts: Optional[Sequence[tuple]],
    ) -> List[List[PineToken]]:
        """Re-run convert on just the slots that came back empty.

        Builds a fresh batch request from the failed inputs + their
        contexts, then prepends a short directive telling the model
        the previous attempt didn't parse and to take more care with
        the @[...] bracket structure.
        """
        sub_tokens = [jda_tokens[i] for i in retry_idx]
        sub_contexts = (
            [contexts[i] for i in retry_idx] if contexts else None
        )
        req = self.build_batch_request(
            sub_tokens,
            template_name=template_name,
            contexts=sub_contexts,
        )
        prompt = (
            "RETRY ATTEMPT — your previous output for the inputs below\n"
            "either contained un-parseable Pine or returned ``null``.\n"
            "Pay close attention to the @[...] bracket structure and the\n"
            "field-level syntax in the grammar reference. Each output\n"
            "MUST be a balanced @[...] expression; consult the\n"
            "``search_pine_syntax`` tool if you're unsure of a field.\n"
            "Return the same JSON schema.\n\n"
        ) + req.assemble_batch_prompt()

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "batch_pine_results",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["results"],
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["slot", "pine_tokens"],
                                "properties": {
                                    "slot": {"type": "integer"},
                                    "pine_tokens": {
                                        "anyOf": [
                                            {"type": "null"},
                                            {"type": "array", "items": {"type": "string"}},
                                        ],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }

        if hasattr(self._client, "complete_with_tools"):
            response = self._client.complete_with_tools(
                prompt,
                tools={"search_pine_syntax": search_pine_syntax},
                response_format=response_format,
            )
        else:
            response = self._client.complete(prompt)
        n_sub = len(sub_tokens)
        out = BatchConversionRequest.parse_json_response(response, n_sub)
        if not any(out):
            out = BatchConversionRequest.parse_batch_response(response, n_sub)
        return out
