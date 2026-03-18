from typing import TypedDict, Annotated
import operator
from langchain_core.messages import AnyMessage

class AgentState(TypedDict):
    """Holds the state of an agent, including its messages, LLM calls, and templates."""
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
    legacy_template: str
    example_pine_template: str
    extracted_legacy_info: list[str]
    mapped_pine_info: list[str]
    mapping_calls: int
    generated_pine_template: str
    rtf_rendering_output: str = ""
    rtf_rendering_calls: int
