"""Pre-pass that swaps If/Else branch content for `.IsEmpty = true`
conditionals.

JDA semantics::

    %[If(X.IsEmpty = true)]   [A — empty case]
    %[Else]                   [B — has-value case]
    %[EndIf]

The Pine translation flips polarity to ``@[If(@[Y.Any()] == true)]``,
which means the *opposite* thing: enter the if-body when the entity
exists. Without compensating, the if-body and else-body would be
attached to the wrong branches.

Compensation: swap the two bodies in the source RTF *before* token
extraction. After swapping, the structure is::

    %[If(X.IsEmpty = true)]   [B — has-value case]
    %[Else]                   [A — empty case]
    %[EndIf]

Subsequent per-token patterns then translate the If-condition to
``@[If(@[Y.Any()] == true)]`` and the bodies stay in the right
branches.

Only swaps when a matching ``%[Else]`` exists at the same nesting
depth — no-Else blocks have no branches to swap. Nested If/EndIf are
handled via depth tracking. Walks back-to-front so earlier byte
positions stay valid as later swaps happen.

This is the v2 port of ``Agent/src/nodes.py::_swap_inverted_branches``.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from . import rtf_extractor


# Match an `If(X.<NameField>.IsEmpty = true)` condition where
# <NameField> is one of the OBA name fields whose IsEmpty check has a
# corresponding `Any() == true` Pine pattern. We deliberately don't
# fire on every `.IsEmpty = true` because non-name IsEmpty checks
# (StateIDNum, address fields, etc.) fall through to `envelope_if`
# which preserves the original polarity — swapping branches there
# would corrupt semantics.
#
# Keep this list in sync with the patterns in
# library/oba/if_isempty.toml.
# All three regexes match against ``Hit.text``, which is the cleaned
# token text *including* the surrounding ``%[...]``.
_INVERTED_IF_RE = re.compile(
    r"""^\s*%\[\s*If\s*\(
        \s*[A-Za-z_][A-Za-z0-9_]*           # entity name
        \s*\.\s*(?:FullName|LastName)       # name-bearing field
        \s*\.\s*(?:IsEmpty|IsNullOrEmpty)
        (?:\s*=\s*(?:true|1))?              # optional " = true" clause
        \s*\)\s*\]\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_IF_ANY_RE = re.compile(r"^\s*%\[\s*If\s*\(", re.IGNORECASE)
_ELSE_RE = re.compile(r"^\s*%\[\s*Else\s*\]\s*$", re.IGNORECASE)
_ENDIF_RE = re.compile(r"^\s*%\[\s*EndIf\s*\]\s*$", re.IGNORECASE)


def swap_inverted_branches(rtf: str) -> str:
    """Swap if/else bodies for every ``If(...IsEmpty=true)`` block that
    has a matching ``Else``. Returns the rewritten RTF.

    Idempotent on input that has no inverted-If blocks. Position-stable
    in the sense that bytes outside the swapped regions are preserved
    exactly; only the bodies between If/Else and Else/EndIf change place.
    """
    hits = list(rtf_extractor.extract(rtf, parse=False))
    if not hits:
        return rtf

    # Locate every (if_idx, else_idx, endif_idx) triple where the If
    # condition uses IsEmpty=true and there's a same-depth Else.
    triples: List[Tuple[int, int, int]] = []
    for i, h in enumerate(hits):
        if not _INVERTED_IF_RE.match(h.text):
            continue
        depth = 1
        else_idx = endif_idx = None
        for j in range(i + 1, len(hits)):
            t = hits[j].text
            if _IF_ANY_RE.match(t):
                depth += 1
            elif _ELSE_RE.match(t) and depth == 1:
                if else_idx is None:
                    else_idx = j
            elif _ENDIF_RE.match(t):
                depth -= 1
                if depth == 0:
                    endif_idx = j
                    break
        if else_idx is not None and endif_idx is not None:
            triples.append((i, else_idx, endif_idx))

    if not triples:
        return rtf

    # Apply swaps back-to-front so earlier byte positions remain valid.
    out = rtf
    for if_idx, else_idx, endif_idx in reversed(triples):
        if_h, else_h, endif_h = hits[if_idx], hits[else_idx], hits[endif_idx]
        if_token = out[if_h.start:if_h.end]
        if_branch = out[if_h.end:else_h.start]
        else_token = out[else_h.start:else_h.end]
        else_branch = out[else_h.end:endif_h.start]
        endif_token = out[endif_h.start:endif_h.end]

        out = (
            out[:if_h.start]
            + if_token + else_branch + else_token + if_branch + endif_token
            + out[endif_h.end:]
        )
    return out
