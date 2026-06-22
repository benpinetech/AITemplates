"""Pydantic-validated loaders for the Phase 3 grammar assets.

Each loader reads one TOML file under this directory, validates it
against a typed model, and returns the model. Validation errors
surface as Pydantic ``ValidationError`` — fail loudly, fail early.

The loaders are pure: they read from disk on each call. Cache at the
caller layer if needed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


from pipeline._resource_path import (
    GRAMMAR_DIR as _GRAMMAR_DIR,
    agency_overrides_dir as _agency_overrides_dir,
)

# Agencies are user data (created/renamed/deleted at runtime), so this resolves
# to a writable location: JDA_AGENCY_DIR (userData) in packaged builds, the
# source tree in dev. Resolved at import — the sidecar spawns a fresh process
# per call with the env set, so the override always takes effect.
_AGENCY_OVERRIDES_DIR = _agency_overrides_dir()


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
# Models — agency_overrides/<agency>.toml
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
    # Default to empty so a newly-added agency (just id + description) is
    # valid. With empty lists the LLM prompt simply carries no vocabulary
    # hints — conversion still works, just with less guidance.
    entities: List[str] = Field(default_factory=list)
    builtins: List[str] = Field(default_factory=list)
    prompt_variables: List[str] = Field(default_factory=list)


class OrgSubdocFamily(BaseModel):
    path_prefix: str
    output_ids: List[int]
    notes: str = ""


class OrgInvolvementRole(BaseModel):
    """One involvement role (subject, complainant, etc.) for this agency.

    The Pine data model is universal — what varies per agency is the
    name the agency uses for the variable and the ``Type`` code that
    identifies the role inside ``@CaseInvolvement.GetByQuery(...)``.
    """

    pine_name: str
    """The Pine variable name this agency exposes the role as. Examples:
    ``Respondent`` (OBA), ``Defendant`` (criminal-defense)."""

    type_code: str
    """The ``Type`` code used in the GetByQuery filter. Examples:
    ``CIT10`` (OBA Respondent), ``DEF`` (criminal Defendant)."""

    jda_aliases: List[str] = Field(default_factory=list)
    """All JDA prefixes that mean this role in source templates.
    Used by ``translate_jda_entity_to_pine`` for normalization."""

    pre_declared: bool = True
    """If True, the Pine variable-screen for this agency's deployment
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
    """If True, this agency's variable-screen has the child variable
    pre-declared; no prelude CreateVar needed. If False, prepend the
    parent CreateVar AND the child CreateVar."""


class OrgOverrides(BaseModel):
    agency: "AgencyRoot"  # forward declaration

    @property
    def conventions(self) -> OrgConventions:
        return self.agency.conventions

    @property
    def vocabulary(self) -> OrgVocabulary:
        return self.agency.vocabulary


class AgencyRoot(BaseModel):
    id: str
    description: str
    notes: str = ""
    # conventions has no live consumers (leftover from the removed pattern
    # engine) — optional so a minimal agency need not declare it.
    conventions: Optional[OrgConventions] = None
    pre_bound_collections: Dict[str, str] = Field(default_factory=dict)
    subdoc_template_family: Optional[OrgSubdocFamily] = None
    # Defaulted so an agency with just id + description validates and converts.
    vocabulary: OrgVocabulary = Field(default_factory=OrgVocabulary)
    # New per-agency tables — keyed by an agency-internal role id (e.g.
    # "respondent_attorney"), values define how the agency names and
    # fetches the role. Empty by default for back-compat with agencies
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


