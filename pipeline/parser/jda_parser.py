"""JDA parser — turns ``%[...]`` source text into a JdaToken AST.

The parser has two stages:

    text  ──preprocess──►  cleaned text  ──lex──►  token stream  ──parse──►  AST

Preprocessing collapses whitespace artefacts that legacy RTF leaves
behind (``Cust_Foo _Bar`` → ``Cust_Foo_Bar``, ``X .field`` → ``X.field``,
``F (arg)`` → ``F(arg)``).

The lexer is a small state machine that walks characters and yields
``(kind, value)`` pairs. Whitespace between tokens is dropped.

The parser is recursive descent: one function per grammar rule. Each
rule consumes the tokens it needs and returns an AST node. Rules call
each other directly — there is no separate evaluator or transformer.

The parser is permissive: extra whitespace anywhere is fine, ``=`` and
``==`` are both accepted as equality, ``in`` and ``IN`` are both
accepted as the iteration keyword, and the casing of control keywords
(``If`` / ``IF`` / ``if``) is normalised to the canonical form.

Public entry point:

    parse(source: str) -> JdaToken
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .jda_ast import (
    CONTROL_KEYWORD_LOOKUP,
    LITERAL_BOOL,
    LITERAL_FORMAT,
    LITERAL_NUMBER,
    LITERAL_PATH,
    LITERAL_STRING,
    JdaBinaryOp,
    JdaCall,
    JdaControl,
    JdaIter,
    JdaLiteral,
    JdaNode,
    JdaPath,
    JdaToken,
)


class JdaParseError(ValueError):
    """Raised when the parser cannot make sense of an input."""


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing — repair whitespace artefacts that survive RTF cleaning.
# ─────────────────────────────────────────────────────────────────────────────

# Each rule collapses a run of whitespace that should not exist between
# an identifier-shaped neighbourhood and an adjacent ``_``, ``.``, or
# ``(``. None of these rules touch whitespace inside FormatDate's date
# pattern (which has letters separated by spaces but no _/./() neighbours),
# inside Subdocument's path, or inside string literals.
_WHITESPACE_REPAIRS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?<=[A-Za-z0-9])\s+(?=_[A-Za-z0-9])"), ""),  # "Foo _Bar" → "Foo_Bar"
    (re.compile(r"(?<=_)\s+(?=[A-Za-z0-9])"), ""),             # "Foo_ Bar" → "Foo_Bar"
    (re.compile(r"(?<=[A-Za-z0-9_])\s+(?=\.)"), ""),           # "Foo .bar" → "Foo.bar"
    (re.compile(r"(?<=\.)\s+(?=[A-Za-z0-9_])"), ""),           # ". bar"   → ".bar"
    (re.compile(r"(?<=[A-Za-z0-9_])\s+(?=\()"), ""),           # "Foo (..." → "Foo(..."
    # Collapse whitespace inside multi-char operators (RTF artefact):
    (re.compile(r"=\s+="), "=="),                              # "= =" → "=="
    (re.compile(r"!\s+="), "!="),                              # "! =" → "!="
    (re.compile(r"<\s+="), "<="),                              # "< =" → "<="
    (re.compile(r">\s+="), ">="),                              # "> =" → ">="
)


def _repair_whitespace(text: str) -> str:
    """Apply identifier-whitespace repairs (see _WHITESPACE_REPAIRS)."""
    for pattern, replacement in _WHITESPACE_REPAIRS:
        text = pattern.sub(replacement, text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Lexer
# ─────────────────────────────────────────────────────────────────────────────

# Token kinds emitted by the lexer. We use plain strings rather than an
# Enum because they show up in error messages directly.
KIND_IDENT = "IDENT"
KIND_NUMBER = "NUMBER"
KIND_STRING = "STRING"
KIND_LPAREN = "("
KIND_RPAREN = ")"
KIND_COMMA = ","
KIND_DOT = "."
KIND_EQ = "="
KIND_EQEQ = "=="
KIND_NEQ = "!="
KIND_LT = "<"
KIND_LTEQ = "<="
KIND_GT = ">"
KIND_GTEQ = ">="
KIND_RAW = "RAW"     # any character the lexer doesn't recognise; only
                     # legal during raw-capture (Subdocument path,
                     # FormatDate format string). The regular parser
                     # path errors on RAW.
KIND_EOF = "EOF"

# Multi-character operators, longest first so the lexer matches "==" before "=".
_MULTI_OPS = (
    ("==", KIND_EQEQ),
    ("!=", KIND_NEQ),
    ("<=", KIND_LTEQ),
    (">=", KIND_GTEQ),
)
_SINGLE_OPS = {
    "(": KIND_LPAREN,
    ")": KIND_RPAREN,
    ",": KIND_COMMA,
    ".": KIND_DOT,
    "=": KIND_EQ,
    "<": KIND_LT,
    ">": KIND_GT,
}


@dataclass(frozen=True)
class _Tok:
    kind: str
    value: str
    pos: int

    def __repr__(self) -> str:  # easier to read in failure messages
        return f"{self.kind}({self.value!r})@{self.pos}"


def _lex_inner(text: str) -> List[_Tok]:
    """Lex the text *inside* the outer ``%[`` ``]`` delimiters.

    Whitespace is skipped. Identifiers, numbers, strings, and operators
    are emitted as tokens with their source positions for error messages.
    """
    tokens: List[_Tok] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue

        # Multi-character operators first.
        matched_multi = False
        for op_text, op_kind in _MULTI_OPS:
            if text.startswith(op_text, i):
                tokens.append(_Tok(op_kind, op_text, i))
                i += len(op_text)
                matched_multi = True
                break
        if matched_multi:
            continue

        # Single-character operators.
        if c in _SINGLE_OPS:
            tokens.append(_Tok(_SINGLE_OPS[c], c, i))
            i += 1
            continue

        # String literal — single or double quoted.
        if c == "'" or c == '"':
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                # No escape handling — JDA strings are simple.
                j += 1
            if j >= n:
                raise JdaParseError(
                    f"Unterminated string starting at position {i}: {text[i:]!r}"
                )
            tokens.append(_Tok(KIND_STRING, text[i + 1 : j], i))
            i = j + 1
            continue

        # Number — sequence of digits, optionally with a decimal point.
        if c.isdigit():
            j = i
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            tokens.append(_Tok(KIND_NUMBER, text[i:j], i))
            i = j
            continue

        # Identifier — letters, digits, underscore. We also allow ``$``
        # as a starting character so pattern source can use ``$hole``;
        # real JDA templates never contain ``$``, so this extension is
        # transparent for production input.
        if c.isalpha() or c == "_" or c == "$":
            j = i + 1   # always include the start char (even ``$`` which isn't alnum)
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(_Tok(KIND_IDENT, text[i:j], i))
            i = j
            continue

        # Unknown character — we don't raise here so that raw-capture
        # mode (Subdocument paths, FormatDate format strings) can swallow
        # things like `\`, `/`, ``-``, etc. The regular parser path will
        # raise if it encounters a RAW token, so we still catch real
        # garbage at parse time.
        tokens.append(_Tok(KIND_RAW, c, i))
        i += 1

    tokens.append(_Tok(KIND_EOF, "", n))
    return _merge_split_idents(tokens)


# IDENT tokens that should NEVER be merged with their neighbour, because
# they're keywords whose meaning depends on standing alone.
_NON_MERGE_IDENTS = frozenset({"in", "true", "false"} |
                              {k.lower() for k in CONTROL_KEYWORD_LOOKUP})


def _merge_split_idents(tokens: List[_Tok]) -> List[_Tok]:
    """Post-lex pass: merge two adjacent IDENT tokens into one when
    neither is a keyword. Repairs RTF artefacts like ``Cust_Foo Bar`` or
    ``JW_C aseStatusEvents`` that the regex pre-processor can't catch
    (because the join point is between two letter sequences with no
    underscore/dot/paren between them).

    A side effect: an unquoted multi-word RHS in a binary op like
    ``If(X=Diversion Agreement)`` becomes a single identifier
    ``DiversionAgreement``. We accept the lossy round-trip; pattern
    matchers can normalise downstream if they care about the original
    spelling.
    """
    merged: List[_Tok] = []
    for tok in tokens:
        if (
            merged
            and tok.kind == KIND_IDENT
            and merged[-1].kind == KIND_IDENT
            and merged[-1].value.lower() not in _NON_MERGE_IDENTS
            and tok.value.lower() not in _NON_MERGE_IDENTS
        ):
            prev = merged[-1]
            merged[-1] = _Tok(KIND_IDENT, prev.value + tok.value, prev.pos)
        else:
            merged.append(tok)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

# Function names that take their argument as a raw path (don't tokenize
# the contents — capture until matching close paren). This is how
# Subdocument's backslash-paths survive.
_RAW_PATH_FUNCTIONS = {"subdocument"}

# Function names whose *second* argument should be captured as a raw
# format string. FormatDate's pattern has spaces and commas that are
# not argument separators.
_FORMAT_DATE_FUNCTIONS = {"formatdate"}

# Casing wrapper functions that take a single nested expression argument.
# Listed here for documentation; the parser doesn't special-case them
# because plain Call parsing handles them correctly.
_CASING_FUNCTIONS = {"titlecase", "uppercase", "lowercase", "initials"}

# Operators that form a binary comparison.
_COMPARISON_OPS = {KIND_EQ, KIND_EQEQ, KIND_NEQ, KIND_LT, KIND_LTEQ, KIND_GT, KIND_GTEQ}


class _Parser:
    """Recursive-descent parser. One instance per parse call."""

    def __init__(self, tokens: List[_Tok], original_inner: str):
        self._tokens = tokens
        self._pos = 0
        self._original_inner = original_inner

    # ── token-stream helpers ────────────────────────────────────────────
    def _peek(self, offset: int = 0) -> _Tok:
        return self._tokens[self._pos + offset]

    def _advance(self) -> _Tok:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _expect(self, kind: str) -> _Tok:
        tok = self._peek()
        if tok.kind != kind:
            raise JdaParseError(
                f"Expected {kind} but got {tok!r} in {self._original_inner!r}"
            )
        return self._advance()

    def _accept(self, kind: str) -> Optional[_Tok]:
        if self._peek().kind == kind:
            return self._advance()
        return None

    # ── top-level ───────────────────────────────────────────────────────
    def parse_inner(self) -> JdaNode:
        """Parse the inside of a %[ ... ] expression."""
        node = self._expression()
        if self._peek().kind != KIND_EOF:
            raise JdaParseError(
                f"Trailing tokens after expression: "
                f"{self._tokens[self._pos:]!r} in {self._original_inner!r}"
            )
        return node

    # ── grammar rules ───────────────────────────────────────────────────
    def _expression(self) -> JdaNode:
        """expression := control | binary_op | atom

        Binary operators are flat (no precedence): we parse one atom,
        and if a comparison operator follows, consume one more atom.
        That covers the whole JDA condition vocabulary observed in the
        corpus.
        """
        # Control keywords are recognised by their canonical name on a
        # bare IDENT at the start.
        if self._peek().kind == KIND_IDENT:
            keyword = CONTROL_KEYWORD_LOOKUP.get(self._peek().value.lower())
            if keyword is not None:
                return self._control(keyword)

        left = self._atom()
        if self._peek().kind in _COMPARISON_OPS:
            op = self._advance()
            right = self._atom()
            return JdaBinaryOp(op=op.value, left=left, right=right)
        return left

    def _control(self, keyword: str) -> JdaControl:
        """control := KEYWORD '(' inner_expr ')' | KEYWORD"""
        self._advance()  # consume the keyword IDENT
        if self._accept(KIND_LPAREN) is None:
            return JdaControl(keyword=keyword, args=())

        # Foreach/MultiSelect take an iter binding, others take an expr.
        if keyword in ("Foreach", "MultiSelect"):
            arg = self._iter_binding()
        else:
            arg = self._expression()
        self._expect(KIND_RPAREN)
        return JdaControl(keyword=keyword, args=(arg,))

    def _iter_binding(self) -> JdaIter:
        """iter := path 'in' path

        ``var`` is normally a single identifier (``a``, ``c``), but JDA
        templates also use a dotted path here, e.g.
        ``ForEach(JW_CaseStatusEvents.EventID in JW_CaseStatusEvents)``
        — the var-side is the field-to-extract, not a binding name. We
        accept either; ``JdaIter.var`` is a string, so we serialise the
        path back with dots for round-trip.
        """
        first = self._expect(KIND_IDENT)
        var_parts: List[str] = [first.value]
        while self._peek().kind == KIND_DOT:
            self._advance()
            var_parts.append(self._expect(KIND_IDENT).value)
        var_str = ".".join(var_parts)

        in_tok = self._peek()
        if in_tok.kind != KIND_IDENT or in_tok.value.lower() != "in":
            raise JdaParseError(
                f"Expected 'in' keyword in iter binding, got {in_tok!r} "
                f"in {self._original_inner!r}"
            )
        self._advance()  # consume 'in'
        collection = self._atom()
        return JdaIter(var=var_str, collection=collection)

    def _atom(self) -> JdaNode:
        """atom := call | path | identifier | literal"""
        tok = self._peek()

        if tok.kind == KIND_STRING:
            self._advance()
            return JdaLiteral(value=tok.value, kind=LITERAL_STRING)

        if tok.kind == KIND_NUMBER:
            self._advance()
            return JdaLiteral(value=tok.value, kind=LITERAL_NUMBER)

        if tok.kind == KIND_IDENT:
            # Could be:
            #   - bool literal:    true / false (case-insensitive)
            #   - call:            IDENT '(' ...
            #   - path / ident:    IDENT ('.' IDENT)*
            lower = tok.value.lower()
            if lower in ("true", "false"):
                self._advance()
                return JdaLiteral(value=lower, kind=LITERAL_BOOL)

            # Look one ahead to disambiguate call vs path.
            if self._peek(1).kind == KIND_LPAREN:
                return self._call()
            return self._path_or_identifier()

        raise JdaParseError(
            f"Unexpected token {tok!r} in atom position; "
            f"context: {self._original_inner!r}"
        )

    def _call(self) -> JdaCall:
        """call := IDENT '(' arglist? ')'

        Two function names get special argument handling:
          - Subdocument: argument is captured as a raw path literal
          - FormatDate:  if there are >=2 args, the second one is captured
                         as a raw format-string literal
        """
        name_tok = self._expect(KIND_IDENT)
        self._expect(KIND_LPAREN)
        name = name_tok.value
        lower_name = name.lower()

        if lower_name in _RAW_PATH_FUNCTIONS:
            return self._call_with_raw_path(name)

        # Empty arg list?
        if self._peek().kind == KIND_RPAREN:
            self._advance()
            return JdaCall(name=name, args=())

        args: List[JdaNode] = [self._expression()]
        while self._accept(KIND_COMMA) is not None:
            if lower_name in _FORMAT_DATE_FUNCTIONS and len(args) == 1:
                # Capture the rest of the call as a raw format pattern.
                args.append(self._capture_raw_until_rparen(LITERAL_FORMAT))
                # _capture_raw_until_rparen consumes the closing paren.
                return JdaCall(name=name, args=tuple(args))
            args.append(self._expression())
        self._expect(KIND_RPAREN)
        return JdaCall(name=name, args=tuple(args))

    def _call_with_raw_path(self, name: str) -> JdaCall:
        """Subdocument(...) — capture the parenthesised content verbatim."""
        path_lit = self._capture_raw_until_rparen(LITERAL_PATH)
        return JdaCall(name=name, args=(path_lit,))

    def _capture_raw_until_rparen(self, literal_kind: str) -> JdaLiteral:
        """Walk forward in the original source from the current position,
        find the matching close paren, and emit a single literal carrying
        the text between here and there. Used for FormatDate format strings
        and Subdocument paths, both of which can contain commas, slashes,
        backslashes, and other characters that the lexer doesn't expect.

        After this returns, the parser position is past the closing ')'.
        """
        # Find where in the original source we currently are. The next
        # un-consumed token's `pos` is our anchor; if there are no more
        # tokens (paren is empty) we use len(source) as a fallback.
        anchor = self._peek().pos
        depth = 1
        j = anchor
        n = len(self._original_inner)
        while j < n and depth > 0:
            c = self._original_inner[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise JdaParseError(
                f"Unbalanced parentheses while capturing raw arg "
                f"in {self._original_inner!r}"
            )
        raw = self._original_inner[anchor:j].strip()

        # Drop all tokens we just skipped over; advance past the ')'.
        while self._peek().pos < j:
            self._advance()
        # Now consume the close paren itself.
        if self._peek().kind != KIND_RPAREN:
            raise JdaParseError(
                f"Expected ')' after raw capture at position {j} "
                f"in {self._original_inner!r}; got {self._peek()!r}"
            )
        self._advance()
        return JdaLiteral(value=raw, kind=literal_kind)

    def _path_or_identifier(self) -> JdaPath:
        """path := IDENT ('.' IDENT)*

        A single-segment path covers both "field access on a top-level
        entity" and "bare unquoted value" (like ``RBA`` in
        ``If(X=RBA)``). Pattern matching distinguishes them by context.
        """
        first = self._expect(KIND_IDENT)
        parts: List[str] = [first.value]
        while self._peek().kind == KIND_DOT:
            self._advance()
            ident_tok = self._expect(KIND_IDENT)
            parts.append(ident_tok.value)
        return JdaPath(parts=tuple(parts))


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def parse(source: str) -> JdaToken:
    """Parse a JDA expression of the form ``%[...]`` into a JdaToken AST.

    Raises JdaParseError on malformed input.

    Example::

        parse("%[TitleCase(JW_Respondent.FullName)]")
        # → JdaToken(inner=JdaCall('TitleCase',
        #             (JdaPath(('JW_Respondent', 'FullName')),)))
    """
    if source is None:
        raise JdaParseError("Cannot parse None")
    stripped = source.strip()
    if not (stripped.startswith("%[") and stripped.endswith("]")):
        raise JdaParseError(
            f"Expression must be wrapped in %[ ... ]; got {source!r}"
        )
    inner_text = stripped[2:-1]
    cleaned = _repair_whitespace(inner_text).strip()
    if not cleaned:
        raise JdaParseError(f"Empty %[] expression in {source!r}")

    tokens = _lex_inner(cleaned)
    parser = _Parser(tokens, cleaned)
    inner = parser.parse_inner()
    return JdaToken(inner=inner, raw=source)
