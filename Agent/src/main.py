import asyncio
import json
from pathlib import Path

from agent import Agent
from utils import build_vector_store, load_vector_store
from eval import evaluate_mappings, print_eval_report, print_batch_summary, save_eval_results, save_run
from dotenv import load_dotenv
import os
import argparse
import time

AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
CHROMA_DIR = AGENT_DIR / "chroma_db"
GROUND_TRUTH_DIR = AGENT_DIR / "ground_truth"
EXTRACTOR_DIR = REPO_ROOT / "phase1-jda-to-pine-extractor"
TEMPLATE_OUTPUT_DIR = AGENT_DIR / "template_output"
EVAL_OUTPUT_DIR = AGENT_DIR / "evaluation_output"
EVAL_RUNS_DIR = AGENT_DIR / "eval_runs"

load_dotenv(REPO_ROOT / ".env")

parser = argparse.ArgumentParser()
parser.add_argument("File", nargs="?", default=None, help="Path to a legacy RTF file, or a folder of RTF files when using -b.")
parser.add_argument('-e', '--evaluate', action='store_true', help="Run the agent in evaluation mode (no File required)")
parser.add_argument('-b', '--batch', action='store_true', help="Run the agent in batch mode on all .rtf files in the provided folder")
parser.add_argument('--legacy-dir', type=str, default=None, help="Path to the folder of legacy RTF templates (required for evaluation mode)")
parser.add_argument('--pine-dir', type=str, default=None, help="Path to the folder of Pine RTF templates (required for evaluation mode)")
parser.add_argument('--label', type=str, default="", help="Optional label for this evaluation run")
parser.add_argument('--single-eval', action='store_true', help="Run a one-off eval on a single legacy/pine file pair (not saved to runs)")
parser.add_argument('--legacy-file', type=str, default=None, help="Single legacy RTF file path (for --single-eval)")
parser.add_argument('--pine-file', type=str, default=None, help="Single Pine reference RTF file path (for --single-eval)")
parser.add_argument('--output-json', type=str, default=None, help="Write result JSON to this path (used by GUI for one-off evals)")

