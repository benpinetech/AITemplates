"""Pine ``CreateVar`` prelude generation.

Pine templates that reference **child** entities (those whose foreign
key points at a Root entity rather than the Case) need an inline
``@[CreateVar(...)]`` declaration before the body uses them — otherwise
the Pine renderer can't reach them.

This module derives the required prelude from the entities that appear
in the agent's converted output, so the agent produces self-contained
templates that don't rely on external "variable screen" pre-declarations
at the destination Pine install.

The classification rules come from the OBA corpus and the converter's
explanation:

* **Root entities** — sourced from ``Case*`` tables (CaseInvolvement,
  CaseAssignment, CaseAgency, CaseCharge, Event, Case). They have FK
  directly to Case and can be used without a template-local CreateVar
  (the variable screen handles them).

* **Child entities** — sourced from ``NameAddress`` / ``PersonnelAddress``
  / ``NamePhone`` / etc. Their FK points at a Root entity's primary
  key, so they need a TWO-STEP lookup:
  1. CreateVar the Root entity (parent).
  2. CreateVar the Child entity, using the parent's NameID / PersonnelID.

The agent calls :func:`generate_prelude` once per template with the
list of Pine tokens it produced, and gets back the ordered list of
CreateVar declarations to prepend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..grammar.loaders import AgencyRoot
from ..grammar.role_index import RoleIndex, role_index_for
from ..parser.pine_ast import PineAtName, PineChain, PineToken


# ─────────────────────────────────────────────────────────────────────────────
# Pine "Type" code lookup tables
# ─────────────────────────────────────────────────────────────────────────────
# These were mined directly from the OBA corpus — each Pine variable
# in a ``CreateVar(@X, @CaseInvolvement.GetByQuery("Type":"..."))``
# declaration. If we encounter an entity not in either table, we
# decline to generate a prelude rather than guess the type code.

INVOLVEMENT_TYPE_CODES: Dict[str, str] = {
    "Respondent": "CIT10",
    "Complainant": "CIT01",
    "Defendant": "DEF",
    "PrChild": "PKID",
    "Child": "KID",
}

ASSIGNMENT_TYPE_CODES: Dict[str, str] = {
    "OBAAttorney": "CIT14",
    "RespondentAtty": "CIT12",
    "Investigator": "CIT13",
    "Judge": "JDG",
    "DefAtty": "DA",
    "Defense": "DA",
    "DefAttorney": "DA",
    "Prosecutor": "PROS",
    "ProsAttorney": "PA",
}


# Child-entity suffix → (Name-table-source, Personnel-table-source).
# Involvement parents (Respondent, Complainant, …) bring a NameID;
# assignment parents (OBAAttorney, RespondentAtty, …) bring a
# PersonnelID. The same suffix maps to different source tables
# depending on which kind of parent it hangs off.
CHILD_SUFFIXES: Dict[str, Tuple[str, str]] = {
    "Address": ("NameAddress", "PersonnelAddress"),
    "Phone":   ("NamePhone",   "PersonnelPhone"),
    "Email":   ("NameEmail",   "PersonnelEmail"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────


def _is_known_root(name: str) -> bool:
    return name in INVOLVEMENT_TYPE_CODES or name in ASSIGNMENT_TYPE_CODES


def split_child_entity(name: str) -> Optional[Tuple[str, str, str]]:
    """If ``name`` is a known child entity (e.g. ``ComplainantAddress``,
    ``RespondentAttyPhone``), return ``(parent, suffix, source_table)``.

    The classification is naming-based and deterministic:
      ``<Parent><Suffix>``  where ``Parent`` is in INVOLVEMENT/ASSIGNMENT
      and ``Suffix`` is one of ``Address``/``Phone``/``Email``.

    Returns ``None`` for names that don't match the pattern.
    """
    for suffix, (name_tbl, pers_tbl) in CHILD_SUFFIXES.items():
        if not name.endswith(suffix) or len(name) == len(suffix):
            continue
        parent = name[: -len(suffix)]
        if parent in INVOLVEMENT_TYPE_CODES:
            return (parent, suffix, name_tbl)
        if parent in ASSIGNMENT_TYPE_CODES:
            return (parent, suffix, pers_tbl)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Declarations
# ─────────────────────────────────────────────────────────────────────────────


def parent_declaration(parent: str, idx: Optional[RoleIndex] = None) -> Optional[str]:
    """Return the CreateVar string that declares the parent Root entity.

    Consults the role index first (agency-config-driven) for the type
    code and source table; falls back to the hardcoded tables for
    back-compat. Returns ``None`` when the parent isn't classifiable.
    """
    # Org-config path
    if idx is not None and parent in idx.type_codes:
        code = idx.type_codes[parent]
        if not code:
            return None  # role defined but no type code yet — can't emit
        if idx.is_involvement(parent):
            source = "CaseInvolvement"
        elif idx.is_assignment(parent):
            source = "CaseAssignment"
        else:
            return None
        return (
            f'@[CreateVar(@{parent}, '
            f'@{source}.GetByQuery("CaseID":@[builtin.CaseID],'
            f'"Type":"{code}"))]'
        )
    # Hardcoded fallback
    if parent in INVOLVEMENT_TYPE_CODES:
        code = INVOLVEMENT_TYPE_CODES[parent]
        return (
            f'@[CreateVar(@{parent}, '
            f'@CaseInvolvement.GetByQuery("CaseID":@[builtin.CaseID],'
            f'"Type":"{code}"))]'
        )
    if parent in ASSIGNMENT_TYPE_CODES:
        code = ASSIGNMENT_TYPE_CODES[parent]
        return (
            f'@[CreateVar(@{parent}, '
            f'@CaseAssignment.GetByQuery("CaseID":@[builtin.CaseID],'
            f'"Type":"{code}"))]'
        )
    return None


def child_declaration(child: str, parent: str, source_table: str) -> str:
    """Return the CreateVar for a child entity, assuming ``parent`` is
    already in scope. The query key is ``NameID`` when the source is
    Name*, otherwise ``PersonnelID``."""
    fk = "NameID" if source_table.startswith("Name") else "PersonnelID"
    return (
        f'@[CreateVar(@{child}, '
        f'@{source_table}.GetByQuery("{fk}":@[{parent}.first.{fk}]))]'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parsing — the inverse of the declaration builders above
# ─────────────────────────────────────────────────────────────────────────────
# A mapper's corrected CreateVar carries the structured facts the agency
# config needs (type code for a root, parent/source for a child). The
# CreateVar syntax is regular, so targeted searches extract each fact
# without a full balanced-bracket parse.


@dataclass(frozen=True)
class CreateVarFacts:
    """Structured facts extracted from a single CreateVar declaration.

    ``kind`` is ``"involvement"`` / ``"assignment"`` (root entities, with a
    ``type_code``) or ``"child"`` (with a ``parent`` and ``suffix``).
    """

    var_name: str
    kind: str
    source_table: str
    type_code: Optional[str] = None
    parent: Optional[str] = None
    suffix: Optional[str] = None


_CV_VAR_RE = re.compile(r"CreateVar\(\s*@(\w+)")
_CV_SRC_RE = re.compile(r",\s*@(\w+)\.GetByQuery")
_CV_TYPE_RE = re.compile(r'"Type"\s*:\s*"([^"]*)"')
_CV_FK_RE = re.compile(r'"(?:NameID|PersonnelID)"\s*:\s*@\[?(\w+)\.')


def parse_createvar(pine_str: str) -> Optional[CreateVarFacts]:
    """Parse a single ``@[CreateVar(...)]`` declaration into structured facts.

    Returns ``None`` when the string isn't a recognisable CreateVar (so a
    caller can map over a mixed token list and keep only the CreateVars).
    Root entities are classified by their source table (CaseInvolvement vs
    CaseAssignment); children by a ``Name*`` / ``Personnel*`` source, with
    the parent read from the foreign-key reference.
    """
    if "CreateVar" not in pine_str:
        return None
    mvar = _CV_VAR_RE.search(pine_str)
    msrc = _CV_SRC_RE.search(pine_str)
    if not mvar or not msrc:
        return None
    var_name = mvar.group(1)
    source = msrc.group(1)

    if source == "CaseInvolvement":
        mt = _CV_TYPE_RE.search(pine_str)
        return CreateVarFacts(var_name, "involvement", source,
                              type_code=mt.group(1) if mt else None)
    if source == "CaseAssignment":
        mt = _CV_TYPE_RE.search(pine_str)
        return CreateVarFacts(var_name, "assignment", source,
                              type_code=mt.group(1) if mt else None)

    # Child entity: parent comes from the FK reference, suffix from the
    # source-table name (NameAddress → Address, PersonnelPhone → Phone, …).
    mfk = _CV_FK_RE.search(pine_str)
    parent = mfk.group(1) if mfk else None
    suffix = next((s for s in ("Address", "Phone", "Email")
                   if source.endswith(s)), None)
    return CreateVarFacts(var_name, "child", source, parent=parent, suffix=suffix)


# ─────────────────────────────────────────────────────────────────────────────
# Reference extraction — what entity bases does the converted output use?
# ─────────────────────────────────────────────────────────────────────────────


def _chain_base_name(chain: PineChain) -> Optional[str]:
    """Return the base identifier of a PineChain, or ``None`` if the
    base is an @-prefixed source name (not a referenced entity)."""
    base = chain.base
    if isinstance(base, str):
        return base
    return None  # PineAtName — that's a data source, not a referenced entity


def referenced_entities(pine_outputs: Sequence[PineToken]) -> List[str]:
    """Collect distinct entity bases referenced anywhere in the output.

    Walks each PineToken's inner expression — including chains nested
    inside If/ElseIf/ForEach control args (``@[If(@[Respondent.Any()]
    == true)]``), binary operands, and function arguments. Returns
    names in first-appearance order so the generated prelude is
    stable.
    """
    import dataclasses

    seen: Set[str] = set()
    order: List[str] = []

    def walk(node) -> None:
        if node is None or isinstance(node, (str, int, float, bool, bytes)):
            return
        if isinstance(node, PineChain):
            name = _chain_base_name(node)
            if name is not None and name not in seen:
                seen.add(name)
                order.append(name)
            # fall through so we also visit segments' args
        # Lists / tuples of nodes (e.g. PineControl.args, segment.args)
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        # Any dataclass: recurse into every field that might hold a node.
        if dataclasses.is_dataclass(node):
            for fld in dataclasses.fields(node):
                walk(getattr(node, fld.name))
            return
        # Last-resort: try common attribute names. Most Pine AST nodes
        # are dataclasses so this rarely matters.
        for attr in ("chain", "value", "left", "right", "inner", "args"):
            if hasattr(node, attr):
                walk(getattr(node, attr))

    for tok in pine_outputs:
        walk(tok.inner)
    return order


# ─────────────────────────────────────────────────────────────────────────────
# Top-level: generate the ordered, deduplicated prelude
# ─────────────────────────────────────────────────────────────────────────────


def generate_prelude(
    pine_outputs: Sequence[PineToken],
    agency_overrides: Optional[AgencyRoot] = None,
) -> List[str]:
    """Generate the CreateVar prelude needed to make ``pine_outputs`` valid.

    When ``agency_overrides`` is supplied, the agency config drives both the
    classification (which Pine entities are children) AND the
    pre-declared filter (entities the agency's variable screen already
    declares are SKIPPED — no inline CreateVar needed). Without an
    agency override, falls back to the hardcoded classification tables
    AND skips no entities (i.e. assumes nothing is pre-declared, which
    is the safe default for a fresh deployment).

    Returns a list of CreateVar declaration strings, topologically
    ordered (parent before child) and deduplicated. Empty list if no
    child-entity references that need declaring are detected.
    """
    idx = role_index_for(agency_overrides)

    declarations: Dict[str, str] = {}
    order: List[str] = []

    for entity in referenced_entities(pine_outputs):
        # Pull child info from the agency config first; fall back to
        # the hardcoded suffix mapping if the agency doesn't define it.
        if idx.is_child(entity):
            parent = idx.parent_of(entity)
            source_tbl = idx.child_source.get(entity, "")
            # Respect pre_declared: skip if the agency's variable screen
            # already exposes this child.
            if entity in idx.pre_declared:
                continue
        else:
            child_info = split_child_entity(entity)
            if child_info is None:
                continue
            parent, _suffix, source_tbl = child_info

        if not parent or not source_tbl:
            continue

        # Parent declaration — but only if the agency doesn't already
        # have it pre-declared.
        if parent not in declarations and parent not in idx.pre_declared:
            pdecl = parent_declaration(parent, idx)
            if pdecl is not None:
                declarations[parent] = pdecl
                order.append(parent)

        # Child declaration. We always need to emit this when the
        # child itself isn't pre-declared (checked above) and the
        # parent is reachable (either declared above or pre-declared).
        parent_ok = parent in declarations or parent in idx.pre_declared
        if parent_ok and entity not in declarations:
            declarations[entity] = child_declaration(entity, parent, source_tbl)
            order.append(entity)

    return [declarations[n] for n in order]


# ─────────────────────────────────────────────────────────────────────────────
# RTF helpers
# ─────────────────────────────────────────────────────────────────────────────


_RTF_PREAMBLE_END_RE = re.compile(rb"")  # unused placeholder; see prepend_prelude


def prepend_prelude_to_rtf(rtf: str, prelude_lines: List[str]) -> str:
    """Insert prelude lines at the top of the document body.

    For RTF, "top of body" means after any header / font / colour
    tables and stylesheet — i.e. after the last ``}`` that closes the
    document preamble and before content begins. We use a heuristic:
    insert immediately before the first ``\\par`` or just inside the
    document body if we can't find one. If neither boundary is found
    (plain text input), we prepend at the very start.

    The prelude is bracketed by ``\\par`` so it appears on its own
    visual line in the RTF reader.
    """
    if not prelude_lines:
        return rtf
    block = "\n".join(prelude_lines)
    # Plain text (no RTF header) — just prepend.
    if not rtf.lstrip().startswith("{\\rtf"):
        return block + "\n\n" + rtf

    # RTF: find the first \par after the document preamble. The
    # preamble ends with the last closing brace before a body
    # paragraph break. Conservative heuristic: insert right after the
    # first ``\par`` we find.
    par_idx = rtf.find("\\par")
    if par_idx < 0:
        # No paragraph break — fall back to prepending right before
        # the final closing brace.
        close_idx = rtf.rfind("}")
        if close_idx < 0:
            return block + "\n\n" + rtf
        return rtf[:close_idx] + " " + block + " " + rtf[close_idx:]
    # Insert immediately before the first \par, then a \par after the
    # prelude so the body's own \par still separates it cleanly.
    return rtf[:par_idx] + block + " \\par " + rtf[par_idx:]
