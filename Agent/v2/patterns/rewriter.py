"""Pattern rewriter — apply a matched Pattern to produce Pine ASTs.

Inputs:
  - the matched Pattern (with rewrite source or rewrite_function)
  - the Captures dict from the matcher
  - the org context (passed to transforms)

Output:
  - a list of PineToken ASTs (length 1 for single-output rewrites,
    >1 for multi-output rewrites like Subdocument expansion)

The rewriter has two paths:

  1. Template rewrite: parse each rewrite source string, walk the AST,
     substitute every hole reference (``$name``) with the resolved value
     (the captured value, possibly passed through a transform).
  2. Function rewrite: call the registered rewrite_function with the
     captures and org. Used for cases where the output shape varies
     with input (e.g. SubDocument expanding to a variable number of IDs).

Limitations (Phase 2):
  - When an atom hole captured a whole JDA AST node, the rewrite source
    cannot reference it directly — substitution into segment positions
    needs a string. Use a derived hole with a transform if you need to
    extract data from a captured AST.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping

from ..parser import pine_parser
from ..parser.jda_ast import JdaNode, JdaToken
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
from . import transforms
from .holes import (
    atom_hole_name_pine,
    hole_name,
    is_atom_hole_pine,
    is_hole_segment,
)
from .matcher import Captures
from .schema import Hole, Pattern


class PatternRewriteError(RuntimeError):
    """Raised when a pattern's rewrite cannot be produced."""


# ─────────────────────────────────────────────────────────────────────────────
# Hole resolution — apply transforms to derived holes
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_holes(
    declared: Dict[str, Hole], captures: Captures, org: str
) -> Dict[str, Any]:
    """Build the substitution dict by combining direct captures and
    derived hole values.

    A derived hole's value is computed by feeding the source hole's
    captured value through the transform. If the source hole isn't in
    captures (the matcher didn't bind it), the derived hole is dropped.
    """
    resolved: Dict[str, Any] = {}
    # Pass 1: direct captures.
    for name, value in captures.items():
        resolved[name] = value
    # Pass 2: derived holes.
    for name, hole in declared.items():
        if not hole.derive_from:
            continue
        source_value = captures.get(hole.derive_from)
        if source_value is None:
            continue
        # If the captured value is an AST node, transforms operate on
        # strings — fall back to .unparse() so the transform sees text.
        if not isinstance(source_value, str):
            source_value = getattr(source_value, "unparse", lambda: str(source_value))()
            if isinstance(source_value, type(None)):
                continue
        try:
            fn = transforms.get_transform(hole.transform)  # type: ignore[arg-type]
        except transforms.UnknownTransformError as e:
            raise PatternRewriteError(str(e)) from e
        try:
            resolved[name] = fn(source_value, org)
        except transforms.UnknownTransformInputError:
            # Transform refused this input — propagate as a no-rewrite
            # signal so the engine can fall through to the next pattern.
            raise
        except Exception as e:  # noqa: BLE001
            raise PatternRewriteError(
                f"transform {hole.transform!r} on hole {name!r} "
                f"failed for input {source_value!r}: {e}"
            ) from e
    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# Textual pre-substitution
# ─────────────────────────────────────────────────────────────────────────────

# Pre-substitution runs on the *rewrite source string* before we hand
# it to the Pine parser. It replaces every ``$name`` reference whose
# resolved value is a STRING with that string's text. This is the only
# way to plug a hole into a position where the Pine parser captures
# raw text and won't expose it to AST substitution — most importantly
# *inside Pine string literals*, e.g. ``'@[$info_var.Gender]'``. Holes
# whose resolved value is an AST node (atom-position captures) are
# left alone for the AST-substitution pass to handle.

_HOLE_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _pre_substitute_text(src: str, resolved: Mapping[str, Any]) -> str:
    string_holes = {k: v for k, v in resolved.items() if isinstance(v, str)}
    if not string_holes:
        return src

    def repl(m: "re.Match[str]") -> str:
        name = m.group(1)
        return string_holes.get(name, m.group(0))

    return _HOLE_REF_RE.sub(repl, src)


