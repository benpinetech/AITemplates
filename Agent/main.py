import asyncio

from agent import Agent
from utils import build_vector_store, load_vector_store
from dotenv import load_dotenv
import os
load_dotenv()

def main():
    print("Testing agent.")
    vector_store = None
    if not os.path.exists("./chroma_db"):
        print("Building vector store.")
        vector_store = build_vector_store(ground_truth_path="../phase1-jda-to-pine-extractor/pine_syntax_ground_truth.txt")
    else:
        print("Vector store already exists.")
        vector_store = load_vector_store()
    
    with open("../phase1-jda-to-pine-extractor/input/pine/3A.rtf", "r") as f:
        pine_template = f.read()

    with open("../phase1-jda-to-pine-extractor/input/legacy/3A.rtf", "r") as f:
        legacy_template = f.read()

    agent = Agent(tools=[])

    final_state = asyncio.run(agent.run(legacy_template=legacy_template, example_pine_template=pine_template))
    mapped_info = final_state["mapped_pine_info"]
    unmapped = 0
    for mapping in mapped_info:
        if "No mapping found" in mapping:
            unmapped += 1

    with open("template_output/output.rtf", "w") as f:
        f.write(final_state["generated_pine_template"])

    print("Final state:", final_state["mapped_pine_info"])
    print("Final generated Pine template:", final_state["generated_pine_template"])
    print(f"Number of unmapped items: {unmapped}")
    
if __name__ == "__main__":
    main()
