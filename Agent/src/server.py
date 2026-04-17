import fastapi
import uvicorn
import asyncio

from agent import Agent
from utils import build_vector_store
from pathlib import Path

app = fastapi.FastAPI()

agent = None

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/generate_fillpoints")
def generate_fillpoints(data: dict):
    legacy_template = data.get("legacy_template")
    if not legacy_template:
        return {"error": "legacy_template is required"}, 400
    
    agent_response = asyncio.run(agent.run(legacy_template=legacy_template))

    generated_template = agent_response["generated_pine_template"]

    if not generated_template:
        return {"error": "Failed to generate Pine template"}, 500
    
    return {"generated_pine_template": generated_template}
    

if __name__ == "__main__":
    AGENT_DIR = Path(__file__).resolve().parent.parent
    REPO_ROOT = AGENT_DIR.parent
    CHROMA_DIR = AGENT_DIR / "chroma_db"
    GROUND_TRUTH_DIR = AGENT_DIR / "ground_truth"

    if not CHROMA_DIR.exists():
        print("Building vector store.")
        build_vector_store(
            ground_truth_path=str(GROUND_TRUTH_DIR / "pine_syntax_ground_truth.txt"),
            persist_directory=str(CHROMA_DIR),
        )
    else:
        print("Vector store already exists.")

    agent = Agent()

    uvicorn.run(app, host="0.0.0.0", port=8000)

