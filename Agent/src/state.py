from typing import TypedDict, Annotated
import operator
from langchain_core.messages import AnyMessage
from pydantic import BaseModel

class mapping(BaseModel):
    """Defines the structure of mappings for the legacy and pine variables and their confidence scores."""
    legacy: str
    pine: str
    loaded: bool

class AgentState(TypedDict):
    """Holds the current state of an agent."""
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
    legacy_template: str
    example_pine_template: str
    extracted_legacy_info: list[str]
    fillpoint_spans: list[tuple[int, int, str]]
    unmapped_legacy_info: list[str]
    mapped_pine_info: list[mapping]
    mapping_calls: int
    generated_pine_template: str
    rtf_rendering_output: str = ""
    rtf_rendering_calls: int
    rtf_validation_error: str
    generation_retries: int