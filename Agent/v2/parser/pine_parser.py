"""Pine parser — turns ``@[...]`` source text into a PineToken AST.

Same shape as the JDA parser:

    text  ──preprocess──►  cleaned text  ──lex──►  tokens  ──parse──►  AST

Pine has more grammar than JDA, so the parser does more work. Notable
constructs the parser handles:

- nested tokens: ``@[X]`` inside another ``@[...]`` (see _scan_nested_token)
- @-prefixed names: ``@Defendant`` (declaration label in CreateVar)
- chained access: ``Respondent.first.FormatName(F L).SetCasing(Title)``
- dict literals: ``"CaseID":@[builtin.CaseID],"Type":"DEF"`` inside GetByQuery
- list literals: ``['TC001','TC002']`` and ``[@[builtin.CaseID]]``
- comparison and logical operators: ``==``, ``!=``, ``&&``, ``||``, ``IN``
- raw-arg capture for FormatName / FormatDate / FormatNumber: their args
  contain spaces and slashes that aren't normal expression syntax

The parser is permissive about whitespace and case, like the JDA parser.
``If`` / ``IF`` / ``if`` are all the canonical ``If``. ``IN`` and ``in``
are both accepted. ``=`` and ``==`` are both accepted as equality.

Public entry points::

    parse(source: str) -> PineToken
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .pine_ast import (
    CONTROL_KEYWORD_LOOKUP,
    LIT_BOOL,
    LIT_FORMAT,
    LIT_IDENT_ARG,
    LIT_NUMBER,
    LIT_STRING,
    RAW_ARG_FUNCTIONS,
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


class PineParseError(ValueError):
    """Raised when the parser cannot make sense of an input."""


# ─────────────────────────────────────────────────────────────────────────────
# Preprocessing — same shape as JDA's repair but adapted for Pine.
# Pine source rarely needs repair, but for safety we still collapse
# whitespace before dots and parens.
# ─────────────────────────────────────────────────────────────────────────────

_WHITESPACE_REPAIRS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?<=[A-Za-z0-9_\]])\s+(?=\.)"), ""),         # "X .y" → "X.y"
    (re.compile(r"(?<=\.)\s+(?=[A-Za-z0-9_])"), ""),            # ". y" → ".y"
    (re.compile(r"(?<=[A-Za-z0-9_])\s+(?=\()"), ""),            # "F (" → "F("
    (re.compile(r"(?<=_)\s+(?=[A-Za-z0-9])"), ""),              # "Foo_ Bar" → "Foo_Bar"
    # Collapse whitespace inside multi-char operators (RTF artefact):
    (re.compile(r"@\s+\["), "@["),                              # "@ [" → "@["
    (re.compile(r"=\s+="), "=="),
    (re.compile(r"!\s+="), "!="),
    (re.compile(r"<\s+="), "<="),
    (re.compile(r">\s+="), ">="),
    (re.compile(r"&\s+&"), "&&"),
    (re.compile(r"\|\s+\|"), "||"),
)


def _repair_whitespace(text: str) -> str:
    for pattern, replacement in _WHITESPACE_REPAIRS:
        text = pattern.sub(replacement, text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Lexer
# ─────────────────────────────────────────────────────────────────────────────

KIND_IDENT = "IDENT"
KIND_AT_IDENT = "AT_IDENT"        # @Foo (declaration name)
KIND_AT_LBRACKET = "AT_LBRACKET"  # @[
KIND_NUMBER = "NUMBER"
KIND_STRING = "STRING"
KIND_LPAREN = "("
KIND_RPAREN = ")"
KIND_LBRACKET = "["               # list-literal opener (NOT preceded by @)
KIND_RBRACKET = "]"
KIND_COMMA = ","
KIND_DOT = "."
KIND_COLON = ":"
KIND_EQ = "="
KIND_EQEQ = "=="
KIND_NEQ = "!="
KIND_LT = "<"
KIND_LTEQ = "<="
KIND_GT = ">"
KIND_GTEQ = ">="
KIND_AND = "&&"
KIND_OR = "||"
KIND_RAW = "RAW"
KIND_EOF = "EOF"

_MULTI_OPS = (
    ("==", KIND_EQEQ),
    ("!=", KIND_NEQ),
    ("<=", KIND_LTEQ),
    (">=", KIND_GTEQ),
    ("&&", KIND_AND),
    ("||", KIND_OR),
    ("@[", KIND_AT_LBRACKET),
)
_SINGLE_OPS = {
    "(": KIND_LPAREN,
    ")": KIND_RPAREN,
    "[": KIND_LBRACKET,
    "]": KIND_RBRACKET,
    ",": KIND_COMMA,
    ".": KIND_DOT,
    ":": KIND_COLON,
    "=": KIND_EQ,
    "<": KIND_LT,
    ">": KIND_GT,
}


@dataclass(frozen=True)
class _Tok:
    kind: str
    value: str
    pos: int

    def __repr__(self) -> str:
        return f"{self.kind}({self.value!r})@{self.pos}"


def _lex_inner(text: str) -> List[_Tok]:
    """Lex the inside of a top-level ``@[ ... ]`` expression.

    ``@[`` and ``]`` are emitted as KIND_AT_LBRACKET / KIND_RBRACKET so
    the parser can recursively handle nested tokens. The lexer emits
    KIND_RAW for any character it doesn't otherwise recognise (so that
    raw-arg capture can swallow slashes, hashes, etc.).
    """
    tokens: List[_Tok] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue

        # Multi-character operators first (longest match).
        matched = False
        for op_text, op_kind in _MULTI_OPS:
            if text.startswith(op_text, i):
                tokens.append(_Tok(op_kind, op_text, i))
                i += len(op_text)
                matched = True
                break
        if matched:
            continue

        # @-prefixed identifier: @Foo (used in CreateVar declarations).
        # We only get here if the next two chars aren't @[.
        if c == "@":
            j = i + 1
            if j < n and (text[j].isalpha() or text[j] == "_"):
                k = j
                while k < n and (text[k].isalnum() or text[k] == "_"):
                    k += 1
                tokens.append(_Tok(KIND_AT_IDENT, text[i:k], i))
                i = k
                continue
            tokens.append(_Tok(KIND_RAW, c, i))
            i += 1
            continue

        if c in _SINGLE_OPS:
            tokens.append(_Tok(_SINGLE_OPS[c], c, i))
            i += 1
            continue

        # String literal — Pine strings are flat character sequences.
        # Inside a string we don't tokenize ``@[...]`` (it's part of
        # the string's text). Patterns can re-parse string contents
        # later if they need to.
        if c == "'" or c == '"':
            quote = c
            j = i + 1
            while j < n and text[j] != quote:
                j += 1
            if j >= n:
                raise PineParseError(
                    f"Unterminated string starting at {i}: {text[i:]!r}"
                )
            tokens.append(_Tok(KIND_STRING, text[i + 1 : j], i))
            i = j + 1
            continue

        if c.isdigit():
            j = i
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            tokens.append(_Tok(KIND_NUMBER, text[i:j], i))
            i = j
            continue

        # Identifier — letters, digits, underscore. ``$`` is also a
        # valid start character so pattern source can use ``$hole``.
        if c.isalpha() or c == "_" or c == "$":
            j = i + 1   # always include the start char (even ``$`` which isn't alnum)
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(_Tok(KIND_IDENT, text[i:j], i))
            i = j
            continue

        # Unrecognised — emit RAW so raw-capture can swallow it.
        tokens.append(_Tok(KIND_RAW, c, i))
        i += 1

    tokens.append(_Tok(KIND_EOF, "", n))
    return _merge_split_idents(tokens)


# IDENT tokens that should NEVER be merged with their neighbour. Same
# spirit as the JDA equivalent — keywords whose meaning depends on
# standing alone.
_NON_MERGE_IDENTS = frozenset(
    {"in", "true", "false"} | {k.lower() for k in CONTROL_KEYWORD_LOOKUP}
)


def _merge_split_idents(tokens: List[_Tok]) -> List[_Tok]:
    """Merge two adjacent IDENT tokens into one when neither is a keyword.

    Repairs RTF artefacts like ``Respondent.first.NameFi rstName``
    where the original identifier got split by a stray space.
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