# ─────────────────────────────────────────────────────────────────────────────
# AST substitution
# ─────────────────────────────────────────────────────────────────────────────


def _substitute(node: PineNode, resolved: Mapping[str, Any]) -> PineNode:
    """Recursively rebuild a Pine AST with hole references replaced.

    Atom holes (a chain like ``$cond`` standing alone) are replaced with
    the captured AST node directly. Segment holes (``$entity`` as a
    chain base or segment name) are replaced with the captured string.
    Unresolved holes are left untouched and passed through — useful for
    diagnostics, since the unparser emits them verbatim.
    """
    if is_atom_hole_pine(node):
        name = atom_hole_name_pine(node)
        if name in resolved:
            value = resolved[name]
            if isinstance(value, PineNode):
                return value
            if isinstance(value, PineToken):
                return value.inner
            if isinstance(value, str):
                # A string captured at atom position: parse as a Pine
                # expression and use its inner.
                return pine_parser.parse(f"@[{value}]").inner
            if isinstance(value, JdaNode) and not isinstance(value, JdaToken):
                # JDA AST captured at atom position. Round-trip through
                # text: most JDA shapes (paths, calls, comparisons,
                # bool/string literals) parse identically as Pine, so
                # an envelope pattern like ``@[If($cond)]`` can splice
                # in whatever the source ``%[If(...)]`` had. JDA-only
                # shapes (e.g. a JdaIter) won't reparse cleanly and
                # will surface a clear error.
                pine_text = value.unparse()
                try:
                    return pine_parser.parse(f"@[{pine_text}]").inner
                except pine_parser.PineParseError as e:
                    raise PatternRewriteError(
                        f"hole {name!r} captured JDA expression "
                        f"{pine_text!r} that does not reparse as Pine: {e}"
                    ) from e
            raise PatternRewriteError(
                f"hole {name!r} resolved to unsupported atom value: {value!r}"
            )
        return node

    if isinstance(node, PineChain):
        new_base = _substitute_base(node.base, resolved)
        new_segments = tuple(_substitute_segment(s, resolved) for s in node.segments)
        return PineChain(base=new_base, segments=new_segments)

    if isinstance(node, PineNested):
        return PineNested(inner=_substitute(node.inner, resolved), raw=node.raw)

    if isinstance(node, PineCall):
        return PineCall(
            name=node.name,
            args=tuple(_substitute(a, resolved) for a in node.args),
        )

    if isinstance(node, PineControl):
        return PineControl(
            keyword=node.keyword,
            args=tuple(_substitute(a, resolved) for a in node.args),
        )

    if isinstance(node, PineBinaryOp):
        return PineBinaryOp(
            op=node.op,
            left=_substitute(node.left, resolved),
            right=_substitute(node.right, resolved),
        )

    if isinstance(node, PineDictLit):
        return PineDictLit(
            entries=tuple((k, _substitute(v, resolved)) for k, v in node.entries),
        )

    if isinstance(node, PineListLit):
        return PineListLit(items=tuple(_substitute(i, resolved) for i in node.items))

    if isinstance(node, PineLiteral) and node.value.startswith("$"):
        # Literal-position hole — when the rewrite source has e.g.
        # ``FormatDate($preset)``, ``$preset`` is captured by the
        # parser as a literal whose value starts with ``$``. Substitute
        # the resolved string in.
        name = node.value[1:]
        if name in resolved:
            value = resolved[name]
            if isinstance(value, str):
                return PineLiteral(value=value, kind=node.kind)
            if isinstance(value, PineLiteral):
                return value
            # AST node captured at literal position — fall back to
            # whatever the source literal carried (e.g. JdaLiteral has
            # ``.value`` and ``.kind``).
            src_value = getattr(value, "value", None)
            if isinstance(src_value, str):
                return PineLiteral(value=src_value, kind=node.kind)
            raise PatternRewriteError(
                f"literal hole {name!r} resolved to unsupported value: {value!r}"
            )
        return node

    # Literal / AtName — leaves, nothing to substitute.
    return node


