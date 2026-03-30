import os
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from typing import Literal
from dotenv import load_dotenv
from state import AgentState
from tools import get_rag_tool
from nodes import *
from mappingdb import MappingDB

class Agent:
    def __init__(self, execution_model="gpt-5-mini", generation_model="gpt-5-mini", temperature=0.7, max_tokens=16384, generation_max_tokens=65536):
        self.execution_model = execution_model
        self.generation_model = generation_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.generation_max_tokens = generation_max_tokens
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
        if state.get("generation_retries", 0) >= 3:
            return "end"
        if state.get("rtf_validation_error", ""):
            return "rerender"
        return "end"

    def _build_graph(self):
        """ Method to initialize the agents models, tools, edges and nodes

            Returns: A compiled agent
        """
        # Execution model (reasoning, for complex tasks like mapping)
        execution_model = init_chat_model(self.execution_model, temperature=self.temperature, max_tokens=self.max_tokens)

        # Generation model (non-reasoning, high token limit for large RTF output)
        generation_model = init_chat_model(self.generation_model, temperature=self.temperature, max_tokens=self.generation_max_tokens)

        # Model with db searching tool
        rag_tool = get_rag_tool()
        execution_model_with_rag_tool = execution_model.bind_tools([rag_tool])

        # Tool node for the RAG tool
        rag_tool_node = ToolNode([rag_tool])

        # Define the different models to use in the agent
        extraction = make_extraction_call()
        mapping = make_mapping_call(execution_model_with_rag_tool)
        mapping_finalize = make_mapping_finalize(execution_model)
        pine_template_generation = make_pine_template_generation_call(generation_model)

        agent_builder = StateGraph(AgentState)
        
        # Add the nodes to the agent
        agent_builder.add_node("extraction", extraction)
        agent_builder.add_node("mapping", mapping)
        agent_builder.add_node("mapping_finalize", mapping_finalize)
        agent_builder.add_node("rag_tool", rag_tool_node)
        agent_builder.add_node("pine_template_generation", pine_template_generation)
        agent_builder.add_node("validate_rtf", validate_rtf)
        agent_builder.add_node("load_mappings", load_mappings)
        agent_builder.add_node("save_mappings", save_mappings)
        agent_builder.add_node("replace_fillpoints", replace_fillpoints)

        # Define the edges through the nodes to build the graph
        agent_builder.add_edge(START, "extraction")
        agent_builder.add_edge("extraction", "load_mappings")
        agent_builder.add_edge("load_mappings", "mapping")
        agent_builder.add_conditional_edges("mapping", self.should_continue,{
            "tool_node": "rag_tool",
            "end": "mapping_finalize"
        })
        agent_builder.add_edge("rag_tool", "mapping")
        agent_builder.add_edge("mapping_finalize", "save_mappings")
        agent_builder.add_edge("save_mappings", "replace_fillpoints")
        agent_builder.add_edge("replace_fillpoints", END)

        # Return the compiled agent
        return agent_builder.compile()
    
    def run(self, legacy_template: str):
        """ Method to run the agent with a legacy template and example
            pine template

            Args: legacy_template: The template we are going to map to pine
                  example_pine_template: An example to give the agent context

            Returns: The final agent state containing the genereated pine template
        """
        initial_state = AgentState(
            messages=[],
            llm_calls=0,
            legacy_template=legacy_template,
            extracted_legacy_info=[]
        )
        final_state = self.graph.ainvoke(initial_state)
        return final_state