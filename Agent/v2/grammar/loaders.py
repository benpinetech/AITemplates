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


# Discoverable filesystem layout — assets live next to this module.
_GRAMMAR_DIR = Path(__file__).resolve().parent
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