def _substitute_base(base, resolved: Mapping[str, Any]):
    """A PineChain.base may be a hole-string, a literal string, a
    PineAtName, or a PineNested. Substitute string-typed holes; leave
    nodes alone (they'll be substituted at recursion if applicable)."""
    if isinstance(base, str) and is_hole_segment(base):
        name = hole_name(base)
        if name in resolved:
            value = resolved[name]
            if isinstance(value, str):
                return value
            raise PatternRewriteError(
                f"chain-base hole {name!r} can only be filled by a string; "
                f"got {value!r} (kind={type(value).__name__})"
            )
        return base
    return base


def _substitute_segment(seg: PineSegment, resolved: Mapping[str, Any]) -> PineSegment:
    """A PineSegment.name may be a hole; args may contain holes."""
    new_name = seg.name
    if is_hole_segment(seg.name):
        name = hole_name(seg.name)
        if name in resolved:
            value = resolved[name]
            if not isinstance(value, str):
                raise PatternRewriteError(
                    f"segment-name hole {name!r} can only be filled by a string"
                )
            new_name = value
    if seg.args is None:
        return PineSegment(name=new_name, args=None)
    new_args = tuple(_substitute(a, resolved) for a in seg.args)
    return PineSegment(name=new_name, args=new_args)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def rewrite(pattern: Pattern, captures: Captures, org: str) -> List[PineToken]:
    """Apply a matched pattern, returning its Pine outputs.

    Raises ``PatternRewriteError`` on rewriter-side failures (unknown
    transform, bad substitution shape, etc.). Lets ``UnknownTransformInputError``
    propagate so the engine can treat it as "this pattern doesn't apply
    to this particular input" and try the next pattern.
    """
    if pattern.rewrite_function:
        try:
            fn = transforms.get_rewrite_function(pattern.rewrite_function)
        except transforms.UnknownTransformError as e:
            raise PatternRewriteError(str(e)) from e
        return list(fn(captures, org))

    rewrite_tokens = pattern.rewrite_tokens()
    if rewrite_tokens is None:
        # Schema validation should prevent this.
        raise PatternRewriteError(
            f"pattern {pattern.id!r} has no rewrite source or rewrite_function"
        )

    resolved = _resolve_holes(pattern.holes, captures, org)
    outputs: List[PineToken] = []
    for tok_src in rewrite_tokens:
        substituted = _pre_substitute_text(tok_src, resolved)
        try:
            tok_ast = pine_parser.parse(substituted)
        except pine_parser.PineParseError as e:
            raise PatternRewriteError(
                f"pattern {pattern.id!r} has unparseable rewrite source "
                f"{substituted!r}: {e}"
            ) from e
        new_inner = _substitute(tok_ast.inner, resolved)
        outputs.append(PineToken(inner=new_inner, raw=tok_ast.raw))
    return outputs


# ─────────────────────────────────────────────────────────────────────────────
# Chunk rewriting
# ─────────────────────────────────────────────────────────────────────────────


