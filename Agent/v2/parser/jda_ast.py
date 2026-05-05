"""JDA AST — node classes for the legacy %[...] template language.

Every node is an immutable frozen dataclass. Equality is structural
(two nodes with the same shape and field values compare equal), which
is what pattern matching and round-trip tests rely on.

Each node has an `unparse()` method that produces a canonical string
form. Whitespace from the original source is *not* preserved — the
unparser uses one canonical spacing. Round-trip means:

    ast1 = parse(s)
    ast2 = parse(ast1.unparse())
    assert ast1 == ast2

(structural equality), not `unparse(parse(s)) == s`.

Node hierarchy:

    JdaToken           top-level wrapper for %[ <inner> ]
    ├── inner: JdaNode
    JdaNode (abstract)
    ├── JdaPath          dotted access:    A.B.C  (single-segment paths
    │                    also use this — JdaPath(('RBA',)) covers both
    │                    "field access on RBA" and "bare value RBA")
    ├── JdaLiteral       quoted/numeric/bool literal
    ├── JdaCall          function call:    F(args)
    ├── JdaBinaryOp      comparison:       X = Y, X == Y, etc.
    ├── JdaIter          loop binding:     a in Collection
    └── JdaControl       control keyword:  If(cond), Else, EndIf, Foreach(...), ...

Examples — full source and the AST it parses to:

    %[JW_Respondent.FullName]
    ── JdaToken(inner=JdaPath(['JW_Respondent', 'FullName']))

    %[TitleCase(JW_Respondent.FullName)]
    ── JdaToken(inner=JdaCall('TitleCase', [JdaPath(['JW_Respondent', 'FullName'])]))

    %[FormatDate(CurrentDate(), MMMM d, yyyy)]
    ── JdaToken(inner=JdaCall('FormatDate', [JdaCall('CurrentDate', []),
                                             JdaLiteral('MMMM d, yyyy', 'format')]))

    %[If(Cust_RespondentAtty.FullName.IsEmpty = true)]
    ── JdaToken(inner=JdaControl('If', [
           JdaBinaryOp('=',
               JdaPath(['Cust_RespondentAtty', 'FullName', 'IsEmpty']),
               JdaLiteral('true', 'bool'))]))

    %[Else]
    ── JdaToken(inner=JdaControl('Else', []))

    %[Subdocument(Template\\Letterhead)]
    ── JdaToken(inner=JdaCall('Subdocument', [JdaLiteral('Template\\Letterhead', 'path')]))

    %[MultiSelect(a in JW_Defendant_Address)]
    ── JdaToken(inner=JdaControl('MultiSelect', [
           JdaIter('a', JdaPath(['JW_Defendant_Address']))]))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple


# Discriminator strings used by JdaLiteral.kind. Documenting them in one
# place so the parser and the unparser stay in sync.
LITERAL_STRING = "string"   # 'foo' or "foo"; .value has no surrounding quotes
LITERAL_NUMBER = "number"   # 42, 3.14
LITERAL_BOOL = "bool"       # 'true' or 'false', stored lowercase
LITERAL_FORMAT = "format"   # FormatDate's date-pattern arg, captured raw
LITERAL_PATH = "path"       # Subdocument's backslash-path, captured raw

VALID_LITERAL_KINDS = frozenset(
    {LITERAL_STRING, LITERAL_NUMBER, LITERAL_BOOL, LITERAL_FORMAT, LITERAL_PATH}
)

# Canonical spelling for control keywords. The parser case-folds inputs
# to match these; the unparser emits these as-is. Adding a new keyword
# here is the only place it needs to be added (parser does a lookup).
CONTROL_KEYWORDS = (
    "If",
    "ElseIf",
    "Else",
    "EndIf",
    "Foreach",
    "EndForeach",
    "MultiSelect",
    "EndMultiSelect",
)
CONTROL_KEYWORD_LOOKUP = {k.lower(): k for k in CONTROL_KEYWORDS}


class JdaNode:
    """Abstract base for every JDA AST node.

    Every concrete subclass must implement `unparse()`. We don't use
    `abc.ABC` because the dataclass machinery doesn't compose with it
    cleanly; instead we rely on subclasses overriding the method.
    """

    def unparse(self) -> str:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError(f"{type(self).__name__} must implement unparse()")


@dataclass(frozen=True)
class JdaPath(JdaNode):
    """A dotted-access path. ``parts`` is one or more identifiers.

    Examples:
        ``JW_Respondent`` → JdaPath(parts=('JW_Respondent',))
        ``JW_Respondent.FullName`` → JdaPath(parts=('JW_Respondent', 'FullName'))
        ``Cust_RespondentAtty.FullName.IsEmpty``
            → JdaPath(parts=('Cust_RespondentAtty', 'FullName', 'IsEmpty'))

    The first part typically carries an entity prefix (``JW_``, ``Cust_``,
    ``KF_``, ``kf_``). Pattern rewrites strip these — they're part of the
    string, not parsed separately.
    """

    parts: Tuple[str, ...]

    def unparse(self) -> str:
        return ".".join(self.parts)


@dataclass(frozen=True)
class JdaLiteral(JdaNode):
    """A literal value: string, number, boolean, FormatDate pattern, or
    Subdocument path.

    ``kind`` is one of the ``LITERAL_*`` constants in this module. The
    parser uses it to know how to render the value back; ``string`` adds
    quotes, ``format`` and ``path`` are emitted bare (their content is
    unstructured).
    """

    value: str
    kind: str  # one of VALID_LITERAL_KINDS

    def __post_init__(self):
        if self.kind not in VALID_LITERAL_KINDS:
            raise ValueError(
                f"JdaLiteral.kind must be one of {sorted(VALID_LITERAL_KINDS)}; "
                f"got {self.kind!r}"
            )

    def unparse(self) -> str:
        if self.kind == LITERAL_STRING:
            # Single-quote canonical (JDA mostly doesn't have strings, but
            # we still pick a canonical form for any that show up).
            return f"'{self.value}'"
        # number / bool / format / path: emit the raw token text
        return self.value


@dataclass(frozen=True)
class JdaCall(JdaNode):
    """A function or method call: ``F(arg1, arg2, ...)``.

    Used for casing wrappers (``TitleCase(...)``, ``UpperCase(...)``,
    ``LowerCase(...)``), name formatters (``Initials(...)``),
    date helpers (``FormatDate(...)``, ``CurrentDate()``), and
    document inclusions (``Subdocument(...)``).

    The function name preserves source casing (``TitleCase`` stays
    ``TitleCase``, not lowercased).
    """

    name: str
    args: Tuple[JdaNode, ...]

    def unparse(self) -> str:
        if not self.args:
            return f"{self.name}()"
        return f"{self.name}(" + ", ".join(a.unparse() for a in self.args) + ")"


@dataclass(frozen=True)
class JdaBinaryOp(JdaNode):
    """A binary comparison or assignment-style expression.

    JDA conditions use both ``=`` and ``==`` for equality; the parser
    keeps the operator that was written so we don't lose information
    about the source style. Both forms are common in the corpus.
    """

    op: str  # "=", "==", "!=", "<", "<=", ">", ">="
    left: JdaNode
    right: JdaNode

    def unparse(self) -> str:
        return f"{self.left.unparse()} {self.op} {self.right.unparse()}"


@dataclass(frozen=True)
class JdaIter(JdaNode):
    """A ``var in Collection`` binding inside MultiSelect or Foreach.

    Example: ``%[MultiSelect(a in JW_Defendant_Address)]`` parses
    the inner ``a in JW_Defendant_Address`` as a JdaIter where
    ``var = "a"`` and ``collection = JdaPath(('JW_Defendant_Address',))``.

    JDA writes this with lowercase ``in``; the unparser emits ``in``
    (lowercase) for canonical form.
    """

    var: str
    collection: JdaNode

    def unparse(self) -> str:
        return f"{self.var} in {self.collection.unparse()}"


@dataclass(frozen=True)
class JdaControl(JdaNode):
    """A control-flow keyword: If, ElseIf, Else, EndIf, Foreach,
    EndForeach, MultiSelect, EndMultiSelect.

    Standalone keywords (Else, EndIf, EndForeach, EndMultiSelect) have
    an empty ``args`` tuple. Block-openers (If, ElseIf, Foreach,
    MultiSelect) have one argument: the condition or iterator.
    """

    keyword: str  # canonical case from CONTROL_KEYWORDS
    args: Tuple[JdaNode, ...]

    def __post_init__(self):
        if self.keyword not in CONTROL_KEYWORDS:
            raise ValueError(
                f"JdaControl.keyword must be one of {CONTROL_KEYWORDS}; "
                f"got {self.keyword!r}"
            )

    def unparse(self) -> str:
        if not self.args:
            return self.keyword
        body = ", ".join(a.unparse() for a in self.args)
        return f"{self.keyword}({body})"


@dataclass(frozen=True)
class JdaToken(JdaNode):
    """The top-level ``%[ <inner> ]`` wrapper.

    ``inner`` is the parsed AST. ``raw`` is kept for diagnostics: the
    original source text including delimiters and any whitespace, so
    error messages can quote what the user actually wrote. Round-trip
    tests compare ``inner`` for structural equality and ignore ``raw``.
    """

    inner: JdaNode
    raw: str = field(default="", compare=False)

    def unparse(self) -> str:
        return f"%[{self.inner.unparse()}]"