def load_agency_overrides(agency: str, path: Optional[Path] = None) -> AgencyRoot:
    """Load and validate ``agency_overrides/<agency>.toml``.

    Returns the AgencyRoot model directly (not the wrapping ``[agency]`` table)
    for ergonomic access — callers usually want ``oba.vocabulary`` not
    ``oba.agency.vocabulary``.

    When loading the real config (``path`` is None), learned CreateVar
    entities (``agency_overrides/learned/<agency>.toml`` — written by the
    HITL learning loop) are merged into the role tables, learned entries
    taking precedence. Passing an explicit ``path`` loads that file exactly,
    with no learned merge (used by tests).
    """
    p = path or (_AGENCY_OVERRIDES_DIR / f"{agency}.toml")
    data = _read_toml(p)
    if "agency" not in data:
        raise ValueError(
            f"{p} must have a top-level [agency] table; got keys {list(data)}"
        )
    agency_tbl = data["agency"]
    if path is None:
        learned = _load_learned_tables(agency)
        for table_name, entries in learned.items():
            merged = dict(agency_tbl.get(table_name, {}))
            merged.update(entries)          # learned overrides/adds by key
            agency_tbl[table_name] = merged
    return AgencyRoot.model_validate(agency_tbl)


# ─────────────────────────────────────────────────────────────────────────────
# Learned CreateVar entities — the HITL learning store
# ─────────────────────────────────────────────────────────────────────────────
# Mapper corrections to CreateVars teach the agency's role tables. We keep
# them in a SEPARATE file (not the hand-authored config) so the authored
# config stays pristine, and the learned set is auditable and resettable
# (delete the file to forget). The role tables we accept entries for:

_LEARNED_DIR = _AGENCY_OVERRIDES_DIR / "learned"
_LEARNED_TABLES = ("involvement_roles", "assignment_roles", "child_entities")


def _load_learned_tables(agency: str, learned_dir: Optional[Path] = None) -> dict:
    """Return the learned role tables for ``agency`` (``{}`` if none).

    Shape: ``{"involvement_roles": {...}, "child_entities": {...}}`` — the
    same inner structure as the authored ``[agency.<table>.<key>]`` blocks.
    """
    d = learned_dir or _LEARNED_DIR
    p = d / f"{agency}.toml"
    if not p.exists():
        return {}
    try:
        data = _read_toml(p)
    except Exception:  # noqa: BLE001 — a corrupt learned file must never break load
        return {}
    return {k: data[k] for k in _LEARNED_TABLES if k in data}


def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(f'"{x}"' for x in v) + "]"
    return '"' + str(v).replace('"', '\\"') + '"'


def write_learned_tables(
    agency: str, tables: dict, learned_dir: Optional[Path] = None
) -> Path:
    """Write the learned role tables for ``agency`` to disk, replacing any
    existing learned file. ``tables`` mirrors ``_load_learned_tables``'s shape.
    """
    d = learned_dir or _LEARNED_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{agency}.toml"

    lines = [
        f"# Learned CreateVar entities for {agency!r}.",
        "# Auto-generated from mapper corrections — delete to reset learning.",
        "",
    ]
    for table_name in _LEARNED_TABLES:
        entries = tables.get(table_name) or {}
        for key, entry in entries.items():
            lines.append(f"[{table_name}.{key}]")
            for field_name, val in entry.items():
                lines.append(f"{field_name} = {_toml_scalar(val)}")
            lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def list_agencies(directory: Optional[Path] = None) -> List[dict]:
    """Enumerate the configured agencies for the picker UI.

    Scans ``agency_overrides/*.toml`` and returns ``[{"id", "description"}]``
    sorted by id. Reads each file's ``[agency]`` table directly (not the full
    ``AgencyRoot`` model) so one malformed config doesn't hide the others —
    a file we can't parse is simply skipped.
    """
    root = directory or _AGENCY_OVERRIDES_DIR
    out: List[dict] = []
    for p in sorted(root.glob("*.toml")):
        try:
            table = _read_toml(p).get("agency", {})
        except Exception:  # noqa: BLE001 — skip unreadable/invalid configs
            continue
        agency_id = table.get("id") or p.stem
        out.append({"id": agency_id, "description": table.get("description", "")})
    return out


