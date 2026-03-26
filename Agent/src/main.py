import asyncio
from pathlib import Path

from agent import Agent
from utils import build_vector_store, load_vector_store
from dotenv import load_dotenv
import os
import argparse
import time

AGENT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENT_DIR.parent
CHROMA_DIR = AGENT_DIR / "chroma_db"
EXTRACTOR_DIR = REPO_ROOT / "phase1-jda-to-pine-extractor"
TEMPLATE_OUTPUT_DIR = AGENT_DIR / "template_output"

load_dotenv(REPO_ROOT / ".env")

parser = argparse.ArgumentParser()
parser.add_argument("File", help="Path to the legacy RTF template to be converted.")

def main():
    args = parser.parse_args()
    template_file_path = Path(args.File)
    if not template_file_path.exists():
        print(f"Error: File {template_file_path} does not exist.")
        return

    print("Testing agent.")
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
    
    with open(EXTRACTOR_DIR / "input" / "pine" / "3A.rtf", "r") as f:
        pine_template = f.read()

    with open(template_file_path, "r") as f:
        legacy_template = f.read()

    agent = Agent(tools=[])

    start = time.time()
    final_state = asyncio.run(agent.run(legacy_template=legacy_template, example_pine_template=pine_template))
    end = time.time()
    print("Agent execution time:", end - start, "seconds")
    mapped_info = final_state["mapped_pine_info"]
    unmapped = 0
    for m in mapped_info:
        if "no mapping found" in m.pine.lower():
            unmapped += 1

    TEMPLATE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = TEMPLATE_OUTPUT_DIR / template_file_path.name
    with open(output_path, "w") as f:
        f.write(final_state["generated_pine_template"])

    print(f"Agent run complete.")
    print(f"Number of mapped items: {len(mapped_info) - unmapped}")
    print(f"Number of unmapped items: {unmapped}")
    print(f"Output saved to {output_path}")
    print(f"all mapped variabled, functions, and their mappings: {final_state['mapped_pine_info']}")
if __name__ == "__main__":
    main()
