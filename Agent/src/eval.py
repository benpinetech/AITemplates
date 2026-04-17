import re
import json
from datetime import datetime
from pathlib import Path
from state import mapping


def extract_pine_fillpoints(rtf_content: str, exclude_createvar: bool = False) -> list[str]:
    """Extract @[...] tokens from a Pine RTF template, preserving duplicates.

    Uses the same balanced-bracket approach as the legacy extractor to handle
    nested brackets (e.g. @[FormatDate(preset1)]).

    Args:
        rtf_content: The RTF content to extract tokens from.
        exclude_createvar: If True, skip @[CreateVar(...)] tokens and all tokens
                          nested inside them. These are structural declarations
                          that the mapping pipeline cannot produce.
    """
    tokens: list[str] = []
    i = 0
    while i < len(rtf_content):
        start = rtf_content.find("@[", i)
        if start < 0:
            break

        depth = 1
        pos = start + 2

        while pos < len(rtf_content) and depth > 0:
            c = rtf_content[pos]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            pos += 1

        if depth != 0:
            i = start + 1
            continue

        raw = rtf_content[start:pos]

        # Strip RTF control codes that may be embedded inside the token
        cleaned = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", raw)   # control words
        cleaned = re.sub(r"\\'\w{2}", "", cleaned)            # hex escapes
        cleaned = re.sub(r"[{}]", "", cleaned)                 # RTF braces
        cleaned = re.sub(r"\s+", " ", cleaned).strip()         # collapse whitespace

        if cleaned.startswith("@[") and cleaned.endswith("]") and len(cleaned) > 3:
            inner = cleaned[2:-1].strip()
            cleaned = "@[" + inner + "]"

            # Skip CreateVar tokens — they are structural declarations that the
            # mapping agent cannot produce (no legacy %[] token maps to them).
            if exclude_createvar and inner.lower().startswith("createvar("):
                i = pos
                continue

            tokens.append(cleaned)

        i = pos

    return tokens


def _normalize(token: str) -> str:
    """Lowercase + strip whitespace for comparison."""
    return token.strip().lower().replace(" ", "")


def evaluate_mappings(
    agent_mappings: list[mapping],
    pine_ground_truth_path: Path,
) -> dict:
    """Compare agent-produced mappings against the human-verified Pine template.

    Extracts unique @[...] tokens from the ground-truth Pine RTF and compares
    them to the Pine tokens the agent produced.

    Returns a dict with recall, precision, and detailed correct/missing/extra lists.
    """
    with open(pine_ground_truth_path, "r") as f:
        ground_truth_tokens = extract_pine_fillpoints(f.read(), exclude_createvar=True)

    # Deduplicate ground truth (order doesn't matter for set comparison)
    expected = {_normalize(t) for t in ground_truth_tokens}

    # Collect all @[...] tokens from the agent's mapped Pine values
    # Use the same balanced-bracket parser as ground truth extraction
    # so nested tokens like @[If(@[X.Any()] == true)] are handled correctly.
    agent_tokens: set[str] = set()
    for m in agent_mappings:
        pine_val = m.pine
        if not pine_val or "no mapping found" in pine_val.lower():
            continue
        # Use balanced-bracket extraction to handle nested @[...] tokens
        found = extract_pine_fillpoints(pine_val)
        if found:
            agent_tokens.update(_normalize(t) for t in found)
        else:
            # The agent may have returned bare Pine syntax without @[] wrapper
            agent_tokens.add(_normalize("@[" + pine_val + "]"))

    correct = expected & agent_tokens
    missing = expected - agent_tokens
    extra = agent_tokens - expected

    total_expected = len(expected)
    total_agent = len(agent_tokens)

    return {
        "recall": len(correct) / total_expected if total_expected else 0,
        "precision": len(correct) / total_agent if total_agent else 0,
        "correct_count": len(correct),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "total_expected": total_expected,
        "total_agent": total_agent,
        "correct": sorted(correct),
        "missing": sorted(missing),
        "extra": sorted(extra),
    }


def print_eval_report(template_name: str, result: dict, duration: float):
    """Print a concise evaluation report for a single template."""
    recall = result["recall"]
    precision = result["precision"]
    f1 = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0

    print(f"\n{'=' * 60}")
    print(f"  {template_name}")
    print(f"{'=' * 60}")
    print(f"  Recall:    {recall:.1%}  ({result['correct_count']}/{result['total_expected']})")
    print(f"  Precision: {precision:.1%}  ({result['correct_count']}/{result['total_agent']})")
    print(f"  F1 Score:  {f1:.1%}")
    print(f"  Time:      {duration:.1f}s")

    if result["missing"]:
        print(f"\n  Missing ({result['missing_count']}):")
        for t in result["missing"]:
            print(f"    - {t}")

    if result["extra"]:
        print(f"\n  Extra ({result['extra_count']}):")
        for t in result["extra"]:
            print(f"    - {t}")

    print()


