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


def count_unreplaced_legacy(generated_content: str) -> int:
    """Count remaining %[...] tokens in the generated output.

    Any occurrence means the replacement step failed for that token.
    """
    return len(re.findall(r'%\[', generated_content))


def _sequence_f1(agent_seq: list[str], expected_seq: list[str]) -> tuple[float, float, float]:
    """Compute precision/recall/F1 using longest-common-subsequence matching.

    Unlike set F1, this penalises tokens that appear out of order or in the
    wrong conditional branch, while still tolerating duplicate tokens and
    minor ordering differences within a section.
    """
    if not expected_seq and not agent_seq:
        return 1.0, 1.0, 1.0
    if not expected_seq or not agent_seq:
        return 0.0, 0.0, 0.0

    # Build LCS length table
    m, n = len(expected_seq), len(agent_seq)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if expected_seq[i - 1] == agent_seq[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_len = dp[m][n]
    precision = lcs_len / n if n else 0.0
    recall    = lcs_len / m if m else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def evaluate_output(
    generated_content: str,
    pine_ground_truth_path: Path,
) -> dict:
    """Compare tokens extracted from the generated output against the ground truth.

    Uses LCS-based sequence F1 so that tokens in the wrong conditional branch
    (e.g. swapped If/Else content) are penalised, not just token presence.
    Also tracks unreplaced legacy %[ tokens as a hard quality signal.
    """
    with open(pine_ground_truth_path, "r") as f:
        expected_seq = [_normalize(t) for t in
                        extract_pine_fillpoints(f.read(), exclude_createvar=True)]

    agent_seq = [_normalize(t) for t in
                 extract_pine_fillpoints(generated_content, exclude_createvar=True)]

    precision, recall, f1 = _sequence_f1(agent_seq, expected_seq)

    # Set-level detail for the missing/extra report (still useful for debugging)
    expected_set = set(expected_seq)
    agent_set    = set(agent_seq)
    correct = expected_set & agent_set
    missing = expected_set - agent_set
    extra   = agent_set   - expected_set

    return {
        "recall":        recall,
        "precision":     precision,
        "f1":            f1,
        "correct_count": len(correct),
        "missing_count": len(missing),
        "extra_count":   len(extra),
        "total_expected": len(expected_seq),
        "total_agent":    len(agent_seq),
        "correct": sorted(correct),
        "missing": sorted(missing),
        "extra":   sorted(extra),
        "unreplaced_count": count_unreplaced_legacy(generated_content),
    }


def evaluate_mappings(
    agent_mappings: list[mapping],
    pine_ground_truth_path: Path,
) -> dict:
    """Compare agent-produced mappings against the human-verified Pine template.

    This measures whether the agent found the correct Pine tokens — but does NOT
    verify that replacements were applied correctly in the output. Use
    evaluate_output() for end-to-end accuracy.
    """
    with open(pine_ground_truth_path, "r") as f:
        ground_truth_tokens = extract_pine_fillpoints(f.read(), exclude_createvar=True)

    expected = {_normalize(t) for t in ground_truth_tokens}

    agent_tokens: set[str] = set()
    for m in agent_mappings:
        pine_val = m.pine
        if not pine_val or "no mapping found" in pine_val.lower():
            continue
        found = extract_pine_fillpoints(pine_val)
        if found:
            agent_tokens.update(_normalize(t) for t in found)
        else:
            agent_tokens.add(_normalize("@[" + pine_val + "]"))

    correct = expected & agent_tokens
    missing = expected - agent_tokens
    extra = agent_tokens - expected

    return {
        "recall": len(correct) / len(expected) if expected else 0,
        "precision": len(correct) / len(agent_tokens) if agent_tokens else 0,
        "correct_count": len(correct),
        "missing_count": len(missing),
        "extra_count": len(extra),
        "total_expected": len(expected),
        "total_agent": len(agent_tokens),
        "correct": sorted(correct),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "unreplaced_count": 0,
    }


def print_eval_report(template_name: str, result: dict, duration: float):
    """Print a concise evaluation report for a single template."""
    recall    = result["recall"]
    precision = result["precision"]
    f1        = result.get("f1") or ((2 * recall * precision / (recall + precision)) if (recall + precision) else 0)

    print(f"\n{'=' * 60}")
    print(f"  {template_name}")
    print(f"{'=' * 60}")
    print(f"  Recall:    {recall:.1%}  ({result['correct_count']}/{result['total_expected']})")
    print(f"  Precision: {precision:.1%}  ({result['correct_count']}/{result['total_agent']})")
    print(f"  F1 Score:  {f1:.1%}")
    print(f"  Time:      {duration:.1f}s")

    unreplaced = result.get("unreplaced_count", 0)
    if unreplaced:
        print(f"  ⚠ Unreplaced legacy tokens in output: {unreplaced}")

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


def init_run_file(
    legacy_dir: Path,
    pine_dir: Path,
    runs_dir: Path,
    label: str = "",
    planned_templates: list[str] | None = None,
) -> Path:
    """Create an empty run file at the start of a batch eval.

    Produces a file with status="in_progress" and an empty templates list so
    that if the eval crashes or is killed mid-run, a partial run record is
    still on disk and visible in the dashboard.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_id = f"{timestamp}_{label}" if label else timestamp

    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_path = runs_dir / f"{run_id}.json"

    run_data = {
        "run_id": run_id,
        "label": label,
        "status": "in_progress",
        "timestamp": datetime.now().isoformat(),
        "legacy_dir": str(legacy_dir),
        "pine_dir": str(pine_dir),
        "planned_templates": planned_templates or [],
        "summary": {
            "micro_recall": 0.0, "micro_precision": 0.0, "micro_f1": 0.0,
            "macro_recall": 0.0, "macro_precision": 0.0, "macro_f1": 0.0,
            "total_templates": 0, "total_duration": 0.0,
        },
        "templates": [],
    }
    with open(run_path, "w") as f:
        json.dump(run_data, f, indent=2)
    print(f"Run file initialized at {run_path}")
    return run_path


def _template_record(r: dict) -> dict:
    res = r["result"]
    recall    = res["recall"]
    precision = res["precision"]
    f1 = res.get("f1") or ((2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0)
    return {
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
    }


def update_run_file(
    run_path: Path,
    all_results: list[dict],
    status: str = "in_progress",
) -> None:
    """Rewrite the run file with the current accumulated results.

    Called after each template completes so that if the process dies, the
    on-disk record already contains everything finished so far. At the end
    of the run, pass status="complete".
    """
    with open(run_path, "r") as f:
        run_data = json.load(f)

    run_data["status"] = status
    run_data["templates"] = [_template_record(r) for r in all_results]
    run_data["summary"] = compute_run_summary(all_results)
    run_data["last_updated"] = datetime.now().isoformat()

    # Atomic write — write to temp file then rename, so a crash during the
    # write doesn't corrupt the existing file.
    tmp_path = run_path.with_suffix(run_path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(run_data, f, indent=2)
    tmp_path.replace(run_path)


def save_run(
    all_results: list[dict],
    legacy_dir: Path,
    pine_dir: Path,
    runs_dir: Path,
    label: str = "",
) -> Path:
    """One-shot save — convenience for non-incremental callers."""
    run_path = init_run_file(legacy_dir, pine_dir, runs_dir, label)
    update_run_file(run_path, all_results, status="complete")
    print(f"Run saved to {run_path}")
    return run_path
