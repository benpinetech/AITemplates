"""Named hole transforms.

A transform is a pure function ``(captured_value, agency_context) → result``.
Transforms are registered by name; pattern files reference them by string.
This decouples pattern files from Python imports.

Today's transforms all operate on captured *strings* (path-segment
captures). When a pattern needs richer behaviour, add a new transform
function and register it in ``_REGISTRY`` at the bottom of the file.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping

from ..parser.pine_ast import PineToken


# Type aliases for clarity.
Captures = Mapping[str, Any]
TransformFn = Callable[[Any, str], Any]


# ─────────────────────────────────────────────────────────────────────────────
# JDA → Pine entity name translation
# ─────────────────────────────────────────────────────────────────────────────
# Source: pine_syntax_ground_truth.txt §30 "Entity prefix mapping (JDA → Pine)"
# and §37 "Additional / undocumented JDA variables".

_JDA_TO_PINE_ENTITY: Dict[str, str] = {
    # Involvement entities
    "JW_Respondent": "Respondent",
    "Respondent": "Respondent",
    "Cust_Complainant": "Complainant",
    "JW_Complainant": "Complainant",
    "Complainant": "Complainant",
    "JW_Defendant": "Respondent",       # NOTE: criminal Defendant → OBA Respondent
    "Cust_Defendant": "Respondent",
    "kf_Defendant": "Respondent",
    "JW_VicWitOff": "Complainant",
    "KF_VicWitOff": "Complainant",
    # Assignment entities
    "Cust_RespondentAtty": "RespondentAtty",
    "Cust_OBAAttorney": "OBAAttorney",
    "Cust_Defense": "Defense",
    "JW_Defense": "Defense",
    "Cust_Atty_Def_Active": "Defense",
    "JW_Atty_Def_Active": "Defense",
    "KF_Atty_Def_Active": "Defense",
    "KF_DefAtty": "Defense",
    "Cust_DefAtty": "Defense",
    "JW_Atty_Pros_Active": "Prosecutor",
    "KF_Atty_Pros_Active": "Prosecutor",
    "kf_Atty_Pros_Active": "Prosecutor",
    "KF_Atty_Pros_Inactive": "Prosecutor",
    "Cust_Atty_Pros_Active": "Prosecutor",
    "Cust_Prosecutor": "Prosecutor",
    "JW_Prosecutor": "Prosecutor",
    "Cust_Investigator": "Investigator",
    "JW_Atty_Inv_Active": "Investigator",
    "JW_Investigator": "Investigator",
    "KF_Investigator": "Investigator",
    "Cust_IntakeProsecutor": "IntakeProsecutor",
    "JW_FilingComplainant": "FilingComplainant",
    # Petitioner — corpus uses both Petitioner and Complainant for similar roles.
    "KF_Petitioner": "Complainant",
    # kf_Defendant family
    "kf_VicWitOff": "Complainant",
    # Current user
    "JW_CurrentUser": "cu",
    "CurrentUser": "cu",
    # Address entities (suffix-form). The corpus uses several
    # interchangeable suffixes (_RosterAddress, _MailAddress, _Address)
    # for the same Pine entity.
    "JW_Respondent_RosterAddress": "RespondentAddress",
    "JW_Respondent_Address": "RespondentAddress",
    "JW_Defendant_Address": "RespondentAddress",
    "Cust_RespondentAtty_RosterAddress": "RespondentAttyAddress",
    "Cust_Complainant_MailAddress": "ComplainantAddress",
    "Cust_Complainant_Address": "ComplainantAddress",
    "JW_Complainant_RosterAddress": "ComplainantAddress",
    "JW_VicWitOff_Address": "ComplainantAddress",
    "KF_DefAtty_RosterAddress": "DefenseAddress",
    "JW_Atty_Def_Active_Address": "DefenseAddress",
    "Cust_Atty_Def_Active_Address": "DefenseAddress",
    "KF_Atty_Def_Active_Address": "DefenseAddress",
    "JW_Atty_Pros_Active_Address": "ProsecutorAddress",
    "Cust_Atty_Pros_Active_Address": "ProsecutorAddress",
    "Cust_Investigator_Address": "InvestigatorAddress",
    "Cust_CIPs_Address": "CIPAddress",
    "KF_Atty_Def_Active_Address": "DefenseAddress",
    "kf_VicWitOff_Address": "ComplainantAddress",
    # Added 2026-05 from ground-truth observation. Don't add entries
    # for entities whose target is context-dependent — see
    # LLM_CAPABILITY_FINDINGS.md (Cust_CIPs trial & rollback).
    "KF_Atty_Pros_Active_AgencyNum": "ProsNum",
    "KF_PhoneNumberActive": "ProsecutorPhone",
}


def translate_jda_entity_to_pine(value: str, agency: str) -> str:
    """Map a JDA entity name to its Pine equivalent.

    Two sources are consulted, in order:

      1. The agency config's role index (preferred) — built from
         ``v2/grammar/agency_overrides/<agency>.toml``. This is what new agencies
         configure; it carries deployment-specific labeling.
      2. The legacy hardcoded ``_JDA_TO_PINE_ENTITY`` table (fallback).
         Originally OBA-specific; kept for back-compat with tests and
         pattern files that pre-date the role-config consolidation.

    Raises ``UnknownTransformInputError`` when the entity isn't in
    either source — the pattern engine treats this as "this pattern
    doesn't apply for this input" and falls through.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"translate_jda_entity_to_pine expects a string, got {type(value).__name__}"
        )
    # Org-config-driven lookup (preferred).
    agency_root = _try_load_agency(agency)
    if agency_root is not None:
        from ..grammar.role_index import role_index_for
        idx = role_index_for(agency_root)
        if value in idx.jda_to_pine:
            return idx.jda_to_pine[value]
    # Legacy table (fallback).
    if value in _JDA_TO_PINE_ENTITY:
        return _JDA_TO_PINE_ENTITY[value]
    raise UnknownTransformInputError(f"Unknown JDA entity: {value!r}")


