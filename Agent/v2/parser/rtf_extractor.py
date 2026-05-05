"""RTF extractor — pulls bracketed expressions out of an RTF file
without disturbing the surrounding prose.

The extractor walks the raw RTF source character-by-character, finds
each ``%[...]`` (legacy) or ``@[...]`` (Pine) opener, follows balanced
brackets to the matching close, and yields the start/end positions
plus the cleaned-up expression text.

This is a port of the v1 logic in ``Agent/src/nodes.py`` with two changes:

1. **Splits a unified API across both bracket styles.** Pass
   ``bracket="%["`` for JDA, ``bracket="@["`` for Pine.
2. **Returns a parsed AST alongside the cleaned text** when the
   ``parse`` flag is true (default). Failures don't stop the walk —
   they're returned with ``ast=None`` and ``error=<message>``.

The walk is carefully written to skip RTF control words inside the
brackets (``\\par``, ``\\fcs1``, hex escapes ``\\'27``, etc.). RTF
sometimes splits a single ``%[`` across two formatting runs:
``%}{...\n[`` — a small pre-pass collapses these back to ``%[`` so
the bracket scanner sees a contiguous opener.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Literal, Optional

from . import jda_parser, pine_parser

# Pre-pass: collapse RTF-fragmented "%[" openers. RTF can emit
# ``%}{\rtlch\foo\loch\f1 \n[Token]}`` where the runs split between
# ``%`` and ``[``. After collapse: ``%[Token]``.
_FRAGMENTED_OPENER = re.compile(r"%\}(?:\{[^\[\]]*?\n)(?=\[)")


def _normalize_rtf(rtf: str) -> str:
    """Stitch fragmented %[ openers back together. Returns a string with
    the same length-or-shorter RTF; positions in the returned string are
    what the rest of the extractor uses.
    """
    return _FRAGMENTED_OPENER.sub("%", rtf)


def _is_hex(c: str) -> bool:
    return c in "0123456789abcdefABCDEF"


@dataclass(frozen=True)
class Hit:
    """One bracketed expression found in an RTF file.

    Attributes:
        start: byte position in the *normalized* RTF where the opener begins.
        end: byte position one past the closing ``]``.
        text: the expression with RTF control codes stripped, suitable for
            display or for passing to a parser.
        ast: the parsed AST, or ``None`` if parsing failed.
        error: the parse error message, or ``None`` on success.
    """

    start: int
    end: int
    text: str
    ast: Optional[object] = None
    error: Optional[str] = None


# Strings used to start a bracket. Both share the ``[`` body so the
# bracket-balancing logic is the same.
JDA_BRACKET = "%["
PINE_BRACKET = "@["
BracketStyle = Literal["%[", "@["]


def _scan_one(rtf: str, opener_start: int, opener: str) -> Optional[tuple[int, str]]:
    """Find the matching ``]`` for an opener at ``opener_start``.

    Returns ``(end_pos_exclusive, cleaned_text)`` or None if the brackets
    don't balance.
    """
    depth = 1
    pos = opener_start + len(opener)
    n = len(rtf)
    while pos < n and depth > 0:
        c = rtf[pos]
        if c == "\\":
            # RTF control word, control symbol, or hex escape. Skip past.
            pos += 1
            if pos >= n:
                break
            if rtf[pos] == "'" and pos + 2 < n and _is_hex(rtf[pos + 1]) and _is_hex(rtf[pos + 2]):
                pos += 3
                continue
            if rtf[pos].isalpha():
                while pos < n and rtf[pos].isalpha():
                    pos += 1
                if pos < n and rtf[pos] == "-":
                    pos += 1
                while pos < n and rtf[pos].isdigit():
                    pos += 1
                # Single trailing space is the control-word terminator.
                if pos < n and rtf[pos] == " ":
                    pos += 1
            else:
                pos += 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        pos += 1
    if depth != 0:
        return None

    raw = rtf[opener_start:pos]
    cleaned = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", raw)   # control words
    cleaned = re.sub(r"\\'\w{2}", "", cleaned)            # hex escapes
    cleaned = re.sub(r"[{}]", "", cleaned)                # RTF braces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()        # collapse whitespace

    if cleaned.startswith(opener) and cleaned.endswith("]") and len(cleaned) > len(opener) + 1:
        # Trim any whitespace immediately inside the brackets.
        inner = cleaned[len(opener):-1].strip()
        cleaned = f"{opener}{inner}]"
    return pos, cleaned


def extract(
    rtf: str,
    bracket: BracketStyle = JDA_BRACKET,
    parse: bool = True,
) -> Iterator[Hit]:
    """Yield every bracketed expression found in ``rtf``.

    Args:
        rtf: the RTF source. Will be RTF-normalized internally.
        bracket: ``"%["`` for JDA, ``"@["`` for Pine.
        parse: if True (default), also parse each hit into an AST. On
            parse failure the Hit's ``ast`` is None and ``error`` carries
            the message; iteration continues.

    Yields:
        Hit objects in source order. Duplicates are not deduplicated —
        every occurrence yields its own Hit.
    """
    if bracket not in (JDA_BRACKET, PINE_BRACKET):
        raise ValueError(f"bracket must be '%[' or '@['; got {bracket!r}")
    if parse:
        parser_fn = jda_parser.parse if bracket == JDA_BRACKET else pine_parser.parse
        parser_err = jda_parser.JdaParseError if bracket == JDA_BRACKET else pine_parser.PineParseError
    else:
        parser_fn = None
        parser_err = None

    text = _normalize_rtf(rtf)
    i = 0
    n = len(text)
    while i < n:
        start = text.find(bracket, i)
        if start < 0:
            return
        scan = _scan_one(text, start, bracket)
        if scan is None:
            # Unbalanced — skip past the opener and keep searching.
            i = start + len(bracket)
            continue
        end, cleaned = scan

        ast = None
        err = None
        if parser_fn is not None:
            try:
                ast = parser_fn(cleaned)
            except Exception as e:  # noqa: BLE001 — parser_err type varies
                if parser_err is None or isinstance(e, parser_err):
                    err = str(e)
                else:
                    raise

        yield Hit(start=start, end=end, text=cleaned, ast=ast, error=err)
        i = end


def extract_text(text: str, bracket: BracketStyle = JDA_BRACKET, parse: bool = True) -> Iterator[Hit]:
    """Convenience for non-RTF sources (plain text containing brackets).

    Identical to ``extract`` but skips the RTF normalisation pass.
    Useful for the verified-pair text files in
    ``phase1-jda-to-pine-extractor/output/<template>/{legacy_cleaned,pine}.txt``.
    """
    if bracket not in (JDA_BRACKET, PINE_BRACKET):
        raise ValueError(f"bracket must be '%[' or '@['; got {bracket!r}")
    if parse:
        parser_fn = jda_parser.parse if bracket == JDA_BRACKET else pine_parser.parse
        parser_err = jda_parser.JdaParseError if bracket == JDA_BRACKET else pine_parser.PineParseError
    else:
        parser_fn = None
        parser_err = None

    i = 0
    n = len(text)
    while i < n:
        start = text.find(bracket, i)
        if start < 0:
            return
        # Plain-text scan: just balance brackets, no RTF control handling.
        depth = 1
        pos = start + len(bracket)
        while pos < n and depth > 0:
            c = text[pos]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            pos += 1
        if depth != 0:
            i = start + len(bracket)
            continue
        cleaned = text[start:pos].strip()
        # Normalize whitespace inside.
        if cleaned.startswith(bracket) and cleaned.endswith("]"):
            inner = cleaned[len(bracket):-1].strip()
            cleaned = f"{bracket}{inner}]"
        ast = None
        err = None
        if parser_fn is not None:
            try:
                ast = parser_fn(cleaned)
            except Exception as e:  # noqa: BLE001
                if parser_err is None or isinstance(e, parser_err):
                    err = str(e)
                else:
                    raise
        yield Hit(start=start, end=pos, text=cleaned, ast=ast, error=err)
        i = pos
