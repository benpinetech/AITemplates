"""Named hole transforms.

A transform is a pure function ``(captured_value, org_context) → result``.
Transforms are registered by name; pattern files reference them by string.
This decouples pattern files from Python imports.

Today's transforms all operate on captured *strings* (path-segment
captures). When a pattern needs richer behaviour, add a new transform
function and register it in ``_REGISTRY`` at the bottom of the file.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping

from ..parser import pine_parser
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


def translate_jda_entity_to_pine(value: str, org: str) -> str:
    """Map a JDA entity name to its Pine equivalent.

    Two sources are consulted, in order:

      1. The org config's role index (preferred) — built from
         ``v2/grammar/org_overrides/<org>.toml``. This is what new orgs
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
    org_root = _try_load_org(org)
    if org_root is not None:
        from ..grammar.role_index import role_index_for
        idx = role_index_for(org_root)
        if value in idx.jda_to_pine:
            return idx.jda_to_pine[value]
    # Legacy table (fallback).
    if value in _JDA_TO_PINE_ENTITY:
        return _JDA_TO_PINE_ENTITY[value]
    raise UnknownTransformInputError(f"Unknown JDA entity: {value!r}")


def _try_load_org(org: str):
    """Load the OrgRoot for ``org``, or None if there's no config or
    ``org`` is ``"any"``. Caches so repeated calls are cheap."""
    if not org or org == "any":
        return None
    cached = _ORG_CACHE.get(org)
    if cached is not _UNSET:
        return cached
    try:
        from ..grammar.loaders import load_org_overrides
        loaded = load_org_overrides(org)
    except FileNotFoundError:
        loaded = None
    except Exception:  # noqa: BLE001 — never break the engine if config is malformed
        loaded = None
    _ORG_CACHE[org] = loaded
    return loaded


# Tiny sentinel-based cache so we can distinguish "not yet looked up"
# from "looked up and found None".
_UNSET = object()
_ORG_CACHE: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# JDA date format → Pine preset
# ─────────────────────────────────────────────────────────────────────────────
# Source: pine_syntax_ground_truth.txt §3 "Date format presets and patterns".
# We canonicalize the input (strip spaces) before lookup.

_FORMAT_TO_PRESET: Dict[str, str] = {
    "MMMMd,yyyy": "preset1",
    "MMMM,yyyy": "preset3",
    "M/d/yyyy": "preset4",
    "MM/dd/yyyy": "preset5",
    "M/dd/yyyy": "preset6",
}


def format_to_preset(value: str, org: str) -> str:
    """Map a JDA date format string to a Pine preset name.

    Returns the preset name (e.g. ``preset1``) when the format matches a
    known preset; otherwise returns the format string unchanged so the
    pattern can still emit it as a custom format.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"format_to_preset expects a string, got {type(value).__name__}"
        )
    canon = re.sub(r"\s+", "", value)
    return _FORMAT_TO_PRESET.get(canon, value)


# ─────────────────────────────────────────────────────────────────────────────
# JDA Subdocument path → Pine numeric IDs (one or more)
# ─────────────────────────────────────────────────────────────────────────────
# Source: pine_syntax_ground_truth.txt §36 "JDA SubDocument name → Pine
# numeric ID mapping".

# Exact path → list of Pine SubDocument IDs (in source order).
_SUBDOC_PATH_EXACT: Dict[str, List[int]] = {
    # OBA Template\* family — produces SubDocument(5) then (3).
    "Template\\Letterhead": [5, 3],
    "Template\\Address": [5, 3],
    "Template\\to R": [5, 3],
    "Template\\to C": [5, 3],
    "Template\\to R Certified": [5, 3],
    "Template\\to C Certified": [5, 3],
    "Template\\R addresses": [5, 3],
    "Template\\C addresses": [5, 3],
    "Template\\UPL R addresses": [5, 3],
    # Criminal/PD Subdocs\* family.
    "Subdocs\\_Header": [9],
    "Subdocs\\_Letterhead": [7],
    "Subdocs\\_EndOfLetter": [8],
    "Subdocs\\Digital Signature": [27],
    "Subdocs\\ By Digital Signature": [27],
    "Subdocs\\_Sig_CertofServiceDigSig": [38],
    "SUBDOC\\ SubLetterhead": [8],
    "SUBDOC\\": [30],
}


def subdocument_path_to_pine_ids(value: str, org: str) -> List[int]:
    """Map a Subdocument path to one or more Pine numeric IDs.

    Org-aware: in OBA context, every ``Template\\*`` produces ``[5, 3]``
    even if the exact path isn't tabulated.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"subdocument_path_to_pine_ids expects a string, got {type(value).__name__}"
        )
    if value in _SUBDOC_PATH_EXACT:
        return list(_SUBDOC_PATH_EXACT[value])
    # OBA fallback: any Template\* path → [5, 3].
    if org == "oba" and value.lower().startswith("template\\"):
        return [5, 3]
    raise UnknownTransformInputError(
        f"Unknown Subdocument path: {value!r} (org={org!r})"
    )