def main():
    # Parse the command line arguments
    args = parser.parse_args()

    if not args.evaluate and not args.single_eval and not args.File:
        parser.error("File is required unless running in evaluation mode (-e) or single-eval mode (--single-eval)")

    if args.evaluate:
        if not args.legacy_dir or not args.pine_dir:
            parser.error("Evaluation mode (-e) requires --legacy-dir and --pine-dir")

    if args.single_eval:
        if not args.legacy_file or not args.pine_file:
            parser.error("--single-eval requires --legacy-file and --pine-file")

    if args.batch:
        batch_dir = Path(args.File)
        if not batch_dir.is_dir():
            parser.error(f"Batch mode (-b) requires a folder path, but '{args.File}' is not a directory.")
    
    # Check if vector store exists, if not build it BEFORE initializing the agent
    # (Agent constructor calls load_vector_store, which would create an empty DB)
    if not CHROMA_DIR.exists():
        print("Building vector store.")
        build_vector_store(
            ground_truth_path=str(GROUND_TRUTH_DIR / "pine_syntax_ground_truth.txt"),
            persist_directory=str(CHROMA_DIR),
        )
    else:
        print("Vector store already exists.")

    # initialize the agent
    agent = Agent()

    if args.evaluate:
        print("Running in evaluation mode.")
        legacy_dir = Path(args.legacy_dir)
        pine_dir = Path(args.pine_dir)
        all_results = []

        for legacy_file in sorted(legacy_dir.glob("*.rtf")):
            pine_file = pine_dir / legacy_file.name
            if not pine_file.exists():
                print(f"Skipping {legacy_file.name} — no matching Pine template")
                continue

            print(f"\nEvaluating {legacy_file.name}")
            with open(legacy_file, "r") as f:
                legacy_template = f.read()

            start = time.time()
            final_state = asyncio.run(agent.run(legacy_template=legacy_template))
            duration = time.time() - start

            # Save the generated output
            EVAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = EVAL_OUTPUT_DIR / legacy_file.name
            with open(output_path, "w") as f:
                f.write(final_state["generated_pine_template"])

            # Read ground truth content for the dashboard
            with open(pine_file, "r") as f:
                ground_truth_content = f.read()

            # Score against human-verified Pine template
            result = evaluate_mappings(final_state["mapped_pine_info"], pine_file)
            print_eval_report(legacy_file.name, result, duration)

            all_results.append({
                "template": legacy_file.name,
                "duration": duration,
                "result": result,
                "legacy_content": legacy_template,
                "generated_content": final_state["generated_pine_template"],
                "ground_truth_content": ground_truth_content,
            })

        print_batch_summary(all_results)
        save_eval_results(all_results, EVAL_OUTPUT_DIR / "eval_results.json")
        save_run(all_results, legacy_dir, pine_dir, EVAL_RUNS_DIR, args.label)

    elif args.single_eval:
        print("Running one-off single evaluation (not saved to runs).")
        legacy_file = Path(args.legacy_file)
        pine_file = Path(args.pine_file)

        with open(legacy_file, "r") as f:
            legacy_template = f.read()
        with open(pine_file, "r") as f:
            ground_truth_content = f.read()

        print(f"Evaluating {legacy_file.name}")
        start = time.time()
        final_state = asyncio.run(agent.run(legacy_template=legacy_template))
        duration = time.time() - start

        result = evaluate_mappings(final_state["mapped_pine_info"], pine_file)
        print_eval_report(legacy_file.name, result, duration)

        if args.output_json:
            recall = result["recall"]
            precision = result["precision"]
            f1 = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0
            output = {
                "name": legacy_file.name,
                "duration": duration,
                "recall": recall,
                "precision": precision,
                "f1": f1,
                "correct_count": result["correct_count"],
                "missing_count": result["missing_count"],
                "extra_count": result["extra_count"],
                "total_expected": result["total_expected"],
                "total_agent": result["total_agent"],
                "correct": result["correct"],
                "missing": result["missing"],
                "extra": result["extra"],
                "legacy_content": legacy_template,
                "generated_content": final_state["generated_pine_template"],
                "ground_truth_content": ground_truth_content,
            }
            with open(args.output_json, "w") as f:
                json.dump(output, f, indent=2)
            print(f"Result written to {args.output_json}")

    elif args.batch:
        print("Running in batch mode.")
        batch_dir = Path(args.File)
        for legacy_file in sorted(batch_dir.glob("*.rtf")):
            print(f"\nProcessing {legacy_file.name}")
            with open(legacy_file, "r") as f:
                legacy_template = f.read()

            start = time.time()
            final_state = asyncio.run(agent.run(legacy_template=legacy_template))
            duration = time.time() - start

            # Save the generated output
            TEMPLATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = TEMPLATE_OUTPUT_DIR / legacy_file.name
            with open(output_path, "w") as f:
                f.write(final_state["generated_pine_template"])

            print(f"Processed {legacy_file.name} in {duration:.1f} seconds. Output saved to {output_path}")

    else:
        print("Running in single mode.")
        template_file_path = Path(args.File)

        if not template_file_path.exists():
            print(f"Error: File {template_file_path} does not exist.")
            return
        
        with open(template_file_path, "r") as f:
            legacy_template = f.read()

        start = time.time()
        final_state = asyncio.run(agent.run(legacy_template=legacy_template))
        end = time.time()

        # Save the generated pine template to the output directory
        TEMPLATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = TEMPLATE_OUTPUT_DIR / template_file_path.name
        with open(output_path, "w") as f:
            f.write(final_state["generated_pine_template"])

        print(f"Agent run complete.")
        print(f"Output saved to {output_path}")
        print("Agent execution time:", end - start, "seconds")



if __name__ == "__main__":
    main()
