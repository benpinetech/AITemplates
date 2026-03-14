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
    def extraction_planning_call(state: dict):
        """ The llm makes a plan as to how to extract the relevant information from the legacy templates"""
        return {
            "messages": [
                model.invoke(
                    [
                        SystemMessage(
                            content=(
                                "You are an expert at extracting variables and functions "
                                "from legacy legal templates and mapping it to the Pine syntax."
                                "You will be provided with a legacy template for extraction."
                                "Your task is to provide a structured plan to extract ALL dynamic"
                                "variables and functions from the legacy template. Ignore any static text"
                                "Your output should be a numbered step by step plan to complete the extraction"
                                "DO NOT PERFORM ANY EXTRACTION OR CONVERSION TO PINE SYNTAX, only provide a plan"
                                "Respond only with the plan, no not include any other text in your response"
                                "You will be provided with a reference pine template"
                                "Here is the template to analyze"
                                f"{state['legacy_template']}"
                                "Here is an example pine template"
                                f"{state['example_pine_template']}"
                                "Begin planning"
                            )
                        )
                    ]
                    + state["messages"]
                )
            ],
            "llm_calls": state.get('llm_calls', 0) + 1
        }
    print("Planning extraction of variables and functions from legacy template")
    return extraction_planning_call

def make_extraction_call(model):
    """Makes a call to the LLM to perform the extraction process
    
    Args:
        model: The LLM model to use for planning
    """
    def extraction_call(state: dict):
        """ The llm makes the extration based on the plan it made in the previous step"""
        response = model.invoke(
            [
                SystemMessage(
                    content=(
                        "You are an expert at extracting variables and functions "
                        "from legacy legal templates and mapping it to the Pine syntax."
                        "In a previous step you created a plan to extract the data follow it EXACTLY"
                        "You will be provided with a legacy template for extraction."
                        "Your task is to extract ALL dynamic"
                        "variables and functions from the legacy template. Ignore any static text"
                        "Your output should be a list of extracted legacy variables and functions"
                        "Ensure you gather every dynamic variable and function"
                        "Respond only with the list of variabes and function, do not include any"
                        "other text in your response. Preserve the original merge field delimiters"
                        "Here is the template to analyze"
                        f"{state['legacy_template']}"
                        "Begin extraction"
                    )
                )
            ]
            + state["messages"]
        )

        extracted_vars = [
            line.strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]

        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "extracted_legacy_info": extracted_vars,
        }
    print("Extracting variables and functions from legacy template")
    return extraction_call

def make_pine_mapping_planning_call(model):
    """Makes a call to the LLM to plan the mapping of the extracted 
       legacy variables and functions to the Pine syntax
    
    Args:
        model: The LLM model to use for planning
    """
    def mapping_planning_call(state: dict):
        """ The llm makes a plan to map the extracted legacy variables
            and functions to pine syntax based on the extracted data 
            example pine template provided.
        """
        return {
            "messages": [
                model.invoke(
                    [
                        SystemMessage(
                            content=(
                                "You are an expert at extracting variables and functions "
                                "from legacy legal templates and mapping it to the Pine syntax."
                                "You have already extraced a list of legacy variables and functions"
                                "from a legacy template. They are stored in the state and will be used"
                                "in the next step. Now your task is to create a plan to map"
                                "EACH extracted legacy variable and function to the appropriate Pine"
                                "syntax. During the next step you will be provided with a tool to search"
                                "a pine syntax database, an example Pine template, the legacy"
                                "template, and the list of variables and functions that have alread been extracted"
                                "from the legacy template. The plan you create does not need to include extraction"
                                "Respond only with the plan, do not not include any other text in your response"
                                "The plan should be a numbered step by step process to map each extracted legacy variable"
                                "and function to Pine syntax."
                                "Here is the example Pine template for reference"
                                f"{state['example_pine_template']}"
                                "Begin planning"
                            )
                        )
                    ]
                    + state["messages"]
                )
            ],
            "llm_calls": state.get('llm_calls', 0) + 1
        }
    print("Planning mapping of extracted variables to pine syntax")
    return mapping_planning_call

def make_mapping_call(model):
    """Makes a call to the LLM to perform the mapping of the extracted
       variables and functions to the Pine syntax
    
    Args:
        model: The LLM model to use for planning
    """
    def mapping_call(state: dict):
        """ The llm makes the mapping based on the plan it made in the previous step"""
        response = model.invoke(
            [
                SystemMessage(
                    content=(
                        "You are an expert at extracting variables and functions "
                        "from legacy legal templates and mapping it to the Pine syntax."
                        "In a previous step you created a plan to map the extracted legacy"
                        " variables and functions to Pine syntax. Follow it EXACTLY"
                        "You will be provided with:" \
                        "- A legacy template" \
                        "- An example Pine template for reference" \
                        "- A list of extracted legacy variables and functions that you will need to map to Pine syntax"
                        "- A tool to search a Pine syntax vector database to find the appropriate Pine syntax for each extracted legacy variable and function"
                        "Your task is to map ALL extracted legacy variables and function to the appropriate Pine syntax."
                        "Your output should be a mapping of each extracted legacy variable and function to the appropriate Pine syntax. "
                        "If you cannot find a mapping for a particular legacy variable or function, respond with 'No mapping found' for that item."
                        "Ensure you map EVERY extracted variable and function"
                        "Respond only with the mappings of variables and functions, do not include any"
                        "other text in your response. Preserve the original merge field delimiters"
                        "Here is the legacy template for reference"
                        f"{state['legacy_template']}"
                        "Here is the example Pine template for reference"
                        f"{state['example_pine_template']}"
                        "Here is the list of extracted legacy variables and functions"
                        f"{state['extracted_legacy_info']}"

                        "Begin mapping"
                    )
                )
            ]
            + state["messages"]
        )

        extracted_vars = [
            line.strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "mapped_pine_info": extracted_vars,
        }
    print("Mapping variables to pine syntax")
    return mapping_call