def _try_load_agency(agency: str):
    """Load the AgencyRoot for ``agency``, or None if there's no config or
    ``agency`` is ``"any"``. Caches so repeated calls are cheap."""
    if not agency or agency == "any":
        return None
    cached = _AGENCY_CACHE.get(agency)
    if cached is not _UNSET:
        return cached
    try:
        from ..grammar.loaders import load_agency_overrides
        loaded = load_agency_overrides(agency)
    except FileNotFoundError:
        loaded = None
    except Exception:  # noqa: BLE001 — never break the engine if config is malformed
        loaded = None
    _AGENCY_CACHE[agency] = loaded
    return loaded


# Tiny sentinel-based cache so we can distinguish "not yet looked up"
# from "looked up and found None".
_UNSET = object()
_AGENCY_CACHE: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# JDA entity → Pine "info-var" Name-record name
# ─────────────────────────────────────────────────────────────────────────────
# In OBA Pine, gender / prefix / etc. are read off a Name record loaded
# via CreateVar from the entity's NameID. The naming convention is
# ``<Entity>Info`` (RespondentInfo, ComplainantInfo, …). Patterns that
# emit gender-pronoun blocks need this name; it's not always the same
# as ``<Entity>`` plus a generic suffix because criminal/PD templates
# use ``DefName`` instead. This transform encodes the OBA convention.

_ENTITY_TO_INFO_VAR: Dict[str, str] = {
    "JW_Respondent": "RespondentInfo",
    "Respondent": "RespondentInfo",
    "JW_Defendant": "RespondentInfo",      # OBA: Defendant → Respondent
    "Cust_Defendant": "RespondentInfo",
    "kf_Defendant": "RespondentInfo",
    "JW_Complainant": "ComplainantInfo",
    "Cust_Complainant": "ComplainantInfo",
    "Complainant": "ComplainantInfo",
    "JW_VicWitOff": "ComplainantInfo",
    "KF_VicWitOff": "ComplainantInfo",
}


# Pine entity classes — drives whether ``X.FullName`` (bare) splits
# into Name*Name fields (involvement entities, backed by
# CaseInvolvement) or Personnel*Name fields (assignment entities,
# backed by CaseAssignment). See pine_syntax_ground_truth.txt §30:
# "Bare X.FullName (no wrapper) → split into two tokens".
_INVOLVEMENT_PINE_ENTITIES = frozenset({
    "Respondent", "Complainant", "FilingComplainant", "PrimaryInvolvement",
})
_ASSIGNMENT_PINE_ENTITIES = frozenset({
    "OBAAttorney", "RespondentAtty", "Defense", "Prosecutor",
    "Investigator", "IntakeProsecutor",
})


