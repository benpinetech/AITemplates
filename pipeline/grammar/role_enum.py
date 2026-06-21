"""Universal Pine role enumeration.

Pine has a fixed system-wide enumeration of involvement and assignment
type codes. This module captures that enum so the LLM prompt and the
agent's classification logic can refer to it. Codes here are the same
across deployments; what varies per agency is whether a deployment
**renames** a code in its Pine variable screen (e.g. OBA exposes
``ASSTDC`` as ``OBAAttorney``).

The enum lives in code rather than the agency config because it's not an
agency concern — it's the Pine system's own data model. Per-agency overrides
(renames, JDA aliases, pre-declared flags) go in
``v2/grammar/agency_overrides/<agency>.toml``.

Source: provided by domain converter. Update when the Pine system
adds or removes a role code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class RoleSpec:
    """One row of the Pine role enum.

    A type code is a specific row in CaseInvolvement / CaseAssignment.
    Pine variables can filter rows in two modes:
      - by ``Type`` — a single specific code (one row family)
      - by ``MasterCode`` — a group of codes (e.g. all "Law Enforcement"
        types in CaseAssignment)
    The MasterCode is the same value as our older "category" column
    — kept under both names for back-compat; ``master_code`` is the
    canonical attribute, ``category`` is an alias.
    """

    code: str           # Pine system code, used in CreateVar "Type":<code> filter
    display_name: str   # Descriptive label; deployments often shorten when naming the variable
    master_code: str    # Pine MasterCode for the row; used in CreateVar "MasterCode":<value> filter

    # Backwards-compat alias for the old field name.
    @property
    def category(self) -> str:
        return self.master_code


# Involvement table — sourced from ``CaseInvolvement.GetByQuery("Type":<code>)``.
INVOLVEMENT_ROLES: List[RoleSpec] = [
    RoleSpec("INMATE",      "Inmate",       "Person of Interest"),
    RoleSpec("AG",          "Agency",       "Person of Interest"),
    RoleSpec("NEW",         "New",          "Other"),
    RoleSpec("CLIENT",      "Client",       "Person of Interest"),
    RoleSpec("PRIMWIT",     "Witness",      "Person of Interest"),
    RoleSpec("CLAIMANT",    "Claimant",     "Person of Interest"),
    RoleSpec("COUNCIL",     "Council",      "Other"),
    RoleSpec("RESPONDENT",  "Respondent",   "Person of Interest"),
    RoleSpec("PETITIONER",  "Petitioner",   "Victim"),
    RoleSpec("COMPLAINANT", "Complainant",  "Victim"),
    RoleSpec("PLAINTIFF",   "Plaintiff",    "Person of Interest"),
    RoleSpec("WITNESS",     "Witness_Old",  "Witness"),
    RoleSpec("VICTIM",      "Victim",       "Victim"),
    RoleSpec("DEFENDANT",   "Defendant",    "Person of Interest"),
    RoleSpec("CODEFENDANT", "Co-Defendant", "Co-Defendant"),
    RoleSpec("PRIM",        "Primary",      "Person of Interest"),
]


# Assignment table — sourced from ``CaseAssignment.GetByQuery("Type":<code>)``.
# Note: ``COUNCIL`` also appears in the involvement table — same code,
# different parent table, distinct semantics.
ASSIGNMENT_ROLES: List[RoleSpec] = [
    RoleSpec("HEARING",             "Hearing Panel Attorney",     "Prosecution"),
    RoleSpec("SEASONAL",            "Seasonal Staff",              "Staff"),
    RoleSpec("RECREATION",          "Recreation Manager",          "Staff"),
    RoleSpec("WILDFIRE",            "Wildland Firefighter",        "Staff"),
    RoleSpec("CHIEFJUDGE",          "Chief Judge",                 "Judiciary"),
    RoleSpec("DEPPROS",             "Deputy Prosecutor",           "Prosecution"),
    RoleSpec("CITYATTY",            "City Attorney",               "Prosecution"),
    RoleSpec("CITYPROS",            "City Prosecutor",             "Prosecution"),
    RoleSpec("MEDIATOR",            "Mediator",                    "Staff"),
    RoleSpec("PLAINTIFFATTY",       "Plaintiff's Attorney",        "Prosecution"),
    RoleSpec("ASSTCITY",            "Assistant City Solicitor",    "Defense"),
    RoleSpec("INTAKESEC",           "Intake Officer",              "Law Enforcement"),
    RoleSpec("ASSTDC",              "Assistant Disciplinary Counsel", "Prosecution"),
    RoleSpec("DEPCHIEFDC",          "Deputy Chief Disciplinary Counsel", "Prosecution"),
    RoleSpec("CHIEFDC",             "Chief Disciplinary Counsel",  "Prosecution"),
    RoleSpec("ADMINAID1",           "Administrative Aide I",       "Staff"),
    RoleSpec("DIRECTORCC",          "Director of Community Corrections", "Staff"),
    RoleSpec("ADMINAID2",           "Administrative Aide II",      "Staff"),
    RoleSpec("PRGRMMANAGER",        "Program Manager",             "Staff"),
    RoleSpec("COMMCORRECTIONS",     "Community Corrections Coordinator", "Staff"),
    RoleSpec("MENTALHEALTH",        "Mental Health Program Manager", "Staff"),
    RoleSpec("CHIEFPROBATION",      "Chief Probation Officer",     "Staff"),
    RoleSpec("TECHSUPPORT",         "IT Director",                 "Staff"),
    RoleSpec("PROB",                "Probation Officer",           "Staff"),
    RoleSpec("VAC",                 "Victim Assistance Coordinator", "Staff"),
    RoleSpec("COUNCIL",             "Council",                     "Defense"),
    RoleSpec("PROBATIONOFFICER",    "Case Officer",                "Law Enforcement"),
    RoleSpec("ADVOCATE",            "Advocate",                    "Staff"),
    RoleSpec("DISTRICTATTY",        "District Attorney",           "Prosecution"),
    RoleSpec("LABTECH",             "Lab Technician",              "Staff"),
    RoleSpec("COURTREPORTER",       "Court Reporter",              "Judiciary"),
    RoleSpec("JUDGE",               "Municipal Judge",             "Judiciary"),
    RoleSpec("AGENT",               "Agent",                       "Law Enforcement"),
    RoleSpec("DETECTIVE",           "Detective",                   "Staff"),
    RoleSpec("INVESTIGATOR",        "Investigator",                "Staff"),
    RoleSpec("TROOPER",             "Trooper",                     "Law Enforcement"),
    RoleSpec("DEPUTY",              "Deputy",                      "Law Enforcement"),
    RoleSpec("OFFICER",             "Officer",                     "Law Enforcement"),
    RoleSpec("PARALEGAL",           "Paralegal",                   "Staff"),
    RoleSpec("STAFF",               "Staff",                       "Staff"),
    RoleSpec("DEFENSEATTORNEY",     "Defense Attorney",            "Defense"),
    RoleSpec("PROSECUTINGATTORNEY", "Prosecutor",                  "Prosecution"),
]


# ─── lookup helpers ───────────────────────────────────────────────────────


def involvement_by_code() -> Dict[str, RoleSpec]:
    return {r.code: r for r in INVOLVEMENT_ROLES}


def assignment_by_code() -> Dict[str, RoleSpec]:
    return {r.code: r for r in ASSIGNMENT_ROLES}


def involvement_by_display() -> Dict[str, RoleSpec]:
    return {r.display_name: r for r in INVOLVEMENT_ROLES}


def assignment_by_display() -> Dict[str, RoleSpec]:
    return {r.display_name: r for r in ASSIGNMENT_ROLES}


def classify_pine_name(pine_name: str) -> Optional[str]:
    """Return ``"involvement"`` / ``"assignment"`` / None for a Pine
    variable name. Matches against the default display name in the
    enum — agencies that rename roles need their agency config to provide
    the classification override.

    When a name appears in BOTH tables (e.g. ``Council``), prefers
    ``involvement`` since involvement is the more common case in
    document templates. Callers that need the unambiguous version
    should consult the role index instead.
    """
    inv = involvement_by_display()
    if pine_name in inv:
        return "involvement"
    asg = assignment_by_display()
    if pine_name in asg:
        return "assignment"
    return None


# ─── prompt rendering ─────────────────────────────────────────────────────


def render_for_prompt() -> str:
    """Render the enum as a compact prompt section.

    Two tables, grouped by MasterCode so the LLM can spot the right
    role visually (Prosecution roles together, Law Enforcement
    together, etc.). The MasterCode header is shown explicitly
    because it's also a valid CreateVar filter value:
    ``"MasterCode":"Law Enforcement"`` selects any LE-grouped row.
    """
    def render(roles: List[RoleSpec], header: str, source_table: str) -> str:
        lines = [
            f"{header} (filter by `\"Type\":\"<code>\"` for a specific role, "
            f"or by `\"MasterCode\":\"<group>\"` for any role in that group, "
            f"in a `@{source_table}.GetByQuery(...)` query):"
        ]
        # Group by MasterCode, preserve enum order within a group
        by_master: Dict[str, List[RoleSpec]] = {}
        order: List[str] = []
        for r in roles:
            if r.master_code not in by_master:
                by_master[r.master_code] = []
                order.append(r.master_code)
            by_master[r.master_code].append(r)
        for master in order:
            members = by_master[master]
            entries = ", ".join(f"{r.code} ({r.display_name})" for r in members)
            lines.append(f"  MasterCode \"{master}\": {entries}")
        return "\n".join(lines)

    inv = render(INVOLVEMENT_ROLES, "INVOLVEMENT TYPES", "CaseInvolvement")
    asg = render(ASSIGNMENT_ROLES, "ASSIGNMENT TYPES", "CaseAssignment")
    return inv + "\n\n" + asg