_COMPARISON_OPS = {
    KIND_EQ, KIND_EQEQ, KIND_NEQ, KIND_LT, KIND_LTEQ, KIND_GT, KIND_GTEQ,
    KIND_AND, KIND_OR,
}


class _Parser:
    def __init__(self, tokens: List[_Tok], original: str):
        self._tokens = tokens
        self._pos = 0
        self._source = original

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
            raise PineParseError(
                f"Expected {kind} but got {tok!r} in {self._source!r}"
            )
        return self._advance()

    def _accept(self, kind: str) -> Optional[_Tok]:
        if self._peek().kind == kind:
            return self._advance()
        return None

    # ── top-level ───────────────────────────────────────────────────────
    def parse_inner(self) -> PineNode:
        node = self._expression()
        if self._peek().kind != KIND_EOF:
            raise PineParseError(
                f"Trailing tokens after expression: "
                f"{self._tokens[self._pos:]!r} in {self._source!r}"
            )
        return node

    # ── grammar rules ───────────────────────────────────────────────────
    def _expression(self) -> PineNode:
        """expression := control_kw | binary_chain"""
        # Top-level control keywords.
        if self._peek().kind == KIND_IDENT:
            kw = CONTROL_KEYWORD_LOOKUP.get(self._peek().value.lower())
            if kw is not None:
                # Distinguish bare control (Else, EndIf, EndForEach, EndCca, EndLb)
                # from chain forms (X.ForEach...). At top level, IDENT-keyword is
                # always a control because the chain form requires a base before
                # the dot.
                return self._control(kw)
        return self._binary_chain()

    def _control(self, keyword: str) -> PineControl:
        self._advance()  # consume keyword
        if self._accept(KIND_LPAREN) is None:
            return PineControl(keyword=keyword, args=())
        if keyword == "Foreach":
            arg = self._foreach_iter()
        else:
            arg = self._binary_chain()
        self._expect(KIND_RPAREN)
        return PineControl(keyword=keyword, args=(arg,))

    def _foreach_iter(self) -> PineNode:
        """The argument to ``Foreach(...)`` is ``var IN expression``.

        Modeled as PineBinaryOp(op='IN', left=ident, right=expr) — the
        same node shape as a binary-op ``IN`` so callers don't have to
        special-case Foreach.
        """
        var_tok = self._expect(KIND_IDENT)
        in_tok = self._peek()
        if in_tok.kind != KIND_IDENT or in_tok.value.lower() != "in":
            raise PineParseError(
                f"Expected 'IN' in Foreach iter, got {in_tok!r} "
                f"in {self._source!r}"
            )
        self._advance()
        right = self._binary_chain()
        return PineBinaryOp(
            op="IN",
            left=PineLiteral(value=var_tok.value, kind=LIT_IDENT_ARG),
            right=right,
        )

    def _binary_chain(self) -> PineNode:
        """Parse an atom, then any number of binary operators applied
        left-to-right with no precedence distinctions."""
        node = self._atom()
        while self._peek().kind in _COMPARISON_OPS:
            op = self._advance()
            # IN has special RHS handling: comma-separated values until close paren.
            if False:  # placeholder: we don't see literal IN at top level often
                pass
            right = self._atom()
            node = PineBinaryOp(op=op.value, left=node, right=right)

        # `IN` written as a bare lowercase/uppercase word is also possible.
        if self._peek().kind == KIND_IDENT and self._peek().value.upper() == "IN":
            self._advance()
            items = [self._atom()]
            while self._accept(KIND_COMMA) is not None:
                items.append(self._atom())
            node = PineBinaryOp(op="IN", left=node, right=PineListLit(items=tuple(items)))

        return node

    def _atom(self) -> PineNode:
        tok = self._peek()

        if tok.kind == KIND_STRING:
            self._advance()
            return PineLiteral(value=tok.value, kind=LIT_STRING)
        if tok.kind == KIND_NUMBER:
            self._advance()
            return PineLiteral(value=tok.value, kind=LIT_NUMBER)
        if tok.kind == KIND_AT_LBRACKET:
            return self._nested_token()
        if tok.kind == KIND_AT_IDENT:
            self._advance()
            base = PineAtName(name=tok.value[1:])  # strip '@'
            # @-name may be the base of a chain.
            if self._peek().kind == KIND_DOT:
                return self._continue_chain(base)
            return base
        if tok.kind == KIND_LBRACKET:
            return self._list_literal()
        if tok.kind == KIND_IDENT:
            lower = tok.value.lower()
            if lower in ("true", "false"):
                self._advance()
                return PineLiteral(value=lower, kind=LIT_BOOL)
            # Top-level call (e.g. CreateVar(...), GetAge(...))?
            # Heuristic: if it's followed immediately by LPAREN AND the
            # name isn't a control keyword (controls were handled by
            # _expression), treat as a call. But — many things look
            # like calls and are actually chains-of-one-segment (e.g.
            # FormatName(F L) standalone is rare but possible). The
            # safer rule: treat any IDENT '(' as the head of a chain
            # so that chained-method form works. The chain will end up
            # with no base segments and one method-call-like first
            # segment. This is structurally identical to a "top-level
            # call" — we use PineCall only for known top-level builtins.
            if self._peek(1).kind == KIND_LPAREN and lower in _TOP_LEVEL_CALL_NAMES:
                return self._top_level_call()
            return self._chain_starting_at_ident()

        raise PineParseError(
            f"Unexpected token {tok!r} starting an atom in {self._source!r}"
        )

    # ── chain / call ────────────────────────────────────────────────────
    def _chain_starting_at_ident(self) -> PineNode:
        head = self._expect(KIND_IDENT)
        base: str = head.value
        # If immediately followed by '(' — bare method-style call with no
        # base. Treat as a Chain with empty-base + one segment, which is
        # equivalent in practice. Pattern matchers can normalise.
        if self._peek().kind == KIND_LPAREN:
            args = self._call_args(head.value)
            chain = PineChain(
                base="", segments=(PineSegment(name=head.value, args=args),)
            )
            return self._continue_chain(chain) if self._peek().kind == KIND_DOT else chain
        return self._continue_chain(base)

    def _continue_chain(self, base) -> PineNode:
        """Given a base value, consume zero or more `.segment` parts and
        return a PineChain. ``base`` may be a str, PineAtName, or PineNested."""
        segments: List[PineSegment] = []
        # If base is already a PineChain (from chain_starting_at_ident
        # constructing one), unpack and continue from there.
        if isinstance(base, PineChain):
            base, segments = base.base, list(base.segments)

        while self._peek().kind == KIND_DOT:
            self._advance()
            name_tok = self._expect(KIND_IDENT)
            if self._peek().kind == KIND_LPAREN:
                args = self._call_args(name_tok.value)
                segments.append(PineSegment(name=name_tok.value, args=args))
            else:
                segments.append(PineSegment(name=name_tok.value, args=None))
        return PineChain(base=base, segments=tuple(segments))

    def _top_level_call(self) -> PineCall:
        name_tok = self._expect(KIND_IDENT)
        args = self._call_args(name_tok.value)
        # _call_args returns None only for property-access; for a real call it returns a tuple.
        assert args is not None
        return PineCall(name=name_tok.value, args=args)

    def _call_args(self, function_name: str) -> Tuple[PineNode, ...]:
        """Parse the arguments inside ``( ... )`` for a function or method
        call. Returns a tuple of arg nodes. Two flavours:

        - ``RAW_ARG_FUNCTIONS`` (FormatName, FormatDate, ...): the entire
          contents are captured as a single LIT_FORMAT literal.
        - default: zero or more comma-separated expressions, with
          dict-literal recognition (``"key":value,...``) for GetByQuery.
        """
        self._expect(KIND_LPAREN)
        if self._peek().kind == KIND_RPAREN:
            self._advance()
            return ()

        if function_name.lower() in RAW_ARG_FUNCTIONS:
            lit = self._capture_raw_until_rparen(LIT_FORMAT)
            return (lit,) if lit.value else ()

        # Dict literal? "key":value, ...
        if self._peek().kind == KIND_STRING and self._peek(1).kind == KIND_COLON:
            dict_lit = self._dict_literal()
            self._expect(KIND_RPAREN)
            return (dict_lit,)

        args: List[PineNode] = [self._binary_chain()]
        while self._accept(KIND_COMMA) is not None:
            args.append(self._binary_chain())
        self._expect(KIND_RPAREN)
        return tuple(args)

    def _capture_raw_until_rparen(self, literal_kind: str) -> PineLiteral:
        """Capture characters from the current source position up to the
        matching ``)``, emit as a single literal. Position-based, like
        the JDA parser's equivalent."""
        anchor = self._peek().pos
        depth = 1
        j = anchor
        n = len(self._source)
        while j < n and depth > 0:
            c = self._source[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise PineParseError(
                f"Unbalanced parentheses while capturing raw arg in {self._source!r}"
            )
        raw = self._source[anchor:j].strip()
        while self._peek().pos < j:
            self._advance()
        if self._peek().kind != KIND_RPAREN:
            raise PineParseError(
                f"Expected ')' after raw capture in {self._source!r}"
            )
        self._advance()
        return PineLiteral(value=raw, kind=literal_kind)

    # ── nested tokens ──────────────────────────────────────────────────
    def _nested_token(self) -> PineNested:
        """Parse a nested ``@[...]`` by finding the matching ``]`` and
        recursively running the parser on the substring."""
        at_lbracket = self._expect(KIND_AT_LBRACKET)
        anchor = at_lbracket.pos + 2  # one past "@["
        depth = 1
        j = anchor
        n = len(self._source)
        while j < n and depth > 0:
            # We need to handle nested @[...] too. The cheapest way is
            # depth tracking on '[' / ']' regardless of whether they're
            # part of "@[" — works because `[` only appears as the
            # start of a token or a list literal, both of which balance
            # with `]`.
            c = self._source[j]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth != 0:
            raise PineParseError(
                f"Unbalanced @[...] starting at {at_lbracket.pos} in {self._source!r}"
            )
        nested_source = self._source[at_lbracket.pos : j + 1]
        # Skip our token cursor past the nested expression.
        while self._peek().pos < j + 1:
            self._advance()
        nested_ast = parse(nested_source)
        return PineNested(inner=nested_ast.inner, raw=nested_source)

    # ── list / dict literals ───────────────────────────────────────────
    def _list_literal(self) -> PineListLit:
        self._expect(KIND_LBRACKET)
        items: List[PineNode] = []
        if self._peek().kind != KIND_RBRACKET:
            items.append(self._binary_chain())
            while self._accept(KIND_COMMA) is not None:
                items.append(self._binary_chain())
        self._expect(KIND_RBRACKET)
        return PineListLit(items=tuple(items))

    def _dict_literal(self) -> PineDictLit:
        entries: List[Tuple[str, PineNode]] = []
        while True:
            key_tok = self._expect(KIND_STRING)
            self._expect(KIND_COLON)
            value = self._binary_chain()
            entries.append((key_tok.value, value))
            if self._accept(KIND_COMMA) is None:
                break
        return PineDictLit(entries=tuple(entries))


# Names that are always parsed as a "top-level" function call rather
# than as a chain whose first segment is a method call. This keeps the
# resulting AST shape predictable for builders. (Both forms would be
# semantically equivalent; we just pick one canonical AST shape.)
_TOP_LEVEL_CALL_NAMES = frozenset({
    "createvar",
    "getage",
    "getdatediff",
    "subdocument",
})


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def parse(source: str) -> PineToken:
    """Parse a Pine expression of the form ``@[...]`` into a PineToken AST.

    Raises PineParseError on malformed input.
    """
    if source is None:
        raise PineParseError("Cannot parse None")
    stripped = source.strip()
    if not (stripped.startswith("@[") and stripped.endswith("]")):
        raise PineParseError(
            f"Expression must be wrapped in @[ ... ]; got {source!r}"
        )
    inner_text = stripped[2:-1]
    cleaned = _repair_whitespace(inner_text).strip()
    if not cleaned:
        raise PineParseError(f"Empty @[] expression in {source!r}")

    tokens = _lex_inner(cleaned)
    parser = _Parser(tokens, cleaned)
    inner = parser.parse_inner()
    return PineToken(inner=inner, raw=source)