def print_batch_summary(all_results: list[dict]):
    """Print an aggregate summary across all evaluated templates."""
    if not all_results:
        print("No templates evaluated.")
        return

    total_correct = sum(r["result"]["correct_count"] for r in all_results)
    total_expected = sum(r["result"]["total_expected"] for r in all_results)
    total_agent = sum(r["result"]["total_agent"] for r in all_results)
    total_time = sum(r["duration"] for r in all_results)

    macro_recall = sum(r["result"]["recall"] for r in all_results) / len(all_results)
    macro_precision = sum(r["result"]["precision"] for r in all_results) / len(all_results)
    macro_f1 = (2 * macro_recall * macro_precision / (macro_recall + macro_precision)) if (macro_recall + macro_precision) else 0

    micro_recall = total_correct / total_expected if total_expected else 0
    micro_precision = total_correct / total_agent if total_agent else 0
    micro_f1 = (2 * micro_recall * micro_precision / (micro_recall + micro_precision)) if (micro_recall + micro_precision) else 0

    print(f"\n{'#' * 60}")
    print(f"  BATCH EVALUATION SUMMARY  ({len(all_results)} templates)")
    print(f"{'#' * 60}")
    print(f"  Micro Recall:    {micro_recall:.1%}  ({total_correct}/{total_expected})")
    print(f"  Micro Precision: {micro_precision:.1%}  ({total_correct}/{total_agent})")
    print(f"  Micro F1:        {micro_f1:.1%}")
    print(f"  Macro Recall:    {macro_recall:.1%}")
    print(f"  Macro Precision: {macro_precision:.1%}")
    print(f"  Macro F1:        {macro_f1:.1%}")
    print(f"  Total time:      {total_time:.1f}s")
    print()


def save_eval_results(all_results: list[dict], output_path: Path):
    """Save detailed evaluation results to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Detailed results saved to {output_path}")


def compute_run_summary(all_results: list[dict]) -> dict:
    """Compute micro and macro aggregate stats for a batch of results."""
    if not all_results:
        return {}

    total_correct = sum(r["result"]["correct_count"] for r in all_results)
    total_expected = sum(r["result"]["total_expected"] for r in all_results)
    total_agent = sum(r["result"]["total_agent"] for r in all_results)
    total_time = sum(r["duration"] for r in all_results)

    macro_recall = sum(r["result"]["recall"] for r in all_results) / len(all_results)
    macro_precision = sum(r["result"]["precision"] for r in all_results) / len(all_results)
    macro_f1 = (2 * macro_recall * macro_precision / (macro_recall + macro_precision)) if (macro_recall + macro_precision) else 0.0

    micro_recall = total_correct / total_expected if total_expected else 0.0
    micro_precision = total_correct / total_agent if total_agent else 0.0
    micro_f1 = (2 * micro_recall * micro_precision / (micro_recall + micro_precision)) if (micro_recall + micro_precision) else 0.0

    return {
        "micro_recall": micro_recall,
        "micro_precision": micro_precision,
        "micro_f1": micro_f1,
        "macro_recall": macro_recall,
        "macro_precision": macro_precision,
        "macro_f1": macro_f1,
        "total_templates": len(all_results),
        "total_duration": total_time,
    }


def save_run(
    all_results: list[dict],
    legacy_dir: Path,
    pine_dir: Path,
    runs_dir: Path,
    label: str = "",
) -> Path:
    """Save a complete evaluation run to a timestamped JSON file in runs_dir.

    Expects each entry in all_results to have:
        template, duration, result (from evaluate_mappings),
        and optionally: legacy_content, generated_content, ground_truth_content.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_id = f"{timestamp}_{label}" if label else timestamp

    templates = []
    for r in all_results:
        res = r["result"]
        recall = res["recall"]
        precision = res["precision"]
        f1 = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0
        templates.append({
            "name": r["template"],
            "duration": r["duration"],
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "correct_count": res["correct_count"],
            "missing_count": res["missing_count"],
            "extra_count": res["extra_count"],
            "total_expected": res["total_expected"],
            "total_agent": res["total_agent"],
            "correct": res["correct"],
            "missing": res["missing"],
            "extra": res["extra"],
            "legacy_content": r.get("legacy_content", ""),
            "generated_content": r.get("generated_content", ""),
            "ground_truth_content": r.get("ground_truth_content", ""),
        })

    run_data = {
        "run_id": run_id,
        "label": label,
        "timestamp": datetime.now().isoformat(),
        "legacy_dir": str(legacy_dir),
        "pine_dir": str(pine_dir),
        "summary": compute_run_summary(all_results),
        "templates": templates,
    }

    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_path = runs_dir / f"{run_id}.json"
    with open(output_path, "w") as f:
        json.dump(run_data, f, indent=2)
    print(f"Run saved to {output_path}")
    return output_path
