from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, ToolMessage


class mappings(BaseModel):
    """Defines the structure of mappings for the legacy and pine variables and their confidence scores."""
    legacy: str
    pine: str
    confidence: str

def make_extraction_planning_call(model):
    """Makes a call to the LLM to plan the extraction process
    
    Args:
        model: The LLM model to use for planning
    """
    def extraction_call(state: dict):
        """ The llm makes a plan as to how to extract the relevant information from the legacy templates"""
        """LLM decides whether to call a tool or not"""
        return {
            "messages": [
                model.invoke(
                    [
                        SystemMessage(
                            content=(
                                "You are an expert at extracting variables and functions "
                                "from legacy legal tfrom dotenv import load_dotenvemplates and mapping it to the Pine syntax."
                                "You will be provided with a legacy template for extraction."
                                "Your task is to provide a structured plan to extract ALL dynamic"
                                "variables and functions from the legacy template. Ignore any static text"
                                "Your output should be a numbered step by step plan to complete the extraction"
                                "You will be provided with a reference pine template"
                                "Here is the template to analyze"
                                "{state.legacy_template}"
                                "Here is an example pine template"
                                "{state.example_pine_template}"
                                "Begin planning"
                            )
                        )
                    ]
                    + state["messages"]
                )
            ],
            "llm_calls": state.get('llm_calls', 0) + 1
        }
    return extraction_call

