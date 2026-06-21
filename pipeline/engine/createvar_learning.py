"""Learn agency CreateVar definitions from mapper corrections.

The HITL learning loop for CreateVars. When a mapper writes or fixes a
``@[CreateVar(...)]`` declaration in the converter, we parse the corrected
declaration into structured facts (:func:`prelude.parse_createvar`) and
persist them into the agency's *learned* role tables
(:func:`grammar.loaders.write_learned_tables`).

Because :func:`prelude.generate_prelude` reads those role tables, the next
conversion regenerates the corrected CreateVar automatically — and for
every future template, not just the one the mapper was editing. Per the
domain notes: different agencies have different involvement type codes, so
the corrections are learned *per agency*.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Sequence

from .prelude import parse_createvar
from ..grammar.loaders import (
    _LEARNED_TABLES,
    _load_learned_tables,
    write_learned_tables,
)


def _camel_to_snake(name: str) -> str:
    """``DefendantAddress`` → ``defendant_address`` for a stable table key."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return s.lower()


def learn_createvars(
    agency: str,
    pine_strings: Sequence[str],
    learned_dir: Optional[Path] = None,
) -> dict:
    """Parse CreateVar declarations and merge them into ``agency``'s learned
    role tables.

    ``pine_strings`` is any list of Pine token strings — non-CreateVar tokens
    are ignored, so the caller can pass the whole prelude (or document)
    without filtering. Re-learning an entity (same Pine name) updates its
    entry. Returns ``{"learned": <count>, "entities": [names]}``.
    """
    facts = [f for f in (parse_createvar(s) for s in pine_strings) if f is not None]

    # Start from the existing learned tables so learning is incremental.
    existing = _load_learned_tables(agency, learned_dir)
    tables = {t: dict(existing.get(t, {})) for t in _LEARNED_TABLES}

    learned_names: List[str] = []
    for f in facts:
        key = _camel_to_snake(f.var_name)
        if f.kind == "involvement":
            tables["involvement_roles"][key] = {
                "pine_name": f.var_name,
                "type_code": f.type_code or "",
                "pre_declared": False,
            }
        elif f.kind == "assignment":
            tables["assignment_roles"][key] = {
                "pine_name": f.var_name,
                "type_code": f.type_code or "",
                "pre_declared": False,
            }
        elif f.kind == "child":
            # A child needs a parent + suffix to be declarable; skip if the
            # FK reference didn't parse (nothing to link it to).
            if not f.parent or not f.suffix:
                continue
            tables["child_entities"][key] = {
                "pine_name": f.var_name,
                "parent_pine_name": f.parent,
                "suffix": f.suffix,
                "source_table": f.source_table,
                "pre_declared": False,
            }
        else:
            continue
        learned_names.append(f.var_name)

    if learned_names:
        write_learned_tables(agency, tables, learned_dir)

    return {"learned": len(learned_names), "entities": learned_names}
