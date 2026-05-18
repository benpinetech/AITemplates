"""v2 batch eval — same shape as v1's main.py -e, but routes the
conversion through ``v2.pipeline.convert_template`` and writes
results in the existing Eval Dashboard's run-file format so the
existing GUI can chart them alongside v1 runs.

Behavioural differences vs the v1 eval:

  - Optionally **auto-accepts every LLM suggestion** during the run.
    With ``--auto-accept``, each LLM-produced (jda → pine) pair is
    written to ``Agent/v2/suggestions/verified/<org>/`` immediately,
    so the very next template that hits the same JDA token converts
    deterministically — no further LLM call. A cold-run / warm-run
    timing delta tells you whether caching is paying off.
  - Records ``llm_seconds`` per template (time spent inside the
    fallback) so the dashboard can show LLM-cost trends.

Usage::

    ./venv/bin/python Agent/v2/tools/eval_v2.py --org oba
    ./venv/bin/python Agent/v2/tools/eval_v2.py --org oba --auto-accept --use-llm
    ./venv/bin/python Agent/v2/tools/eval_v2.py --org oba --label cold --no-auto-accept
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve()
AGENT_DIR = HERE.parent.parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

# Load OPENAI_API_KEY (and friends) from the repo-root .env so CLI
# runs work without requiring the user to export the key. Mirrors
# what the v1 main.py and the Streamlit pages already do.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(AGENT_DIR.parent / ".env")
except ImportError:
    pass

from pipeline import pipeline
from pipeline.engine import suggestion_store
from pipeline.engine.llm_converter import LlmConverter, OpenAILlmClient
from pipeline.grammar.loaders import load_org_overrides
from pipeline.parser import rtf_extractor
from pipeline.patterns import loader as pattern_loader

# Reuse the v1 eval's scoring + run-persistence helpers so the
# dashboard can display v2 runs without changing.
sys.path.insert(0, str(AGENT_DIR / "src"))
from eval import (   # type: ignore  (running scripts; circular path noise OK)
    evaluate_output,
    init_run_file,
    update_run_file,
)


CORPUS_ROOT = AGENT_DIR / "ground_truth" / "evaluation_templates" / "jda_to_pine"
LEGACY_DIR = CORPUS_ROOT / "legacy"
PINE_DIR = CORPUS_ROOT / "pine"
RUNS_DIR = AGENT_DIR / "eval_runs"


# ─────────────────────────────────────────────────────────────────────────────
# Auto-accept hook — wraps the pipeline's LLM fallback so every
# successful suggestion is written to the verified store immediately.
# ─────────────────────────────────────────────────────────────────────────────


class _AutoAcceptingConverter:
    """Decorator around ``LlmConverter`` that persists every successful
    conversion as a verified pattern. Behaves exactly like the wrapped
    fallback for callers; the side effect is the autocaching."""

    def __init__(self, inner: LlmConverter, org: str, source_template: str):
        self._inner = inner
        self._org = org
        self._source = source_template
        self.calls = 0
        self.seconds = 0.0
        self.accepted = 0

    def convert(self, jda_token):
        self.calls += 1
        t0 = time.perf_counter()
        out = self._inner.convert(jda_token)
        self.seconds += time.perf_counter() - t0
        if out is not None:
            try:
                suggestion_store.accept_suggestion(
                    jda_token.unparse(),
                    out.unparse(),
                    org=self._org,
                    source_template=self._source,
                )
                self.accepted += 1
            except Exception as e:  # noqa: BLE001
                print(f"warning: auto-accept failed for {jda_token.unparse()!r}: {e}",
                      file=sys.stderr)
        return out

    def convert_batch(self, jda_tokens, template_name=None):
        # One LLM call for the whole document. Each input slot can
        # yield 0+ Pine tokens; we only auto-cache slots that produced
        # exactly one Pine token (multi-token outputs like split
        # FullName don't fit the (jda, pine) cache shape cleanly).
        self.calls += 1
        t0 = time.perf_counter()
        results = self._inner.convert_batch(jda_tokens, template_name=template_name)
        self.seconds += time.perf_counter() - t0
        for tok, outs in zip(jda_tokens, results):
            if len(outs) != 1:
                continue
            try:
                suggestion_store.accept_suggestion(
                    tok.unparse(),
                    outs[0].unparse(),
                    org=self._org,
                    source_template=self._source,
                )
                self.accepted += 1
            except Exception as e:  # noqa: BLE001
                print(f"warning: auto-accept failed for {tok.unparse()!r}: {e}",
                      file=sys.stderr)
        return results

    # The pipeline calls only .convert() on the fallback; we don't need
    # to mirror the rest of the API. But a stub here keeps it tidy.
    def __getattr__(self, name):
        return getattr(self._inner, name)


# ─────────────────────────────────────────────────────────────────────────────
# Eval runner
# ─────────────────────────────────────────────────────────────────────────────


def run_eval(
    legacy_dir: Path,
    pine_dir: Path,
    org: str,
    runs_dir: Path,
    label: str,
    use_llm: bool,
    auto_accept: bool,
    api_key: Optional[str],
    template_filter: Optional[set] = None,
    on_template: Optional[callable] = None,
    reverse: bool = False,
    include_broken: bool = False,
) -> Path:
    """Run v2 against every paired template under ``legacy_dir``.

    Saves a JSON run file in the dashboard's expected format. Returns
    the path of that file. Caller can `tail -f` it during the run —
    each completed template re-writes the file atomically.
    """
    # Layer in verified LLM suggestions for this org. The pipeline
    # only auto-loads them when ``library`` is left None, but the eval
    # passes its library explicitly — so we have to merge here. Without
    # this, cached suggestions sit on disk but never reach conversion.
    library = pattern_loader.load_library().patterns
    if org != "any":
        library = library + suggestion_store.load_verified_for_org(org)
    org_overrides = load_org_overrides(org) if org != "any" else None

    # Build a base LLM client we reuse for every template (one OpenAI
    # client is fine; SDK is thread-safe enough for sequential calls).
    base_fb: Optional[LlmConverter] = None
    if use_llm:
        if not api_key:
            raise RuntimeError("--use-llm requires --api-key or OPENAI_API_KEY")
        client = OpenAILlmClient(api_key=api_key)
        base_fb = LlmConverter(client=client, library=library, org_overrides=org_overrides)

    legacy_files = sorted(legacy_dir.glob("*.rtf"), reverse=reverse)
    # Auto-skip "broken" templates: legacy with zero JDA tokens (RTF
    # mangling), or pine ground truth that's empty. They contribute
    # zero F1 and skew the macro average — but they're not measurable
    # agent quality. Always reported in the run file under
    # ``skipped_broken`` so it stays auditable.
    skipped_broken: List[dict] = []
    eligible_files: List[Path] = []
    for f in legacy_files:
        pine_file = pine_dir / f.name
        if not pine_file.exists():
            continue
        if template_filter is not None and f.name not in template_filter:
            continue
        try:
            leg_text = f.read_text(encoding="utf-8", errors="replace")
            pin_text = pine_file.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            skipped_broken.append({"name": f.name, "reason": f"read error: {e}"})
            continue
        n_jda = len(re.findall(r"%\[", leg_text))
        n_pine = len(re.findall(r"@\[", pin_text))
        is_broken = (n_jda == 0 and n_pine > 0) or (n_jda > 0 and n_pine == 0)
        if is_broken and not include_broken:
            reason = (
                f"legacy has 0 JDA tokens but pine has {n_pine} (RTF mangling)"
                if n_jda == 0
                else f"pine ground truth empty ({n_jda} JDA tokens unmatched)"
            )
            skipped_broken.append({"name": f.name, "reason": reason})
            continue
        eligible_files.append(f)
    planned = [f.name for f in eligible_files]

    run_path = init_run_file(legacy_dir, pine_dir, runs_dir,
                             label=label or f"v2-{org}",
                             planned_templates=planned)
    # Annotate the run file with the broken-template list up front so
    # it's visible even on early termination.
    if skipped_broken:
        try:
            run_data = json.loads(run_path.read_text())
            run_data["skipped_broken"] = skipped_broken
            run_path.write_text(json.dumps(run_data, indent=2))
        except Exception as e:  # noqa: BLE001
            print(f"warning: could not record skipped_broken to run file: {e}",
                  file=sys.stderr)

    if skipped_broken and on_template:
        # Surface skips in the live log so the user sees what the eval
        # decided to leave out.
        for s in skipped_broken:
            print(f"  SKIPPED (broken): {s['name']} — {s['reason']}", flush=True)

    all_results: List[dict] = []

    for legacy_file in eligible_files:
        pine_file = pine_dir / legacy_file.name
        if on_template:
            on_template(legacy_file.name)

        legacy_rtf = legacy_file.read_text(encoding="utf-8", errors="replace")
        ground_truth = pine_file.read_text(encoding="utf-8", errors="replace")

        fb_for_this = (
            _AutoAcceptingConverter(base_fb, org, legacy_file.name)
            if (auto_accept and base_fb is not None) else base_fb
        )

        # IMPORTANT: re-load the library on every template if auto-accept
        # is on, so newly verified suggestions become matchable
        # patterns in subsequent runs. Without this the cache would
        # only kick in on a fresh run.
        if auto_accept and base_fb is not None:
            # Re-load both the static patterns AND the verified
            # suggestions, since auto-accept may have written new ones.
            library_for_this = pattern_loader.load_library().patterns
            if org != "any":
                library_for_this = library_for_this + suggestion_store.load_verified_for_org(org)
        else:
            library_for_this = library

        t0 = time.perf_counter()
        result = pipeline.convert_template(
            legacy_rtf, org=org,
            library=library_for_this,
            org_overrides=org_overrides,
            converter=fb_for_this,
            template_name=legacy_file.name,
        )
        duration = time.perf_counter() - t0

        # Reuse v1's eval scoring (token-LCS-F1).
        eval_result = evaluate_output(result.converted_rtf, pine_file)

        # Extra v2 fields. The dashboard shows the v1 fields by default;
        # these extra keys are ignored unless someone displays them.
        prov = result.by_provenance
        eval_result["v2_pattern_segments"] = prov[pipeline.PROV_PATTERN]
        eval_result["v2_llm_segments"] = prov[pipeline.PROV_LLM]
        eval_result["v2_unmatched_segments"] = prov[pipeline.PROV_UNMATCHED]
        if isinstance(fb_for_this, _AutoAcceptingConverter):
            eval_result["v2_llm_calls"] = fb_for_this.calls
            eval_result["v2_llm_seconds"] = round(fb_for_this.seconds, 3)
            eval_result["v2_llm_accepted"] = fb_for_this.accepted

        all_results.append({
            "template": legacy_file.name,
            "duration": duration,
            "result": eval_result,
            "legacy_content": legacy_rtf,
            "generated_content": result.converted_rtf,
            "ground_truth_content": ground_truth,
        })
        update_run_file(run_path, all_results, status="in_progress")

    update_run_file(run_path, all_results, status="complete")
    return run_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--legacy-dir", type=Path, default=LEGACY_DIR)
    p.add_argument("--pine-dir", type=Path, default=PINE_DIR)
    p.add_argument("--org", default="oba")
    p.add_argument("--label", default="")
    p.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    p.add_argument("--templates", nargs="+", default=None,
                   help="restrict to these template filenames")
    p.add_argument("--use-llm", action="store_true",
                   help="enable LLM fallback (needs --api-key or OPENAI_API_KEY)")
    p.add_argument("--api-key", default=None,
                   help="OpenAI API key; falls back to OPENAI_API_KEY env var")
    p.add_argument("--auto-accept", action="store_true",
                   help="persist every LLM suggestion to suggestions/verified/<org>/ "
                        "so subsequent templates that hit the same JDA token "
                        "convert deterministically")
    p.add_argument("--reverse", action="store_true",
                   help="iterate templates in reverse alphabetical order — "
                        "useful for diagnosing whether precision drops are "
                        "caused by template ordering or by template content")
    p.add_argument("--include-broken", action="store_true",
                   help="include broken templates (legacy with 0 JDA tokens "
                        "or pine with 0 Pine tokens). Default is to skip "
                        "them so they don't drag down the macro F1.")
    args = p.parse_args(argv)

    import os
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")
    if args.use_llm and not api_key:
        print("error: --use-llm requires --api-key or OPENAI_API_KEY env var", file=sys.stderr)
        return 2

    template_filter = set(args.templates) if args.templates else None
    run_path = run_eval(
        legacy_dir=args.legacy_dir,
        pine_dir=args.pine_dir,
        org=args.org,
        runs_dir=args.runs_dir,
        label=args.label,
        use_llm=args.use_llm,
        auto_accept=args.auto_accept,
        api_key=api_key,
        template_filter=template_filter,
        on_template=lambda name: print(f"  {name}", flush=True),
        reverse=args.reverse,
        include_broken=args.include_broken,
    )
    print(f"\nrun saved to {run_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
