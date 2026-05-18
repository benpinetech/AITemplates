"""Shared pytest fixtures for the v2 test suite.

Adds the v2 package to sys.path so tests can import ``parser.jda_parser``
etc. without needing the repo to be installed as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Insert the parent of v2/ on the path so ``from pipeline.parser import ...``
# works. We do this here once for the whole suite; individual tests
# import normally.
V2_DIR = Path(__file__).resolve().parent.parent
AGENT_DIR = V2_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


import pytest


@pytest.fixture(scope="session")
def corpus_legacy_dir() -> Path:
    """Path to the legacy RTF corpus.

    Tests that walk the corpus depend on this layout:
    ``Agent/ground_truth/evaluation_templates/jda_to_pine/legacy/*.rtf``.
    """
    return AGENT_DIR / "ground_truth" / "evaluation_templates" / "jda_to_pine" / "legacy"


@pytest.fixture(scope="session")
def corpus_pine_dir() -> Path:
    """Path to the verified Pine RTF corpus."""
    return AGENT_DIR / "ground_truth" / "evaluation_templates" / "jda_to_pine" / "pine"
