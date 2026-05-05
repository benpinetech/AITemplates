"""Corpus regression test.

Runs both parsers across every RTF in
``Agent/ground_truth/evaluation_templates/jda_to_pine/{legacy,pine}/``
and asserts a minimum round-trip rate. The thresholds below are set
just slightly under the current measured rates so that a regression
fails the test, but day-to-day variance (e.g. one new edge-case
expression appearing in the corpus) doesn't.

If you intentionally improve the parser, raise the threshold. If a
regression breaks this test, run ``tools/corpus_round_trip.py`` to see
the failing expressions and decide whether to fix the parser or update
the threshold.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from v2.parser import jda_parser, pine_parser, rtf_extractor


# Threshold expressed as a fraction of expressions that must round-trip
# (parse → unparse → parse → structural equal). The current measured
# rate is 99.95%; we set the gate at 99.5% to leave room for noise.
MIN_ROUND_TRIP_RATE = 0.995


def _round_trip_rate(rtf_paths, parser_fn, bracket: str) -> tuple[int, int]:
    total = 0
    success = 0
    for path in rtf_paths:
        rtf = path.read_text(encoding="utf-8", errors="replace")
        for hit in rtf_extractor.extract(rtf, bracket=bracket, parse=True):
            total += 1
            if hit.error is not None or hit.ast is None:
                continue
            try:
                ast2 = parser_fn(hit.ast.unparse())
            except Exception:  # noqa: BLE001
                continue
            if ast2.inner == hit.ast.inner:
                success += 1
    return success, total


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent.parent
         / "ground_truth/evaluation_templates/jda_to_pine/legacy").exists(),
    reason="corpus directory not present",
)
def test_legacy_corpus_round_trip(corpus_legacy_dir):
    files = sorted(corpus_legacy_dir.glob("*.rtf"))
    assert files, f"no RTF files in {corpus_legacy_dir}"
    success, total = _round_trip_rate(files, jda_parser.parse, "%[")
    rate = success / total if total else 0.0
    assert rate >= MIN_ROUND_TRIP_RATE, (
        f"JDA corpus round-trip rate dropped: {success}/{total} "
        f"({rate:.4%}); threshold {MIN_ROUND_TRIP_RATE:.4%}. "
        f"Run tools/corpus_round_trip.py to see failures."
    )


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent.parent
         / "ground_truth/evaluation_templates/jda_to_pine/pine").exists(),
    reason="corpus directory not present",
)
def test_pine_corpus_round_trip(corpus_pine_dir):
    files = sorted(corpus_pine_dir.glob("*.rtf"))
    assert files, f"no RTF files in {corpus_pine_dir}"
    success, total = _round_trip_rate(files, pine_parser.parse, "@[")
    rate = success / total if total else 0.0
    assert rate >= MIN_ROUND_TRIP_RATE, (
        f"Pine corpus round-trip rate dropped: {success}/{total} "
        f"({rate:.4%}); threshold {MIN_ROUND_TRIP_RATE:.4%}. "
        f"Run tools/corpus_round_trip.py to see failures."
    )
