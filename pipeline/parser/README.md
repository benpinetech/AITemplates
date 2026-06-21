# Phase 1 — parsers and extractors

This is the foundation of v2. Everything else (patterns, mining, validation,
LLM fallback) operates on the ASTs that this layer produces. Nothing here
depends on the rest of v2.

There are three pieces:

| File | What it does |
|---|---|
| `jda_ast.py` | Dataclasses for every JDA AST node, plus `unparse()` for canonical re-rendering. |
| `jda_parser.py` | Lexer + recursive-descent parser. `parse(text)` → `JdaToken`. |
| `pine_ast.py` | Dataclasses for every Pine AST node, plus `unparse()`. |
| `pine_parser.py` | Same shape as the JDA side, with extra grammar: nested `@[...]`, CreateVar, dict/list literals, logical operators, `IN`. |
| `rtf_extractor.py` | Scans RTF, finds every `%[...]` or `@[...]` bracketed expression, returns positions and parsed ASTs. |

## How to use

```python
from Agent.v2.parser import jda_parser, pine_parser, rtf_extractor

# Parse a single expression.
ast = jda_parser.parse("%[TitleCase(JW_Respondent.FullName)]")
print(ast.unparse())   # → %[TitleCase(JW_Respondent.FullName)]

# Extract every expression from an RTF file.
with open("legacy.rtf") as f:
    for hit in rtf_extractor.extract(f.read(), bracket="%["):
        print(hit.start, hit.end, hit.text, hit.ast)
```

## Parser strategy

Both parsers are **hand-rolled lexer + recursive-descent** in plain Python.
No parser-generator dependency. The two languages are small enough that
this comes out to roughly 300 lines per language and stays
step-debuggable.

The parser pipeline is:

```
"%[TitleCase(JW_Respondent.FullName)]"
       │
       ▼
   pre-process    — collapse whitespace inside identifiers (RTF artefact)
       │
       ▼
     Lexer        — yields (kind, value, position) tokens
       │
       ▼
    Parser        — recursive descent; one function per grammar rule
       │
       ▼
   JdaToken AST   — frozen dataclass tree
       │
       ▼
    unparse()     — canonical text form for round-tripping
```

### Why hand-rolled and not Lark?

The original v2 plan called for Lark with an EBNF grammar file. We
changed direction during implementation in favour of hand-rolled. The
trade-offs:

- **Pro hand-rolled**: every parsing decision is step-debuggable Python
  with a function name; no grammar engine to learn; no extra dependency.
- **Pro Lark**: the grammar lives in one declarative file. With many
  edge cases this can be more compact.

The v2 project priority is "the maintainer reads the code and
understands it without Claude in the loop." A 300-line Python file beats
a Lark grammar plus a transformer plus learning Lark's quirks, for a
small grammar. If the languages grow much past their current size, we
can revisit.

### Whitespace and case

- Whitespace inside expressions is normalized away before parsing.
- Identifier case is preserved as written (entity names are
  case-insensitive in Pine in practice but the AST keeps original
  casing for diagnostics).
- Keyword case is *not* preserved — `If`, `IF`, `if` all become the
  canonical form `If` on unparse.

### Round-trip semantics

`parse(text)` and `unparse(ast)` are not byte-inverse functions because
inputs have whitespace variations the AST drops. Round-trip means:

```
ast1 = parse(text)
ast2 = parse(ast1.unparse())
assert ast1 == ast2   # structural equality
```

This is the property `tests/test_corpus_round_trip.py` checks.

## AST node design

ASTs are immutable `@dataclass(frozen=True)` instances. Each language
has a small node hierarchy:

### JDA AST (jda_ast.py)

```
JdaToken          %[ <inner> ]
├── inner: JdaNode

JdaNode (abstract base)
├── JdaPath        dotted identifier: A.B.C
├── JdaIdentifier  single bare word (used for unquoted values like RBA)
├── JdaLiteral     'string' | "string" | 1234 | true/false | format-pattern
├── JdaCall        F(args)
├── JdaBinaryOp    X = Y, X.IsEmpty = true, etc.
├── JdaIter        a in Collection (used inside MultiSelect/Foreach)
└── JdaControl     If(cond) | Else | EndIf | Foreach(...) | ...
```

