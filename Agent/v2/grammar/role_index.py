"""Derive runtime lookup tables from an :class:`OrgRoot` config.

The org config (``v2/grammar/org_overrides/<org>.toml``) is the source
of truth for everything the agent needs to know about how a specific
deployment labels its entities and what type codes it uses inside
``CaseInvolvement`` / ``CaseAssignment`` queries.

This module turns that nested config into flat lookup dicts that
:mod:`v2.patterns.transforms` and :mod:`v2.engine.prelude` consume:

  * ``jda_to_pine`` — JDA prefix (``JW_Respondent``) → Pine name (``Respondent``)
  * ``involvement_pine_names`` — set of Pine names sourced from CaseInvolvement
  * ``assignment_pine_names`` — set of Pine names sourced from CaseAssignment
  * ``type_codes`` — Pine name → CIT type code
  * ``pre_declared`` — set of Pine names the org's variable screen
    has pre-declared (no inline CreateVar needed)
  * ``child_parent`` — Pine child name → Pine parent name
  * ``child_source`` — Pine child name → source table (NameAddress, etc.)

The index is computed once per :class:`OrgRoot` and cached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Optional, Set

from .loaders import OrgRoot


@dataclass(frozen=True)
class RoleIndex:
    """All the lookups needed at runtime, derived from one OrgRoot."""

    jda_to_pine: Dict[str, str] = field(default_factory=dict)
    involvement_pine_names: Set[str] = field(default_factory=set)
    assignment_pine_names: Set[str] = field(default_factory=set)
    type_codes: Dict[str, str] = field(default_factory=dict)
    pre_declared: Set[str] = field(default_factory=set)
    child_parent: Dict[str, str] = field(default_factory=dict)
    child_source: Dict[str, str] = field(default_factory=dict)

    def is_involvement(self, pine_name: str) -> bool:
        return pine_name in self.involvement_pine_names

    def is_assignment(self, pine_name: str) -> bool:
        return pine_name in self.assignment_pine_names

    def is_child(self, pine_name: str) -> bool:
        return pine_name in self.child_parent

    def parent_of(self, pine_name: str) -> Optional[str]:
        return self.child_parent.get(pine_name)

    def empty(self) -> bool:
        """True when the org config has no role definitions at all."""
        return not (
            self.involvement_pine_names
            or self.assignment_pine_names
            or self.child_parent
        )


# Cache by ``id(org_root)`` so repeated calls during eval reuse the
# same index. The OrgRoot objects are immutable in practice, so cache
# invalidation isn't a concern.
_index_cache: Dict[int, RoleIndex] = {}


def role_index_for(org_root: Optional[OrgRoot]) -> RoleIndex:
    """Build (or return cached) :class:`RoleIndex` for an org config.

    Passing ``None`` returns an empty index — callers can then fall
    back to their own hardcoded defaults.
    """
    if org_root is None:
        return RoleIndex()
    key = id(org_root)
    cached = _index_cache.get(key)
    if cached is not None:
        return cached
    index = _build_index(org_root)
    _index_cache[key] = index
    return index


def _build_index(org_root: OrgRoot) -> RoleIndex:
    jda_to_pine: Dict[str, str] = {}
    involvement: Set[str] = set()
    assignment: Set[str] = set()
    type_codes: Dict[str, str] = {}
    pre_declared: Set[str] = set()
    child_parent: Dict[str, str] = {}
    child_source: Dict[str, str] = {}

    for role in org_root.involvement_roles.values():
        involvement.add(role.pine_name)
        type_codes[role.pine_name] = role.type_code
        if role.pre_declared:
            pre_declared.add(role.pine_name)
        for alias in role.jda_aliases:
            jda_to_pine[alias] = role.pine_name

    for role in org_root.assignment_roles.values():
        assignment.add(role.pine_name)
        type_codes[role.pine_name] = role.type_code
        if role.pre_declared:
            pre_declared.add(role.pine_name)
        for alias in role.jda_aliases:
            jda_to_pine[alias] = role.pine_name

    for child in org_root.child_entities.values():
        child_parent[child.pine_name] = child.parent_pine_name
        child_source[child.pine_name] = child.source_table
        if child.pre_declared:
            pre_declared.add(child.pine_name)
        for alias in child.jda_aliases:
            jda_to_pine[alias] = child.pine_name

    return RoleIndex(
        jda_to_pine=jda_to_pine,
        involvement_pine_names=involvement,
        assignment_pine_names=assignment,
        type_codes=type_codes,
        pre_declared=pre_declared,
        child_parent=child_parent,
        child_source=child_source,
    )