def _classify_pine_entity(pine_entity: str, agency: str = "any") -> str:
    """Return ``"name"`` (involvement → NameFirstName/NameLastName) or
    ``"personnel"`` (assignment → PersonnelFirstName/PersonnelLastName).
    Raises ``UnknownTransformInputError`` for entities we don't classify
    yet — caller falls through to LLM.

    Prefers the agency config's role index; falls back to the hardcoded
    sets for back-compat."""
    agency_root = _try_load_agency(agency)
    if agency_root is not None:
        from ..grammar.role_index import role_index_for
        idx = role_index_for(agency_root)
        if idx.is_involvement(pine_entity):
            return "name"
        if idx.is_assignment(pine_entity):
            return "personnel"
    if pine_entity in _INVOLVEMENT_PINE_ENTITIES:
        return "name"
    if pine_entity in _ASSIGNMENT_PINE_ENTITIES:
        return "personnel"
    raise UnknownTransformInputError(
        f"don't know whether {pine_entity!r} is involvement or assignment"
    )


def jda_entity_to_pine_first_name_field(value: str, agency: str) -> str:
    """Map JDA entity → the Pine field name to use for "first name"."""
    pine = translate_jda_entity_to_pine(value, agency)
    return "NameFirstName" if _classify_pine_entity(pine, agency) == "name" else "PersonnelFirstName"


def jda_entity_to_pine_last_name_field(value: str, agency: str) -> str:
    """Map JDA entity → the Pine field name to use for "last name"."""
    pine = translate_jda_entity_to_pine(value, agency)
    return "NameLastName" if _classify_pine_entity(pine, agency) == "name" else "PersonnelLastName"


def jda_entity_to_pine_info_var(value: str, agency: str) -> str:
    """Map a JDA entity name to its OBA-Pine Name-record variable name.

    Raises ``UnknownTransformInputError`` for entities not in the
    table — pattern falls through to the next candidate / LLM fallback.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"jda_entity_to_pine_info_var expects a string, got {type(value).__name__}"
        )
    if value in _ENTITY_TO_INFO_VAR:
        return _ENTITY_TO_INFO_VAR[value]
    raise UnknownTransformInputError(f"Unknown entity for info-var: {value!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


class UnknownTransformInputError(ValueError):
    """Raised by a transform when the captured value isn't tabulated.

    The engine treats this as a "no match" signal: the pattern is
    inapplicable for this particular input, and we move on to the next
    candidate pattern.
    """


class UnknownTransformError(KeyError):
    """Raised when a pattern references a transform name that isn't registered."""


_REGISTRY: Dict[str, TransformFn] = {
    "translate_jda_entity_to_pine": translate_jda_entity_to_pine,
    "jda_entity_to_pine_info_var": jda_entity_to_pine_info_var,
    "jda_entity_to_pine_first_name_field": jda_entity_to_pine_first_name_field,
    "jda_entity_to_pine_last_name_field": jda_entity_to_pine_last_name_field,
}

# Rewrite functions are keyed separately because they receive the whole
# captures dict and return a list of PineTokens (rather than a single value).
# Empty today — the authored pattern library that used rewrite_functions was
# removed (commit 6f15cac). Kept as the validation contract for loader.py.
_REWRITE_FN_REGISTRY: Dict[str, Callable[[Captures, str], List[PineToken]]] = {}


def get_transform(name: str) -> TransformFn:
    """Look up a transform by registered name."""
    if name not in _REGISTRY:
        raise UnknownTransformError(
            f"No transform registered with name {name!r}. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def get_rewrite_function(name: str) -> Callable[[Captures, str], List[PineToken]]:
    """Look up a rewrite_function by registered name."""
    if name not in _REWRITE_FN_REGISTRY:
        raise UnknownTransformError(
            f"No rewrite_function registered with name {name!r}. "
            f"Available: {sorted(_REWRITE_FN_REGISTRY)}"
        )
    return _REWRITE_FN_REGISTRY[name]


def registered_transforms() -> List[str]:
    """List the names of all registered value-transforms (for diagnostics)."""
    return sorted(_REGISTRY)


def registered_rewrite_functions() -> List[str]:
    """List the names of all registered rewrite_functions (for diagnostics)."""
    return sorted(_REWRITE_FN_REGISTRY)
