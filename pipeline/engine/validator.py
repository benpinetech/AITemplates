"""Pine validator — apply lint rules, vocabulary, and structural checks.

Run after the pattern engine produces Pine outputs to catch
hallucinated entities, anti-pattern shapes, and unbalanced control
flow. Pure function: same inputs → same outputs.

Usage:

    from pipeline.engine.validator import Validator
    from pipeline.grammar.loaders import load_lint_rules, load_agency_overrides

    validator = Validator(
        lint_rules=load_lint_rules(),
        agency=load_agency_overrides("oba"),
    )
    issues = validator.validate_stream(generated_pine_tokens)
    for issue in issues:
        if issue.severity == "error":
            ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Set

from ..grammar.loaders import LintRule, LintRules, AgencyRoot, load_lint_rules
from ..parser.pine_ast import (
    PineAtName,
    PineBinaryOp,
    PineCall,
    PineChain,
    PineControl,
    PineDictLit,
    PineListLit,
    PineLiteral,
    PineNested,
    PineNode,
    PineSegment,
    PineToken,
)


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    """One finding from the validator. ``token_index`` is the position
    of the offending token in the input stream (or None for stream-
    level issues like unbalanced If/EndIf)."""

    severity: str
    rule_id: str
    message: str
    token_index: Optional[int] = None
    token_text: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — walk a Pine AST and pull out the bits the validator needs
# ─────────────────────────────────────────────────────────────────────────────


def _chain_bases(node: PineNode) -> Set[str]:
    """Every string-typed chain base reachable from ``node``.

    These are the candidate "entity" references — the names that
    should appear in the agency's vocabulary allow-list. We skip
    ``PineAtName`` bases (CreateVar declarations) and recurse into
    nested tokens / call args / control args.
    """
    bases: Set[str] = set()
    _collect_bases(node, bases)
    return bases


def _collect_bases(node: PineNode, out: Set[str]) -> None:
    if isinstance(node, PineChain):
        if isinstance(node.base, str):
            out.add(node.base)
        elif isinstance(node.base, PineNested):
            _collect_bases(node.base.inner, out)
        # PineAtName base — declaration, not a vocabulary reference.
        for seg in node.segments:
            if seg.args:
                for arg in seg.args:
                    _collect_bases(arg, out)
        return
    if isinstance(node, PineNested):
        _collect_bases(node.inner, out)
        return
    if isinstance(node, PineCall):
        for arg in node.args:
            _collect_bases(arg, out)
        return
    if isinstance(node, PineControl):
        for arg in node.args:
            _collect_bases(arg, out)
        return
    if isinstance(node, PineBinaryOp):
        _collect_bases(node.left, out)
        _collect_bases(node.right, out)
        return
    if isinstance(node, PineDictLit):
        for _k, v in node.entries:
            _collect_bases(v, out)
        return
    if isinstance(node, PineListLit):
        for item in node.items:
            _collect_bases(item, out)
        return
    # PineLiteral / PineAtName — leaves, nothing to add.


def _createvar_declared_name(node: PineNode) -> Optional[str]:
    """If ``node`` is a top-level ``CreateVar(@X, ...)`` call, return
    ``"X"`` — the name being declared. Otherwise None.

    The declared name is added to the in-scope vocabulary for the rest
    of the stream so subsequent references to ``@[X.field]`` don't
    trigger an unknown-entity warning.
    """
    if not isinstance(node, PineCall):
        return None
    if node.name.lower() != "createvar":
        return None
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, PineAtName):
        return first.name
    return None


def _foreach_loop_var(node: PineNode) -> Optional[str]:
    """If ``node`` is a chain ending in ``.ForEach(<var>)``, return the
    loop-variable name as a string. Used to extend the in-scope
    vocabulary while walking a stream."""
    if not isinstance(node, PineChain):
        return None
    if not node.segments:
        return None
    last = node.segments[-1]
    if last.name.lower() != "foreach" or not last.args:
        return None
    arg = last.args[0]
    # ForEach(c) parses the bare identifier `c` as a chain with that
    # base — pull the base out.
    if isinstance(arg, PineChain) and isinstance(arg.base, str) and not arg.segments:
        return arg.base
    if isinstance(arg, PineLiteral):
        return arg.value
    return None


def _control_keyword(node: PineNode) -> Optional[str]:
    """Return the canonical control keyword if ``node`` is a top-level
    control token (``If``, ``EndIf``, etc.) — None otherwise."""
    if isinstance(node, PineControl):
        return node.keyword
    # Method-form Foreach — chain ending in .ForEach / .EndForEach is a
    # control too. We classify by the last segment name.
    if isinstance(node, PineChain) and node.segments:
        last_name = node.segments[-1].name
        if last_name.lower() in (
            "foreach", "endforeach", "cca", "endcca", "lb", "endlb",
        ):
            # Capitalise first letter to match canonical names.
            return last_name[0].upper() + last_name[1:]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────


# Block-opener keyword → matching closer keyword. Both forms (the
# standalone ``EndForEach`` control and the method-form
# ``Charges.EndForEach``) use the same canonical name here.
_OPENER_TO_CLOSER = {
    "If": "EndIf",
    "Foreach": "EndForEach",
    "ForEach": "EndForEach",   # method-form opener
    "Cca": "EndCca",
    "Lb": "EndLb",
}
_CLOSERS = set(_OPENER_TO_CLOSER.values())
_BRANCHERS = {"ElseIf", "Else"}   # valid only inside an open If


@dataclass
class _StructuralState:
    """Stack-based tracker for nested control-flow blocks."""

    stack: List[str] = field(default_factory=list)   # opener keywords
    issues: List[ValidationIssue] = field(default_factory=list)
    # ``loop_vars`` accumulates ForEach-introduced names so the
    # vocabulary check can accept them while their block is open.
    loop_vars_stack: List[List[str]] = field(default_factory=list)

    def open(self, keyword: str, idx: int, text: str, loop_var: Optional[str]) -> None:
        self.stack.append(keyword)
        self.loop_vars_stack.append([loop_var] if loop_var else [])

    def close(self, keyword: str, idx: int, text: str) -> None:
        if not self.stack:
            self.issues.append(ValidationIssue(
                severity=SEVERITY_ERROR,
                rule_id="unbalanced_control",
                message=f"closing {keyword!r} with no matching opener",
                token_index=idx, token_text=text,
            ))
            return
        opener = self.stack[-1]
        expected = _OPENER_TO_CLOSER.get(opener, opener)
        if expected != keyword:
            self.issues.append(ValidationIssue(
                severity=SEVERITY_ERROR,
                rule_id="unbalanced_control",
                message=f"expected {expected!r} to close {opener!r}, got {keyword!r}",
                token_index=idx, token_text=text,
            ))
            return
        self.stack.pop()
        self.loop_vars_stack.pop()

    def in_scope_loop_vars(self) -> Set[str]:
        out: Set[str] = set()
        for layer in self.loop_vars_stack:
            out.update(layer)
        return out


class Validator:
    """Run lint, vocabulary, and structural checks over generated Pine
    output.

    Construction loads the lint regexes once. Subsequent calls to
    ``validate_stream`` are pure.
    """

    def __init__(
        self,
        lint_rules: Optional[LintRules] = None,
        agency: Optional[AgencyRoot] = None,
    ):
        self._rules = lint_rules.lint_rule if lint_rules else load_lint_rules().lint_rule
        self._compiled = [(r, re.compile(r.match_regex)) for r in self._rules]
        self._agency = agency
        self._allowed_bases = self._build_allowed_bases(agency) if agency else None

    @staticmethod
    def _build_allowed_bases(agency: AgencyRoot) -> Set[str]:
        """The set of legal chain-base names for this agency. Includes
        entities, builtins (head segment only), prompt variables, and
        a few always-acceptable names."""
        out: Set[str] = set(agency.vocabulary.entities)
        for b in agency.vocabulary.builtins:
            head = b.split(".", 1)[0]
            out.add(head)
        out.update(agency.vocabulary.prompt_variables)
        # Loop-variable names declared elsewhere — we add per-stream
        # via _StructuralState; common single-letter loop vars are not
        # baked in here.
        return out

    # ── public API ──────────────────────────────────────────────────
    def validate_token(self, token: PineToken, index: int = 0) -> List[ValidationIssue]:
        """Run lint + vocabulary checks on one token. Structural balance
        across multiple tokens is in ``validate_stream``."""
        issues: List[ValidationIssue] = []
        text = token.unparse()
        issues.extend(self._lint_one(text, index))
        if self._allowed_bases is not None:
            issues.extend(self._vocabulary_one(token, index, in_scope_extras=set()))
        return issues

    def validate_stream(
        self, tokens: Sequence[PineToken]
    ) -> List[ValidationIssue]:
        """Run all checks over a stream of generated Pine tokens."""
        issues: List[ValidationIssue] = []
        state = _StructuralState()
        declared_vars: Set[str] = set()

        for i, tok in enumerate(tokens):
            text = tok.unparse()

            # A PineRawBlock (verbatim multi-token verified suggestion)
            # has no single ``inner`` AST — it's carried opaquely. Lint
            # still runs on its text, but the structural / vocabulary /
            # CreateVar walks skip it: the block is validated per-token
            # at save time and is internally self-contained, so it can't
            # unbalance the surrounding stream.
            inner = getattr(tok, "inner", None)

            # 1. Lint rules.
            issues.extend(self._lint_one(text, i))

            if inner is None:
                continue

            # 2. Vocabulary check.
            if self._allowed_bases is not None:
                in_scope = state.in_scope_loop_vars() | declared_vars
                issues.extend(self._vocabulary_one(tok, i, in_scope))

            # 3. CreateVar bookkeeping (after vocab check so the
            # decl-token itself isn't shielded by its own declaration).
            decl = _createvar_declared_name(inner)
            if decl:
                declared_vars.add(decl)

            # 4. Structural updates.
            keyword = _control_keyword(inner)
            if keyword is None:
                continue
            if keyword in _OPENER_TO_CLOSER:
                # Capture loop var if this is a Foreach.
                loop_var = _foreach_loop_var(inner) if keyword.lower() in ("foreach", "cca", "lb") else None
                state.open(keyword, i, text, loop_var)
            elif keyword in _CLOSERS:
                state.close(keyword, i, text)
            # ElseIf / Else — must be inside an open If; not enforced
            # strictly here (the matcher already only emits these
            # inside a chunk).

        # 4. Anything left on the stack is unclosed.
        for opener in state.stack:
            issues.append(ValidationIssue(
                severity=SEVERITY_ERROR,
                rule_id="unbalanced_control",
                message=f"unclosed {opener!r} block at end of stream",
                token_index=None,
            ))
        issues.extend(state.issues)
        return issues

    # ── internals ───────────────────────────────────────────────────
    def _lint_one(self, text: str, idx: int) -> List[ValidationIssue]:
        out: List[ValidationIssue] = []
        for rule, pat in self._compiled:
            if pat.search(text):
                out.append(ValidationIssue(
                    severity=rule.severity,
                    rule_id=rule.id,
                    message=rule.description,
                    token_index=idx,
                    token_text=text,
                ))
        return out

    def _vocabulary_one(
        self,
        token: PineToken,
        idx: int,
        in_scope_extras: Set[str],
    ) -> List[ValidationIssue]:
        if self._allowed_bases is None:
            return []
        out: List[ValidationIssue] = []
        bases = _chain_bases(token.inner)
        allowed = self._allowed_bases | in_scope_extras
        for base in bases:
            if base in allowed:
                continue
            # Skip lower-case names — likely loop vars or single-letter
            # template-local names. Vocabulary check only fires on
            # capitalised entity-shaped names.
            if not base or not base[0].isupper():
                continue
            out.append(ValidationIssue(
                severity=SEVERITY_WARNING,
                rule_id="unknown_entity",
                message=f"chain base {base!r} not in agency vocabulary",
                token_index=idx,
                token_text=token.unparse(),
            ))
        return out


def errors_only(issues: Iterable[ValidationIssue]) -> List[ValidationIssue]:
    """Filter helper — pull out only the blocking issues."""
    return [i for i in issues if i.severity == SEVERITY_ERROR]


def warnings_only(issues: Iterable[ValidationIssue]) -> List[ValidationIssue]:
    return [i for i in issues if i.severity == SEVERITY_WARNING]
