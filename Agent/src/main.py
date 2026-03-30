import asyncio
from pathlib import Path

from agent import Agent
from utils import build_vector_store, load_vector_store
from eval import evaluate_mappings, print_eval_report, print_batch_summary, save_eval_results
from dotenv import load_dotenv
import os
import argparse
import time

AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
CHROMA_DIR = AGENT_DIR / "chroma_db"
EXTRACTOR_DIR = REPO_ROOT / "phase1-jda-to-pine-extractor"
TEMPLATE_OUTPUT_DIR = AGENT_DIR / "template_output"
EVAL_OUTPUT_DIR = AGENT_DIR / "evaluation_output"

EVAL_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "phase1-jda-to-pine-extractor"
LEGACY_DIR = EVAL_TEMPLATES_DIR / "input" / "legacy"
PINE_DIR = EVAL_TEMPLATES_DIR / "input" / "pine"

load_dotenv(REPO_ROOT / ".env")

parser = argparse.ArgumentParser()
parser.add_argument("File", help="Path to the legacy RTF template to be converted.")
parser.add_argument('-e', '--evaluate', action='store_true', help="Run the agent in evaluation mode")
parser.add_argument('-b', '--batch', action='store_true', help="Run the agent in batch mode on all templates in the provided directory")

def main():
    # Parse the command line arguments
    args = parser.parse_args()
    
    # initialize the agent
    agent = Agent()

    # Check if vector store exists, if not build it
    vector_store = None
    if not CHROMA_DIR.exists():
        print("Building vector store.")
        vector_store = build_vector_store(
            ground_truth_path=str(EXTRACTOR_DIR / "pine_syntax_ground_truth.txt"),
            persist_directory=str(CHROMA_DIR),
        )
    else:
        print("Vector store already exists.")
        vector_store = load_vector_store(persist_directory=str(CHROMA_DIR))

    if args.evaluate:
        print("Running in evaluation mode.")
        all_results = []

        for legacy_file in sorted(LEGACY_DIR.glob("*.rtf")):
            pine_file = PINE_DIR / legacy_file.name
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

            # Score against human-verified Pine template
            result = evaluate_mappings(final_state["mapped_pine_info"], pine_file)
            print_eval_report(legacy_file.name, result, duration)

            all_results.append({
                "template": legacy_file.name,
                "duration": duration,
                "result": result,
            })

        print_batch_summary(all_results)
        save_eval_results(all_results, EVAL_OUTPUT_DIR / "eval_results.json")

    else:
        print("Running in normal mode.")
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

    # # Load the templates for the agent run
    # with open(EXTRACTOR_DIR / "input" / "pine" / "3A.rtf", "r") as f:
    #     pine_template = f.read()


    # # Count how many unampped items there are in the results
    # mapped_info = final_state["mapped_pine_info"]
    # unmapped = 0
    # for m in mapped_info:
    #     if "no mapping found" in m.pine.lower():
    #         unmapped += 1

    # Save the generated pine template to the output directory
    # TEMPLATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # output_path = TEMPLATE_OUTPUT_DIR / template_file_path.name
    # with open(output_path, "w") as f:
    #     f.write(final_state["generated_pine_template"])
    
    # print(f"Agent run complete.")
    # print(f"Number of mapped items: {len(mapped_info) - unmapped}")
    # print(f"Number of unmapped items: {unmapped}")
    
    print(f"all mapped variabled, functions, and their mappings: {final_state['mapped_pine_info']}")
if __name__ == "__main__":
    main()