def slugify_agency(name: str) -> str:
    """Derive a filesystem-safe agency slug from a display name.

    Lowercases, replaces runs of non-alphanumerics with a single hyphen,
    and trims leading/trailing hyphens. Matches the ``[a-z0-9_-]+`` rule
    the suggestion store enforces for on-disk agency directories.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug


def create_agency(name: str, directory: Optional[Path] = None) -> dict:
    """Create a new ``agency_overrides/<slug>.toml`` from a display name.

    Returns ``{"id", "description"}`` for the created agency. Raises
    ``ValueError`` if the name yields an empty/invalid slug, and
    ``FileExistsError`` if that agency already exists.
    """
    root = directory or _AGENCY_OVERRIDES_DIR
    description = (name or "").strip()
    slug = slugify_agency(description)
    if not slug:
        raise ValueError(f"agency name {name!r} produced an empty slug")

    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{slug}.toml"
    if path.exists():
        raise FileExistsError(f"agency {slug!r} already exists")

    # Minimal valid config — id + description. The relaxed AgencyRoot model
    # fills conventions/vocabulary with empty defaults; conversion runs
    # LLM-only until the agency's vocabulary is filled in later.
    esc = description.replace('"', '\\"')
    path.write_text(
        '[agency]\n'
        f'id          = "{slug}"\n'
        f'description = "{esc}"\n',
        encoding="utf-8",
    )
    return {"id": slug, "description": description}


def delete_agency(slug: str, directory: Optional[Path] = None) -> dict:
    """Delete an agency's override config and its learned-tables file.

    Returns ``{"ok": True, "id": slug}``. Raises ``ValueError`` for an empty
    slug and ``FileNotFoundError`` if the agency override doesn't exist. The
    suggestion store lives under a separate, configurable root and is the
    caller's responsibility (see ``tools/delete_agency.py``).
    """
    root = directory or _AGENCY_OVERRIDES_DIR
    slug = (slug or "").strip()
    if not slug:
        raise ValueError("empty agency slug")
    path = root / f"{slug}.toml"
    if not path.exists():
        raise FileNotFoundError(f"agency {slug!r} not found")
    path.unlink()
    (root / "learned" / f"{slug}.toml").unlink(missing_ok=True)
    return {"ok": True, "id": slug}


def rename_agency(
    old_slug: str, new_name: str, directory: Optional[Path] = None
) -> dict:
    """Rename an agency: derive a new slug from ``new_name``, migrate the
    override + learned config files, and rewrite the ``[agency]`` id and
    description in place (preserving the rest of the config).

    Returns ``{"id", "description", "old_id"}``. Raises ``ValueError`` for an
    empty source/derived slug, ``FileNotFoundError`` if the source is
    missing, and ``FileExistsError`` if the (different) target already exists.
    Suggestion-store migration is the caller's responsibility.
    """
    root = directory or _AGENCY_OVERRIDES_DIR
    old_slug = (old_slug or "").strip()
    description = (new_name or "").strip()
    new_slug = slugify_agency(description)
    if not old_slug:
        raise ValueError("empty source agency slug")
    if not new_slug:
        raise ValueError(f"agency name {new_name!r} produced an empty slug")
    src = root / f"{old_slug}.toml"
    if not src.exists():
        raise FileNotFoundError(f"agency {old_slug!r} not found")
    dst = root / f"{new_slug}.toml"
    if new_slug != old_slug and dst.exists():
        raise FileExistsError(f"agency {new_slug!r} already exists")

    # ``id`` and ``description`` appear once each, only in the ``[agency]``
    # table, so a targeted single substitution preserves all other config.
    # Replacement functions (not strings) avoid backslash re-interpretation
    # of the escaped description.
    esc = description.replace("\\", "\\\\").replace('"', '\\"')
    basic_str = r'"(?:\\.|[^"\\])*"'
    text = src.read_text(encoding="utf-8")
    text = re.sub(
        rf'(?m)^(\s*id\s*=\s*){basic_str}',
        lambda m: f'{m.group(1)}"{new_slug}"', text, count=1,
    )
    text = re.sub(
        rf'(?m)^(\s*description\s*=\s*){basic_str}',
        lambda m: f'{m.group(1)}"{esc}"', text, count=1,
    )

    dst.write_text(text, encoding="utf-8")
    if new_slug != old_slug:
        src.unlink()
        old_learned = root / "learned" / f"{old_slug}.toml"
        if old_learned.exists():
            old_learned.replace(root / "learned" / f"{new_slug}.toml")
    return {"id": new_slug, "description": description, "old_id": old_slug}