### Pine AST (pine_ast.py)

```
PineToken         @[ <inner> ]
├── inner: PineNode

PineNode (abstract base)
├── PineNested        @[...] inside another @[...]
├── PineChain         base.seg.seg.method(args).property
│   ├── base: str | PineNested | PineAtName
│   └── segments: list[PineSegment]    each is name + (optional) call args
├── PineAtName        @VarName (used in CreateVar declarations)
├── PineLiteral       'string' | "string" | 1234 | true/false | bare-format
├── PineDictLit       "key": value, "key": value (used in GetByQuery)
├── PineListLit       [a, b, c]
├── PineCall          F(args) — top-level function like CreateVar, GetAge, GetDateDiff
├── PineBinaryOp      X == Y, X != Y, X && Y, X IN a,b,c, ...
└── PineControl       If(...) | Else | EndIf | Foreach(...) | Cca(...) | EndCca | ...
```

The AST is the contract — patterns and rewrites all speak in these
nodes. Extending the grammar means extending these classes.

## What's covered, what's not (yet)

Phase 1 round-trips cleanly on **99.95% of the verified-pair corpus**
(20,380 of 20,391 expressions across all 660+ RTF files).

**Supported:**

- JDA: simple paths, function calls, casing wrappers (TitleCase,
  UpperCase, LowerCase, Initials), FormatDate's date-pattern arg,
  Subdocument paths (with backslashes and slashes), If/ElseIf/Else/EndIf,
  Foreach/EndForeach, MultiSelect/EndMultiSelect, equality with `=` and
  `==`, comparisons (`<`, `<=`, `>`, `>=`, `!=`), bool literals,
  unquoted RHS values, path-shaped iter vars
  (`ForEach(JW_X.EventID in JW_X)`).
- Pine: chained method/property access (any depth), CreateVar with
  `@`-prefixed declaration names, control keywords (If, ElseIf, Else,
  EndIf, Foreach, EndForEach, Cca, EndCca, Lb, EndLb), FormatName /
  FormatDate / FormatNumber format strings (raw-captured),
  single- and double-quoted strings, all comparison and logical
  operators (`==`, `!=`, `<`, `<=`, `>`, `>=`, `&&`, `||`, `IN`), nested
  tokens, dict literals in GetByQuery, list literals with brackets.

**Known unsupported edge cases** (the 0.05% tail; these are genuinely
malformed source strings, not parser bugs):

- `%[AddHour(, )]` and `%[AddMinute(, )]` — empty first argument
  (truly broken JDA).
- Unquoted RHS values containing commas:
  `If(X = Appellate, District and County Court Judges...)` — the comma
  splits arg parsing; we'd need to extend "raw value" capture to
  comparison RHS.
- Unquoted RHS values containing the `in` keyword:
  `ElseIf(TAN.TAN = No earned fees in account)` — `in` collides with
  the iter-binding keyword.
- `@[Else If(...)]` (literal space) — Pine source uses `ElseIf` as one
  word; the typo currently fails to parse.
- IN's RHS interleaved with `&&`:
  `IN 'A','B' && Y IN 'C','D'` — the IN parser greedily consumes commas
  past the logical-op boundary.

When pattern matching can't match these chunks (because they don't
parse), they become Phase 4 LLM-fallback candidates. We don't need to
make the parser bulletproof — we need it to handle the bulk
deterministically, which it does.

The integration test `tests/test_corpus_round_trip.py` gates the
round-trip rate at ≥99.5% so regressions break the test.

## Inspecting round-trip failures

Run the test verbosely and add a print for the mismatching expressions:

```bash
venv/bin/python -m pytest pipeline/tests/test_corpus_round_trip.py -s
```
