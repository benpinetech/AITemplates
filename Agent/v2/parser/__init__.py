"""Phase 1 — parsers and extractors.

Three pieces (some still being built):

- jda_parser.parse(text)  — parses a single %[...] expression into a JDA AST.
- pine_parser.parse(text) — parses a single @[...] expression into a Pine AST.
- rtf_extractor.extract(rtf) — scans an RTF file, yields (start, end, expression
  text, parsed AST) for every bracketed expression. Prose is untouched.

Modules import lazily so importing the package doesn't pull in modules
that don't yet exist (during incremental development).

See README.md in this directory for the parser strategy and AST design.
"""

# We deliberately do NOT import submodules here. Tests and downstream
# code do `from v2.parser import jda_parser` directly, which works
# because each submodule is a normal Python file. This avoids a
# tight coupling where adding a new submodule means touching this file.
