"""Helpers for working with hole references inside parsed pattern ASTs.

We don't add new AST node classes for holes. Instead, the pattern parser
reuses the production parsers (extended to accept ``$`` as an identifier
start) and the matcher recognises holes by looking for identifiers
whose first character is ``$``. The two shapes:

  - **Atom hole** — a hole standing in for an entire expression:
    in JDA, ``JdaPath(parts=('$cond',))``;
    in Pine, ``PineChain(base='$cond', segments=())``.

  - **Segment hole** — a hole standing in for one identifier segment of
    a path: in JDA, ``JdaPath(parts=('$entity', 'FullName'))``; in Pine,
    a ``PineChain.base`` of ``'$entity'`` followed by literal segments,
    or a ``PineSegment`` whose ``name`` starts with ``$``.

These helpers return the hole name (without the ``$``) when given a
node that is in fact a hole, and ``None`` otherwise.
"""

from __future__ import annotations

from typing import Optional

from ..parser.jda_ast import JdaPath
from ..parser.pine_ast import PineChain


HOLE_PREFIX = "$"


def is_hole_segment(segment: str) -> bool:
    """True if a string segment is a hole reference (starts with ``$``)."""
    return isinstance(segment, str) and segment.startswith(HOLE_PREFIX)


def hole_name(segment: str) -> str:
    """Strip the leading ``$`` from a hole segment string."""
    if not is_hole_segment(segment):
        raise ValueError(f"not a hole segment: {segment!r}")
    return segment[1:]


def is_atom_hole_jda(node) -> bool:
    """True if `node` is a JDA atom hole (single-segment path that's a hole)."""
    return (
        isinstance(node, JdaPath)
        and len(node.parts) == 1
        and is_hole_segment(node.parts[0])
    )


def atom_hole_name_jda(node: JdaPath) -> str:
    """Get the hole name for a JDA atom-hole node."""
    if not is_atom_hole_jda(node):
        raise ValueError(f"not a JDA atom hole: {node!r}")
    return hole_name(node.parts[0])


def is_atom_hole_pine(node) -> bool:
    """True if `node` is a Pine atom hole (a chain with $base and no segments)."""
    return (
        isinstance(node, PineChain)
        and isinstance(node.base, str)
        and is_hole_segment(node.base)
        and not node.segments
    )


def atom_hole_name_pine(node: PineChain) -> str:
    """Get the hole name for a Pine atom-hole node."""
    if not is_atom_hole_pine(node):
        raise ValueError(f"not a Pine atom hole: {node!r}")
    return hole_name(node.base)
