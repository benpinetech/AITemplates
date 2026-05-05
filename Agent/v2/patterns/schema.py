"""Pydantic models for the pattern TOML files.

A pattern file looks like::

    [[pattern]]
    id = "..."
    description = "..."
    provenance = "hand-written"
    org_context = "oba"
    match = "%[TitleCase($entity.FullName)]"
    rewrite = "@[$entity_pine.first.FormatName(F L).SetCasing(Title)]"

    [pattern.holes.entity]
    kind = "path-segment"

    [pattern.holes.entity_pine]
    derive_from = "entity"
    transform = "translate_jda_entity_to_pine"

The Pydantic schema below mirrors that wire format. The loader
(`loader.py`) uses these models to parse and validate every TOML in
``library/**/*.toml``.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# Allowed values for hole.kind. Keep this in sync with patterns/README.md.
HoleKind = Literal[
    "path-segment",   # captures a single identifier string (within a path)
    "path",           # captures a JdaPath AST node
    "expression",     # captures any AST node
    "literal",        # captures any JdaLiteral node
    "bool",           # captures a bool literal
    "format-string",  # captures a LIT_FORMAT literal
    "subdoc-path",    # captures a LIT_PATH literal (Subdocument arg)
    "token",          # chunk-pattern only: captures one whole JdaToken from the stream
]


Provenance = Literal["hand-written", "mined", "llm-generated"]
Verification = Literal["verified", "candidate", "deprecated"]


class Hole(BaseModel):
    """One hole declaration inside a pattern.

    A hole either *captures* a value during matching (the source side)
    or *substitutes* a value during rewriting (the rewrite side). Holes
    that appear only in the rewrite must be derived (``derive_from`` set).
    """

    kind: HoleKind = "expression"
    derive_from: Optional[str] = None
    transform: Optional[str] = None
    description: str = ""

    @model_validator(mode="after")
    def _check_derived_has_transform(self):
        # If a hole is derived from another, it should have a transform
        # — otherwise it's just an alias and the pattern author probably
        # meant something else.
        if self.derive_from and not self.transform:
            raise ValueError(
                f"Hole declares derive_from={self.derive_from!r} but no "
                "transform; derived holes need a transform."
            )
        return self


class Pattern(BaseModel):
    """One pattern: a match/rewrite rule with metadata.

    See README.md §"Pattern file format" for an annotated example.
    """

    id: str
    description: str
    provenance: Provenance = "hand-written"
    verification: Verification = "verified"
    notes: str = ""
    org_context: str = "any"
    priority: int = 100

    # match: single string (one token) or list (chunk).
    match: Union[str, List[str]]
    # rewrite: single string (one Pine token), list (multi-output), or
    # absent if the pattern uses rewrite_function.
    rewrite: Optional[Union[str, List[str]]] = None
    rewrite_function: Optional[str] = None

    holes: Dict[str, Hole] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_one_rewrite_form(self):
        if self.rewrite is None and self.rewrite_function is None:
            raise ValueError(
                f"Pattern {self.id!r} must have either `rewrite` or "
                "`rewrite_function`."
            )
        if self.rewrite is not None and self.rewrite_function is not None:
            raise ValueError(
                f"Pattern {self.id!r} has both `rewrite` and "
                "`rewrite_function`; pick one."
            )
        return self

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in ("_", "-") for c in v):
            raise ValueError(
                f"Pattern id {v!r} must be a non-empty alphanumeric slug "
                "(letters, digits, _ , -)."
            )
        return v

    def match_tokens(self) -> List[str]:
        """Return ``match`` as a list of tokens regardless of whether it
        was authored as a single string or a list."""
        return [self.match] if isinstance(self.match, str) else list(self.match)

    def rewrite_tokens(self) -> Optional[List[str]]:
        """Return ``rewrite`` as a list of tokens, or None if this
        pattern uses ``rewrite_function``."""
        if self.rewrite is None:
            return None
        return [self.rewrite] if isinstance(self.rewrite, str) else list(self.rewrite)

    def is_chunk_pattern(self) -> bool:
        """True if `match` is multi-token. Chunk matching is Phase 2.5."""
        return isinstance(self.match, list) and len(self.match) > 1


class PatternFile(BaseModel):
    """A whole TOML file's contents."""

    pattern: List[Pattern] = Field(default_factory=list)
