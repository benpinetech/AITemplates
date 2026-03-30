import re
import json
from pathlib import Path
from state import mapping


def extract_pine_fillpoints(rtf_content: str) -> list[str]:
    """Extract @[...] tokens from a Pine RTF template, preserving duplicates.

    Uses the same balanced-bracket approach as the legacy extractor to handle
    nested brackets (e.g. @[FormatDate(preset1)]).
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
        ground_truth_tokens = extract_pine_fillpoints(f.read())

    # Deduplicate ground truth (order doesn't matter for set comparison)
    expected = {_normalize(t) for t in ground_truth_tokens}

    # Collect all @[...] tokens from the agent's mapped Pine values
    agent_tokens: set[str] = set()
    for m in agent_mappings:
        pine_val = m.pine
        if not pine_val or "no mapping found" in pine_val.lower():
            continue
        # A single mapping value might contain multiple @[...] tokens
        found = re.findall(r"@\[[^\]]+\]", pine_val)
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
