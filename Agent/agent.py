import os
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from typing import Literal
from dotenv import load_dotenv
from state import AgentState
from nodes import make_extraction_planning_call

class agent:
    def __init__(self, tools, model="gpt-4.1-mini", temperature=0.7, max_tokens=2048):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools = tools
        self.graph = self._build_graph()

    def _build_graph(self):
        model = init_chat_model(self.model, temperature=self.temperature, max_tokens=self.max_tokens)

        extraction_planning = make_extraction_planning_call(model)

        agent_builder = StateGraph(AgentState)
        
        agent_builder.add_node("extraction_planning", extraction_planning)