from langgraph.graph.message import add_message
from typing import TypedDict, Annotated
import operator
from langchain_core.messages import AnyMessage

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    llm_calls: int
    legacy_template: str
    example_pine_template: str
    extracted_legacy_info: list[str]