def expand_subdocument_path(captures: Captures, org: str) -> List[PineToken]:
    """rewrite_function for Subdocument expansion.

    Reads ``captures['path']`` (a path string), looks up the numeric IDs
    via :func:`subdocument_path_to_pine_ids`, and returns one
    ``@[SubDocument(N)]`` PineToken per ID.
    """
    path_capture = captures.get("path")
    if path_capture is None:
        raise ValueError("expand_subdocument_path needs a 'path' capture")
    path_str = path_capture if isinstance(path_capture, str) else getattr(path_capture, "value", str(path_capture))
    ids = subdocument_path_to_pine_ids(path_str, org)
    return [pine_parser.parse(f"@[SubDocument({i})]") for i in ids]


# ─────────────────────────────────────────────────────────────────────────────
# JDA prompt-variable name → Pine prompt name
# ─────────────────────────────────────────────────────────────────────────────
# JDA prompt variables follow ``Name.Name`` (both segments equal). Pine
# uses a clean camel-cased identifier. Section 30 lists known mappings.

_PROMPT_VAR_EXACT: Dict[str, str] = {
    "DateofLetter": "DateOfLetter",
    "DateofLettertoC": "LetterToComplainantDate",
    "DateLetterReceived": "DateLetterReceived",
    "ResponseDueDate": "ResponseDueDate",
    "DateofCtOrder": "DateOfCourtOrder",
    "PRCMeeting": "PRCMeeting",
    "ARCDates": "ARCDates",
}


def prompt_variable_camel_case(value: str, org: str) -> str:
    """Translate a JDA prompt-variable name to its Pine equivalent.

    Raises ``UnknownTransformInputError`` when the name isn't tabulated
    — the prompt-variable pattern is a soft pattern (any path
    ``X.X`` where both segments equal looks structurally like a prompt
    variable but might not actually be one). Raising lets the engine
    fall through to the next candidate or to LLM fallback rather than
    silently emitting a guess.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"prompt_variable_camel_case expects a string, got {type(value).__name__}"
        )
    if value in _PROMPT_VAR_EXACT:
        return _PROMPT_VAR_EXACT[value]
    raise UnknownTransformInputError(f"Unknown prompt variable: {value!r}")


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


def _classify_pine_entity(pine_entity: str, org: str = "any") -> str:
    """Return ``"name"`` (involvement → NameFirstName/NameLastName) or
    ``"personnel"`` (assignment → PersonnelFirstName/PersonnelLastName).
    Raises ``UnknownTransformInputError`` for entities we don't classify
    yet — caller falls through to LLM.

    Prefers the org config's role index; falls back to the hardcoded
    sets for back-compat."""
    org_root = _try_load_org(org)
    if org_root is not None:
        from ..grammar.role_index import role_index_for
        idx = role_index_for(org_root)
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


def jda_entity_to_pine_first_name_field(value: str, org: str) -> str:
    """Map JDA entity → the Pine field name to use for "first name"."""
    pine = translate_jda_entity_to_pine(value, org)
    return "NameFirstName" if _classify_pine_entity(pine, org) == "name" else "PersonnelFirstName"


def jda_entity_to_pine_last_name_field(value: str, org: str) -> str:
    """Map JDA entity → the Pine field name to use for "last name"."""
    pine = translate_jda_entity_to_pine(value, org)
    return "NameLastName" if _classify_pine_entity(pine, org) == "name" else "PersonnelLastName"


def jda_entity_to_pine_info_var(value: str, org: str) -> str:
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
    "format_to_preset": format_to_preset,
    "subdocument_path_to_pine_ids": subdocument_path_to_pine_ids,
    "prompt_variable_camel_case": prompt_variable_camel_case,
    "jda_entity_to_pine_info_var": jda_entity_to_pine_info_var,
    "jda_entity_to_pine_first_name_field": jda_entity_to_pine_first_name_field,
    "jda_entity_to_pine_last_name_field": jda_entity_to_pine_last_name_field,
}

# Rewrite functions are keyed separately because they receive the whole
# captures dict and return a list of PineTokens (rather than a single value).
_REWRITE_FN_REGISTRY: Dict[str, Callable[[Captures, str], List[PineToken]]] = {
    "expand_subdocument_path": expand_subdocument_path,
}


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