def rewrite_chunk(
    pattern: Pattern,
    captures: Captures,
    org: str,
    convert_token: Callable[[JdaToken], List[PineToken]],
) -> List[PineToken]:
    """Apply a chunk pattern's rewrite, given the captures from a
    successful chunk match.

    Each rewrite element is either:
      - a Pine token source string (``"@[...]"``) — parsed and emitted with
        any string-typed hole substitutions applied, just like the
        single-token rewriter;
      - a bare hole reference (``"$name"``) — looked up in captures. If the
        captured value is a JdaToken (chunk-position capture), it's
        recursively converted via ``convert_token`` and the resulting
        Pine outputs are spliced into the rewrite stream.

    ``convert_token`` is injected by the engine to break a circular
    import — typically it's just ``lambda t: engine.convert(t, lib, org).outputs``.
    Raises ``UnknownTransformInputError`` (re-raised) when a captured
    sub-token has no matching pattern, so the engine can fall through.
    """
    if pattern.rewrite_function:
        try:
            fn = transforms.get_rewrite_function(pattern.rewrite_function)
        except transforms.UnknownTransformError as e:
            raise PatternRewriteError(str(e)) from e
        return list(fn(captures, org))

    rewrite_tokens = pattern.rewrite_tokens()
    if rewrite_tokens is None:
        raise PatternRewriteError(
            f"pattern {pattern.id!r} has no rewrite source or rewrite_function"
        )

    resolved = _resolve_holes(pattern.holes, captures, org)
    outputs: List[PineToken] = []
    for elem in rewrite_tokens:
        # Sequence-hole reference: $name... → expand to recursively-converted
        # outputs for each captured token.
        if elem.endswith("...") and elem.startswith("$"):
            hole_n = elem[1:-3]
            if not all(c.isalnum() or c == "_" for c in hole_n):
                raise PatternRewriteError(
                    f"pattern {pattern.id!r} has malformed sequence-hole element {elem!r}"
                )
            if hole_n not in captures:
                raise PatternRewriteError(
                    f"pattern {pattern.id!r} rewrite refers to unbound sequence hole {hole_n!r}"
                )
            captured_seq = captures[hole_n]
            if not isinstance(captured_seq, list):
                raise PatternRewriteError(
                    f"sequence hole {hole_n!r} expected a list, got {type(captured_seq).__name__}"
                )
            for tok in captured_seq:
                if not isinstance(tok, JdaToken):
                    raise PatternRewriteError(
                        f"sequence hole {hole_n!r} contains non-JdaToken {tok!r}"
                    )
                sub_outputs = convert_token(tok)
                if not sub_outputs:
                    raise transforms.UnknownTransformInputError(
                        f"sequence-hole token has no matching sub-pattern: "
                        f"{tok.unparse()!r}"
                    )
                outputs.extend(sub_outputs)
            continue

        # Single-token hole reference: $name → recursively convert the captured token.
        if elem.startswith("$") and len(elem) >= 2 and all(c.isalnum() or c == "_" for c in elem[1:]):
            hole_n = elem[1:]
            if hole_n not in captures:
                raise PatternRewriteError(
                    f"pattern {pattern.id!r} rewrite refers to unbound hole {hole_n!r}"
                )
            captured = captures[hole_n]
            if isinstance(captured, JdaToken):
                sub_outputs = convert_token(captured)
                if not sub_outputs:
                    raise transforms.UnknownTransformInputError(
                        f"captured token {hole_n!r} has no matching sub-pattern: "
                        f"{captured.unparse()!r}"
                    )
                outputs.extend(sub_outputs)
            elif isinstance(captured, PineToken):
                outputs.append(captured)
            elif isinstance(captured, str):
                outputs.append(pine_parser.parse(f"@[{captured}]"))
            else:
                raise PatternRewriteError(
                    f"chunk-rewrite hole {hole_n!r} captured unsupported "
                    f"value: {captured!r}"
                )
            continue

        # Otherwise treat as a Pine token source with hole substitution
        # (textual first, then AST).
        substituted = _pre_substitute_text(elem, resolved)
        try:
            tok_ast = pine_parser.parse(substituted)
        except pine_parser.PineParseError as e:
            raise PatternRewriteError(
                f"pattern {pattern.id!r} has unparseable rewrite element "
                f"{substituted!r}: {e}"
            ) from e
        new_inner = _substitute(tok_ast.inner, resolved)
        outputs.append(PineToken(inner=new_inner, raw=tok_ast.raw))
    return outputs
