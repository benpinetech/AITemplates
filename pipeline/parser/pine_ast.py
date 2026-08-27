"""Pine AST — node classes for the @[...] target language.

Pine is richer than JDA. It supports nested tokens (``@[...]`` inside
another ``@[...]``), variable declarations (``CreateVar(@X, ...)``),
dict literals inside ``GetByQuery``, list literals, logical and
comparison operators, and method-style chained calls.

Every node is an immutable frozen dataclass with an ``unparse()``
method that produces canonical text. Round-trip semantics match the
JDA side: ``parse(unparse(parse(s))) == parse(s)``.

Node hierarchy:

    PineToken            top-level wrapper for @[ <inner> ]
    ├── inner: PineNode

    PineNode (abstract)
    ├── PineLiteral      'string' | "string" | 1234 | true/false | format-string
    ├── PineAtName       @Defendant — the @-prefixed declaration name used
    │                    inside CreateVar(@var, source)
    ├── PineNested       @[...] inside another @[...]
    ├── PineChain        base.seg.seg.method(args).seg ...
    │   └── segments: PineSegment[]   (each is a property access or call)
    ├── PineCall         top-level function: CreateVar(...), GetAge(...),
    │                    GetDateDiff(...), Cca(expr) (short form)
    ├── PineDictLit      "key":value, "key":value (used in GetByQuery)
    ├── PineListLit      [a, b, c] (array literal in query parameters)
    ├── PineBinaryOp     X == Y, X != Y, X && Y, X || Y, X IN a,b,c
    └── PineControl      If(cond), Else, EndIf, Foreach(x IN y), Cca(...), ...

Worked examples — full source and the AST it parses to:

    @[Respondent.first.NameLastName]
    ── PineToken(inner=PineChain(base='Respondent', segments=(
           PineSegment('first', None),
           PineSegment('NameLastName', None))))

    @[Respondent.first.FormatName(F L).SetCasing(Title)]
    ── PineToken(inner=PineChain(base='Respondent', segments=(
           PineSegment('first', None),
           PineSegment('FormatName', (PineLiteral('F L', kind='format'),)),
           PineSegment('SetCasing', (PineLiteral('Title', kind='ident-arg'),)))))

    @[CreateVar(@Defendant, @CaseInvolvement.GetByQuery("CaseID":@[builtin.CaseID],"Type":"DEF"))]
    ── PineToken(inner=PineCall('CreateVar', (
           PineAtName('Defendant'),
           PineChain(base=PineAtName('CaseInvolvement'), segments=(
               PineSegment('GetByQuery', (PineDictLit((
                   ('CaseID', PineNested(PineChain(base='builtin', segments=(PineSegment('CaseID', None),)))),
                   ('Type', PineLiteral('DEF', kind='string')),
               )),)),
           )))))

    @[If(@[DefAtty.Any()] == true)]
    ── PineToken(inner=PineControl('If', (
           PineBinaryOp('==',
               PineNested(PineChain(base='DefAtty', segments=(PineSegment('Any', ()),))),
               PineLiteral('true', kind='bool')),)))

Note on string literals: the parser treats string contents as opaque
characters, including any nested ``@[...]`` tokens that appear inside.
That's good enough for pattern matching — patterns can re-parse the
inside lazily if they need to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple, Union


# Literal kinds — discriminator string for PineLiteral.kind.
LIT_STRING = "string"      # 'foo' or "foo" — single source quoting recorded; canonical-emit uses single quotes
LIT_NUMBER = "number"
LIT_BOOL = "bool"          # 'true' or 'false', stored lowercase
LIT_FORMAT = "format"      # FormatName/FormatDate/FormatNumber raw arg
LIT_IDENT_ARG = "ident-arg"  # bare identifier appearing as a method arg
                              # (e.g. SetCasing(Title) — Title is a name, not a string)

VALID_LITERAL_KINDS = frozenset({LIT_STRING, LIT_NUMBER, LIT_BOOL, LIT_FORMAT, LIT_IDENT_ARG})


# Canonical control keywords. Same shape as JDA, plus Pine-specific ones.
CONTROL_KEYWORDS = (
    "If",
    "ElseIf",
    "Else",
    "EndIf",
    "Foreach",
    "EndForEach",
    "Cca",
    "EndCca",
    "Lb",
    "EndLb",
)
CONTROL_KEYWORD_LOOKUP = {k.lower(): k for k in CONTROL_KEYWORDS}


# Method names that, when they appear as the *function argument* of a
# call, want their arguments captured as a single raw format/path literal
# rather than tokenized normally. FormatName(F M L) is the prototype.
RAW_ARG_FUNCTIONS = frozenset({
    "formatname",
    "formatdate",
    "formatnumber",
    "formatphonenumber",  # zero args, but listed for completeness
})


class PineNode:
    """Abstract base for every Pine AST node."""

    def unparse(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError(f"{type(self).__name__} must implement unparse()")


@dataclass(frozen=True)
class PineLiteral(PineNode):
    """A literal: string, number, boolean, format-pattern, or bare ident-arg.

    For ``kind == LIT_STRING``: ``value`` does NOT include the quotes.
    For all other kinds: ``value`` is the source text as written.
    """

    value: str
    kind: str

    def __post_init__(self):
        if self.kind not in VALID_LITERAL_KINDS:
            raise ValueError(
                f"PineLiteral.kind must be one of {sorted(VALID_LITERAL_KINDS)}; "
                f"got {self.kind!r}"
            )

    def unparse(self) -> str:
        if self.kind == LIT_STRING:
            return f"'{self.value}'"
        return self.value


@dataclass(frozen=True)
class PineAtName(PineNode):
    """An ``@``-prefixed name, used as a declaration label inside CreateVar.

    Example: ``@Defendant`` in
    ``CreateVar(@Defendant, @CaseInvolvement.GetByQuery(...))`` — the
    first arg is a PineAtName declaring a new variable; the second arg
    is a chain whose base is also a PineAtName denoting a data source.
    """

    name: str

    def unparse(self) -> str:
        return f"@{self.name}"


@dataclass(frozen=True)
class PineNested(PineNode):
    """A ``@[...]`` token nested inside another ``@[...]``.

    Example: ``"CaseID":@[builtin.CaseID]`` inside a GetByQuery dict.
    """

    inner: PineNode
    raw: str = field(default="", compare=False)

    def unparse(self) -> str:
        return f"@[{self.inner.unparse()}]"


@dataclass(frozen=True)
class PineSegment(PineNode):
    """One segment of a PineChain.

    - ``args is None`` means a bare property access (``.first``).
    - ``args == ()`` means a no-arg method call (``.Any()``).
    - ``args == (...)`` means a method call with one or more arguments.
    """

    name: str
    args: Optional[Tuple[PineNode, ...]]  # None vs () distinguishes property vs no-arg call

    def unparse(self) -> str:
        if self.args is None:
            return self.name
        if not self.args:
            return f"{self.name}()"
        return f"{self.name}(" + ", ".join(a.unparse() for a in self.args) + ")"


# A chain's base may be a bare name (``Respondent``), an @-prefixed
# declaration (``@CaseInvolvement``), or a nested token (rare but
# seen in some patterns).
ChainBase = Union[str, PineAtName, PineNested]


@dataclass(frozen=True)
class PineChain(PineNode):
    """A dotted chain of segments starting from a base.

    Examples:
      ``Respondent.first.NameLastName`` →
        base='Respondent', segments=[first(prop), NameLastName(prop)]
      ``Respondent.first.FormatName(F L).SetCasing(Title)`` →
        base='Respondent', segments=[first(prop), FormatName(call), SetCasing(call)]
      ``@CaseInvolvement.GetByQuery(...)`` →
        base=PineAtName('CaseInvolvement'), segments=[GetByQuery(call)]
    """

    base: ChainBase
    segments: Tuple[PineSegment, ...]

    def unparse(self) -> str:
        if isinstance(self.base, str):
            base_str = self.base
        else:
            base_str = self.base.unparse()
        if not self.segments:
            return base_str
        return base_str + "".join("." + s.unparse() for s in self.segments)


@dataclass(frozen=True)
class PineCall(PineNode):
    """Top-level function call (not on a chain).

    Used for ``CreateVar(...)``, ``GetAge(...)``, ``GetDateDiff(...)``,
    short-form ``Cca(expr)`` and ``Lb(expr)``.
    """

    name: str
    args: Tuple[PineNode, ...]

    def unparse(self) -> str:
        if not self.args:
            return f"{self.name}()"
        return f"{self.name}(" + ", ".join(a.unparse() for a in self.args) + ")"


@dataclass(frozen=True)
class PineDictLit(PineNode):
    """A dict-literal used as the argument to GetByQuery.

    Source form: ``"CaseID":@[builtin.CaseID],"Type":"DEF","IsActive":true``
    """

    entries: Tuple[Tuple[str, PineNode], ...]

    def unparse(self) -> str:
        return ",".join(f'"{k}":{v.unparse()}' for k, v in self.entries)


@dataclass(frozen=True)
class PineListLit(PineNode):
    """An array literal: ``['TC001','TC002']`` or ``[@[builtin.CaseID]]``.

    JDA-style ``"[P,A]"`` (a *string* containing brackets) is NOT this —
    that's a PineLiteral with kind=string. PineListLit only fires when
    the brackets are not enclosed in quotes.
    """

    items: Tuple[PineNode, ...]

    def unparse(self) -> str:
        return "[" + ",".join(i.unparse() for i in self.items) + "]"


@dataclass(frozen=True)
class PineBinaryOp(PineNode):
    """A comparison or logical operator: ==, =, !=, <, <=, >, >=, &&, ||, IN.

    For ``IN``, the ``right`` operand is a PineListLit holding the
    candidate values. We unparse IN with comma-separated values *without*
    the surrounding brackets to match the source form
    (``X IN 'AG01', 'AG02'`` rather than ``X IN ['AG01','AG02']``).

    We also use this node to represent Foreach's ``var IN collection``
    binding, where ``left`` is a LIT_IDENT_ARG and ``right`` is the
    collection expression.
    """

    op: str
    left: PineNode
    right: PineNode

    def unparse(self) -> str:
        if self.op == "IN" and isinstance(self.right, PineListLit):
            values = ", ".join(item.unparse() for item in self.right.items)
            return f"{self.left.unparse()} IN {values}"
        return f"{self.left.unparse()} {self.op} {self.right.unparse()}"


@dataclass(frozen=True)
class PineControl(PineNode):
    """Control-flow keyword: If / ElseIf / Else / EndIf / Foreach /
    EndForEach / Cca / EndCca / Lb / EndLb.

    Method-form Foreach / Cca / Lb (e.g. ``Charges.ForEach(c)``) are
    *chains*, not controls. Only the bare top-level keyword forms hit
    this node.
    """

    keyword: str
    args: Tuple[PineNode, ...]

    def __post_init__(self):
        if self.keyword not in CONTROL_KEYWORDS:
            raise ValueError(
                f"PineControl.keyword must be one of {CONTROL_KEYWORDS}; "
                f"got {self.keyword!r}"
            )

    def unparse(self) -> str:
        if not self.args:
            return self.keyword
        return f"{self.keyword}(" + ", ".join(a.unparse() for a in self.args) + ")"


@dataclass(frozen=True)
class PineToken(PineNode):
    """The top-level ``@[ <inner> ]`` wrapper.

    ``raw`` is the original source text including delimiters, kept for
    diagnostics. Round-trip equality compares ``inner`` only.
    """

    inner: PineNode
    raw: str = field(default="", compare=False)

    def unparse(self) -> str:
        return f"@[{self.inner.unparse()}]"


@dataclass(frozen=True)
class PineRawBlock(PineNode):
    """A verbatim multi-token Pine fragment — one or more ``@[...]``
    tokens with arbitrary literal text between/around them, kept as raw
    text and emitted unchanged.

    A single ``@[...]`` maps to a :class:`PineToken`; but human-authored
    verified suggestions sometimes map one legacy variable to a whole
    block that doesn't fit the one-token-per-mapping model — e.g. the
    gender-pronoun ``@[if(...)]his@[elseif(...)]her@[else]his/her@[endif]``
    where literal pronoun text sits *between* control tokens. There's no
    single AST for that (Pine control flow spans separate tokens), so we
    carry it opaquely: each inner ``@[...]`` is validated at save time,
    and the block is re-inserted byte-for-byte on future conversions.

    Unlike :class:`PineToken`, ``unparse()`` returns the text as-is —
    it is NOT re-wrapped in ``@[...]``.

    ``tokens`` holds the parsed inner ``@[...]`` tokens (in order) so
    consumers that need to look inside — e.g. the prelude generator
    deriving CreateVar declarations from referenced entities — can do so
    without re-parsing. Emission still uses ``text`` verbatim."""

    text: str
    tokens: Tuple["PineToken", ...] = ()

    def unparse(self) -> str:
        return self.text
