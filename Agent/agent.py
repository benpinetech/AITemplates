import os
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from typing import Literal
from dotenv import load_dotenv
from state import AgentState
from tools import get_rag_tool
from nodes import make_extraction_planning_call, make_extraction_call, make_pine_mapping_planning_call, make_mapping_call, make_pine_template_generation_call

class Agent:
    def __init__(self, tools, planning_model="gpt-4.1-mini", execution_model="gpt-5-mini", generation_model="gpt-4.1-mini", temperature=0.7, max_tokens=16384, generation_max_tokens=32768):
        self.planning_model = planning_model
        self.execution_model = execution_model
        self.generation_model = generation_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.generation_max_tokens = generation_max_tokens
        self.tools = tools
        self.graph = self._build_graph()

    def should_continue(self, state):
        messages = state["messages"]
        last_message = messages[-1]

        if state["mapping_calls"] >= 10:
            return "end"

        if last_message.tool_calls:
            return "tool_node"

        return "end"
    
    def should_rerender(self, state):
        if state.get("rtf_rendering_calls", 0) >= 3:
            return "end"
        if state.get("rtf_rendering_output", "") == "":
            return "end"
        return "rerender"

    def _build_graph(self):
        # Planning model (non-reasoning, fast)
        planning_model = init_chat_model(self.planning_model, temperature=self.temperature, max_tokens=self.max_tokens)

        # Execution model (reasoning, for complex tasks like mapping)
        execution_model = init_chat_model(self.execution_model, temperature=self.temperature, max_tokens=self.max_tokens)

        # Generation model (non-reasoning, high token limit for large RTF output)
        generation_model = init_chat_model(self.generation_model, temperature=self.temperature, max_tokens=self.generation_max_tokens)

        # Model with db searching tool
        rag_tool = get_rag_tool()
        execution_model_with_rag_tool = execution_model.bind_tools([rag_tool])

        # Tool node for the RAG tool
        rag_tool_node = ToolNode([rag_tool])

        extraction_planning = make_extraction_planning_call(planning_model)
        extraction = make_extraction_call(execution_model)
        mapping_planning = make_pine_mapping_planning_call(planning_model)
        mapping = make_mapping_call(execution_model_with_rag_tool)
        pine_template_generation = make_pine_template_generation_call(generation_model)

        agent_builder = StateGraph(AgentState)
        
        agent_builder.add_node("extraction_planning", extraction_planning)
        agent_builder.add_node("extraction", extraction)
        agent_builder.add_node("mapping_planning", mapping_planning)
        agent_builder.add_node("mapping", mapping)
        agent_builder.add_node("rag_tool", rag_tool_node)
        agent_builder.add_node("pine_template_generation", pine_template_generation)

        agent_builder.add_edge(START, "extraction_planning")
        agent_builder.add_edge("extraction_planning", "extraction")
        agent_builder.add_edge("extraction", "mapping_planning")
        agent_builder.add_edge("mapping_planning", "mapping")
        agent_builder.add_conditional_edges("mapping", self.should_continue,{
            "tool_node": "rag_tool",
            "end": "pine_template_generation"
        })
        agent_builder.add_edge("rag_tool", "mapping")
        agent_builder.add_conditional_edges("pine_template_generation", self.should_rerender, {
            "rerender": "pine_template_generation",
            "end": END
        })
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