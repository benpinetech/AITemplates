"""Pydantic-validated loaders for the Phase 3 grammar assets.

Each loader reads one TOML file under this directory, validates it
against a typed model, and returns the model. Validation errors
surface as Pydantic ``ValidationError`` — fail loudly, fail early.

The loaders are pure: they read from disk on each call. Cache at the
caller layer if needed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


from pipeline._resource_path import GRAMMAR_DIR as _GRAMMAR_DIR

_ORG_OVERRIDES_DIR = _GRAMMAR_DIR / "org_overrides"


# ─────────────────────────────────────────────────────────────────────────────
# Models — pine_grammar.toml
# ─────────────────────────────────────────────────────────────────────────────


class TokenFormat(BaseModel):
    opener: str
    closer: str


class DatePreset(BaseModel):
    format: str
    example: str
    notes: str = ""


class GetLabelTable(BaseModel):
    """Mapping of named-string identifier → numeric ID."""

    named_to_numeric: Dict[str, int]


class ControlKeywords(BaseModel):
    canonical: List[str]


class Operators(BaseModel):
    equality: List[str]
    comparison: List[str]
    logical: List[str]
    membership: List[str]


class PineGrammar(BaseModel):
    token_format: TokenFormat
    builtins: Dict[str, str]
    date_presets: Dict[str, DatePreset]
    selectors: Dict[str, List[str]]
    format_tokens: Dict[str, str]
    setcasing: Dict[str, List[str]]
    getlabel: GetLabelTable
    control_keywords: ControlKeywords
    operators: Operators
    builtin_functions: Dict[str, str]


# ─────────────────────────────────────────────────────────────────────────────
# Models — pine_data_model.toml
# ─────────────────────────────────────────────────────────────────────────────


class DataSource(BaseModel):
    name: str
    description: str
    typical_query: str = ""
    common_types: List[str] = Field(default_factory=list)


class QueryParam(BaseModel):
    key: str
    description: str
    example: str = ""


class FieldSet(BaseModel):
    record_type: str
    fields: List[str]
    notes: str = ""


class PineDataModel(BaseModel):
    data_source: List[DataSource]
    query_param: List[QueryParam]
    field_set: List[FieldSet]

    def source_names(self) -> List[str]:
        return [s.name for s in self.data_source]


# ─────────────────────────────────────────────────────────────────────────────
# Models — lint_rules.toml
# ─────────────────────────────────────────────────────────────────────────────


class LintRule(BaseModel):
    id: str
    description: str
    match_regex: str
    severity: str  # "error" | "warning"
    fix_hint: str = ""
    notes: str = ""


class LintRules(BaseModel):
    lint_rule: List[LintRule]


# ─────────────────────────────────────────────────────────────────────────────
# Models — org_overrides/<org>.toml
# ─────────────────────────────────────────────────────────────────────────────


class OrgIdentity(BaseModel):
    id: str
    description: str
    notes: str = ""


class OrgConventions(BaseModel):
    combined_name_format: str
    last_name_only_format: str
    attorney_initials_format: str
    address_setcasing_default: bool
    address_setcasing_exceptions: List[str] = Field(default_factory=list)
    gender_record_pattern: str = ""


class OrgVocabulary(BaseModel):
    entities: List[str]
    builtins: List[str]
    prompt_variables: List[str]


class OrgSubdocFamily(BaseModel):
    path_prefix: str
    output_ids: List[int]
    notes: str = ""


class OrgInvolvementRole(BaseModel):
    """One involvement role (subject, complainant, etc.) for this org.

    The Pine data model is universal — what varies per org is the
    name the org uses for the variable and the ``Type`` code that
    identifies the role inside ``@CaseInvolvement.GetByQuery(...)``.
    """

    pine_name: str
    """The Pine variable name this org exposes the role as. Examples:
    ``Respondent`` (OBA), ``Defendant`` (criminal-defense)."""

    type_code: str
    """The ``Type`` code used in the GetByQuery filter. Examples:
    ``CIT10`` (OBA Respondent), ``DEF`` (criminal Defendant)."""

    jda_aliases: List[str] = Field(default_factory=list)
    """All JDA prefixes that mean this role in source templates.
    Used by ``translate_jda_entity_to_pine`` for normalization."""

    pre_declared: bool = True
    """If True, the Pine variable-screen for this org's deployment
    already has this variable defined; the agent doesn't need to
    emit a ``CreateVar`` for it. If False, prepend a CreateVar."""


class OrgAssignmentRole(OrgInvolvementRole):
    """One assignment role (attorney, prosecutor, judge, investigator…).

    Same shape as an involvement role but the source table is
    ``CaseAssignment`` instead of ``CaseInvolvement``. We use a
    distinct class so consumers can switch on the type."""


class OrgChildEntity(BaseModel):
    """A child entity (e.g. ``RespondentAddress``) — these have FK
    to a Root entity (Personnel / Name), NOT to Case. To use them in
    a template, the Pine renderer needs either a variable-screen
    pre-declaration OR an inline ``CreateVar`` that does the two-step
    lookup.
    """

    pine_name: str
    """Pine variable name for this child entity."""

    parent_pine_name: str
    """Pine variable name of the parent (the involvement or assignment
    role whose ID we'll filter on). E.g. ``Respondent`` for
    ``RespondentAddress``."""

    suffix: str
    """The suffix on the child name that identifies the child type:
    ``Address``, ``Phone``, ``Email``."""

    source_table: str
    """The Pine table the child rows live in: ``NameAddress``,
    ``PersonnelAddress``, ``NamePhone``, etc."""

    jda_aliases: List[str] = Field(default_factory=list)
    """JDA prefixes that produce this child entity in source templates."""

    pre_declared: bool = True
    """If True, this org's variable-screen has the child variable
    pre-declared; no prelude CreateVar needed. If False, prepend the
    parent CreateVar AND the child CreateVar."""


class OrgOverrides(BaseModel):
    org: "OrgRoot"  # forward declaration

    @property
    def conventions(self) -> OrgConventions:
        return self.org.conventions

    @property
    def vocabulary(self) -> OrgVocabulary:
        return self.org.vocabulary


class OrgRoot(BaseModel):
    id: str
    description: str
    notes: str = ""
    conventions: OrgConventions
    pre_bound_collections: Dict[str, str] = Field(default_factory=dict)
    subdoc_template_family: Optional[OrgSubdocFamily] = None
    vocabulary: OrgVocabulary
    # New per-org tables — keyed by an org-internal role id (e.g.
    # "respondent_attorney"), values define how the org names and
    # fetches the role. Empty by default for back-compat with orgs
    # whose TOML pre-dates the role/child sections.
    involvement_roles: Dict[str, OrgInvolvementRole] = Field(default_factory=dict)
    assignment_roles: Dict[str, OrgAssignmentRole] = Field(default_factory=dict)
    child_entities: Dict[str, OrgChildEntity] = Field(default_factory=dict)


OrgOverrides.model_rebuild()


# ─────────────────────────────────────────────────────────────────────────────
# Public loaders
# ─────────────────────────────────────────────────────────────────────────────


def _read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_pine_grammar(path: Optional[Path] = None) -> PineGrammar:
    """Load and validate ``pine_grammar.toml``."""
    p = path or (_GRAMMAR_DIR / "pine_grammar.toml")
    return PineGrammar.model_validate(_read_toml(p))


def load_pine_data_model(path: Optional[Path] = None) -> PineDataModel:
    """Load and validate ``pine_data_model.toml``."""
    p = path or (_GRAMMAR_DIR / "pine_data_model.toml")
    return PineDataModel.model_validate(_read_toml(p))


def load_lint_rules(path: Optional[Path] = None) -> LintRules:
    """Load and validate ``lint_rules.toml``."""
    p = path or (_GRAMMAR_DIR / "lint_rules.toml")
    return LintRules.model_validate(_read_toml(p))


def load_org_overrides(org: str, path: Optional[Path] = None) -> OrgRoot:
    """Load and validate ``org_overrides/<org>.toml``.

    Returns the OrgRoot model directly (not the wrapping ``[org]`` table)
    for ergonomic access — callers usually want ``oba.vocabulary`` not
    ``oba.org.vocabulary``.
    """
    p = path or (_ORG_OVERRIDES_DIR / f"{org}.toml")
    data = _read_toml(p)
    if "org" not in data:
        raise ValueError(
            f"{p} must have a top-level [org] table; got keys {list(data)}"
        )
    return OrgRoot.model_validate(data["org"])
