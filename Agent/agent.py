import os
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from typing import Literal
from dotenv import load_dotenv
from state import AgentState
from tools import get_rag_tool
from nodes import make_extraction_planning_call, make_extraction_call, make_pine_mapping_planning_call, make_mapping_call

class Agent:
    def __init__(self, tools, model="gpt-4.1-mini", temperature=0.7, max_tokens=2048):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools = tools
        self.graph = self._build_graph()

    def should_continue(self, AgentState):
        messages = AgentState["messages"]
        last_message = messages[-1]

        if last_message.tool_calls:
            return "tool_node"

        return "end"

    def _build_graph(self):
        # Base chat model for all calls
        model = init_chat_model(self.model, temperature=self.temperature, max_tokens=self.max_tokens)

        # Model with db searching tool
        model_with_rag_tool = model.bind_tools([get_rag_tool()])

        # Tool node for the RAG tool
        rag_tool_node = ToolNode([get_rag_tool()])

        extraction_planning = make_extraction_planning_call(model)
        extraction = make_extraction_call(model)
        mapping_planning = make_pine_mapping_planning_call(model)
        mapping = make_mapping_call(model_with_rag_tool)

        agent_builder = StateGraph(AgentState)
        
        agent_builder.add_node("extraction_planning", extraction_planning)
        agent_builder.add_node("extraction", extraction)
        agent_builder.add_node("mapping_planning", mapping_planning)
        agent_builder.add_node("mapping", mapping)
        agent_builder.add_node("rag_tool", rag_tool_node)

        agent_builder.add_edge(START, "extraction_planning")
        agent_builder.add_edge("extraction_planning", "extraction")
        agent_builder.add_edge("extraction", "mapping_planning")
        agent_builder.add_edge("mapping_planning", "mapping")
        agent_builder.add_conditional_edges("mapping", self.should_continue,{
            "tool_node": "rag_tool",
            "end": END
        })
        agent_builder.add_edge("rag_tool", "mapping")
        return agent_builder.compile()
    
    def run(self, legacy_template: str, example_pine_template: str):
        initial_state = AgentState(
            messages=[],
            llm_calls=0,
            legacy_template=legacy_template,
            example_pine_template=example_pine_template,
            extracted_legacy_info=[]
        )
        final_state = self.graph.ainvoke(initial_state)
        return final_state