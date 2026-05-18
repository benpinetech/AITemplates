"""Document-audience classification.

Templates fall into three rough audience classes that determine which
Pine entity ambiguous references should bind to:

    - ``"complainant"`` — letter or document addressed to the complainant
    - ``"respondent"`` — letter or document addressed to the respondent
    - ``None`` — mixed / unclassifiable

Two signal sources are combined: the template filename (when available)
and the dominant Pine entity in the JDA tokens.

This module exists so the LLM fallback and the suggestion-store loader
agree on the same audience for a given template — without it, an
audience-scoped suggestion saved during one run wouldn't necessarily be
re-loaded by the next run.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from ..parser.jda_ast import JdaToken


_FILENAME_TO_C_RE = re.compile(
    r"(?:^|[\s_\-])"
    r"(?:to\s*C|C\s+Offer|C\s+ARC|C\s+Notice|"
    r"Letter\s+to\s+C|Process\s+Ltr\s+C|Process\s+Complainant|"
    r"Letter\s+to\s+Complainant)"
    r"(?:[\s_\-.]|$)",
    re.IGNORECASE,
)
_FILENAME_TO_R_RE = re.compile(
    r"(?:^|[\s_\-])"
    r"(?:to\s*R|R\s+Offer|R\s+ARC|R\s+Notice|"
    r"Letter\s+to\s+R|Process\s+Ltr\s+R|Process\s+R|"
    r"Letter\s+to\s+Respondent|Letter\s+to\s+Disbarred)"
    r"(?:[\s_\-.]|$)",
    re.IGNORECASE,
)


_INVOLVEMENT_COMPLAINANT_PINE = frozenset({"Complainant", "FilingComplainant"})
_INVOLVEMENT_RESPONDENT_PINE = frozenset({"Respondent", "PrimaryInvolvement"})


def _leading_entity(jda_token: JdaToken) -> Optional[str]:
    """Extract the leading bare-identifier from a JDA token's source."""
    text = jda_token.unparse()
    m = re.search(r"%\[\s*(?:[A-Za-z]+\(\s*)?([A-Za-z_][A-Za-z0-9_]*)", text)
    return m.group(1) if m else None


def classify_document_audience(
    template_name: Optional[str],
    jda_tokens: Sequence[JdaToken],
    jda_to_pine: dict,
) -> Optional[str]:
    """Best-effort classification of who the document is addressed to.

    Filename signal first (most reliable when templates follow naming
    conventions); token-frequency fallback returns the dominant
    involvement entity if it accounts for ≥60% of recognized involvement
    references.

    Returns ``"complainant"`` / ``"respondent"`` / None.
    """
    if template_name:
        # "Letter to Disbarred" matches both regexes — but the disbarred
        # attorney IS the respondent in OBA discipline, so the R-check
        # wins. Check R before C.
        if _FILENAME_TO_R_RE.search(template_name):
            return "respondent"
        if _FILENAME_TO_C_RE.search(template_name):
            return "complainant"

    c_count = r_count = 0
    for tok in jda_tokens:
        leading = _leading_entity(tok)
        if leading is None:
            continue
        pine = jda_to_pine.get(leading)
        if pine is None:
            continue
        for c_name in _INVOLVEMENT_COMPLAINANT_PINE:
            if pine == c_name or pine == c_name + "Address":
                c_count += 1
                break
        for r_name in _INVOLVEMENT_RESPONDENT_PINE:
            if pine == r_name or pine == r_name + "Address":
                r_count += 1
                break
    total = c_count + r_count
    if total < 3:
        return None
    if c_count / total >= 0.6:
        return "complainant"
    if r_count / total >= 0.6:
        return "respondent"
    return None
