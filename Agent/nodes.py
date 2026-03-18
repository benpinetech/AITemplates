from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage
from prompts import extraction_planning_prompt, extraction_prompt, mapping_planning_prompt, mapping_prompt, generation_prompt
from striprtf.striprtf import rtf_to_text

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
        print("Planning extraction of variables and functions from legacy template")
        return {
            "messages": [
                model.invoke(
                    [
                        SystemMessage(
                            content=extraction_planning_prompt(
                                state['legacy_template'],
                                state['example_pine_template']
                            )
                        )
                    ]
                )
            ],
            "llm_calls": state.get('llm_calls', 0) + 1
        }
    
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
                    content=extraction_prompt(state['legacy_template'])
                )
            ]
        )

        extracted_vars = [
            line.strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        print("Extracting variables and functions from legacy template")
        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "extracted_legacy_info": extracted_vars,
        }
    
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
        print("Planning mapping of extracted variables to pine syntax")
        return {
            "messages": [
                model.invoke(
                    [
                        SystemMessage(
                            content=mapping_planning_prompt(
                                state['extracted_legacy_info']
                            )
                        )
                    ]
                )
            ],
            "llm_calls": state.get('llm_calls', 0) + 1
        }
        
    return mapping_planning_call

def make_mapping_call(model):
    """Makes a call to the LLM to perform the mapping of the extracted
       variables and functions to the Pine syntax
    
    Args:
        model: The LLM model to use for planning
    """
    def mapping_call(state: dict):
        """ The llm makes the mapping based on the plan it made in the previous step"""
        tool_messages = [
            m for m in state["messages"]
            if isinstance(m, ToolMessage) or (isinstance(m, AIMessage) and m.tool_calls)
        ]
        response = model.invoke(
            [
                SystemMessage(
                    content=mapping_prompt(state['extracted_legacy_info'])
                )
            ]
            + tool_messages
        )

        extracted_vars = [
            line.strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        print("Mapping variables to pine syntax")
        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "mapping_calls": state.get('mapping_calls', 0) + 1,
            "mapped_pine_info": extracted_vars,
        }
    
    return mapping_call

def make_pine_template_generation_call(model):
    """Make a call to the LLM to generate a Pine template
       based on the mapped variables and functions
    
    Args:
        model: The LLM model to use for planning
    """
    def pine_template_generation_call(state: dict):
        """ The llm generates a pine templated based on the
            mapped legacy to pine variables and functions.
        """
        response = model.invoke(
            [
                SystemMessage(
                    content=generation_prompt(
                        state['legacy_template'],
                        state['example_pine_template'],
                        state['mapped_pine_info'],
                        state.get('rtf_rendering_output', '')
                    )
                )
            ]
        )
        print("Generating the Pine template")
        return {
            "messages": [response],
            "llm_calls": state.get('llm_calls', 0) + 1,
            "generated_pine_template": response.content,
        }
    
    return pine_template_generation_call

def validate_rtf(state: dict):
    """ Validates that the generated .rtf template is correctly formatted.
        This is done by attempting to render the .rtf and checking for errors.

        Args: state: the current agent state
    """
    try:
        rtf_to_text(state["generated_pine_template"])
        print("RTF validation successful")
        return {"rtf_validation_error": "", "generation_retries": state.get("generation_retries", 0) + 1}
    except Exception as e:
        print(f"RTF validation failed: {e}")
        print(f"Retrying generation of Pine template: Attempt {state.get('generation_retries', 0) + 1}")
        return {"rtf_validation_error": str(e), "generation_retries": state.get("generation_retries", 0) + 1}