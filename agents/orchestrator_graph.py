# agents/orchestrator_graph.py
"""
Conversational orchestrator built on LangGraph.
"""

import json
import time
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END

from utils.llm_client import GroqClient
from utils.metrics import log_tool_call
from agents.review_tool import review_contract
from agents.draft_tool import draft_contract
from agents.search_tool import web_search
from agents.maud_tool import benchmark_ma_provision

SYSTEM_PROMPT = """You are Azazil, a legal contract assistant. You have four tools:

- review_contract: analyzes a contract clause-by-clause against a database of
  real SEC-filed contracts (CUAD), flagging clauses that are non-standard or
  one-sided, with cited comparisons. For any clause flagged Medium or High
  severity, it also automatically attaches negotiation guidance.
- benchmark_ma_provision: benchmarks M&A / merger-agreement provisions
  against the MAUD database.
- draft_contract: drafts a new contract or revises an existing one.
- web_search: looks up current information not covered by the contract
  database.

Rules:
- Use review_contract for general commercial contracts.
- Use benchmark_ma_provision for merger agreements / M&A documents.
- Present review/benchmark results essentially verbatim.
- After review, ask if user wants a revised draft.
- Keep responses clear and professional.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "review_contract",
            "description": "Review a contract clause-by-clause against real SEC-filed contracts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_text": {"type": "string", "description": "The full text of the contract to review."}
                },
                "required": ["contract_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "benchmark_ma_provision",
            "description": "Benchmark M&A provisions against the MAUD database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provision_text": {"type": "string", "description": "The M&A provision or full agreement text."}
                },
                "required": ["provision_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_contract",
            "description": "Draft or revise a contract.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instructions": {"type": "string", "description": "What to draft or fix."},
                    "source_contract": {"type": "string", "description": "Original contract text (optional)."},
                },
                "required": ["instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for legal information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_REGISTRY = {
    "review_contract": lambda args: review_contract(args.get("contract_text", "")),
    "benchmark_ma_provision": lambda args: benchmark_ma_provision(args.get("provision_text", "")),
    "draft_contract": lambda args: draft_contract(args.get("instructions", ""), args.get("source_contract")),
    "web_search": lambda args: web_search(args.get("query", "")),
}


class ChatState(TypedDict):
    messages: List[Dict[str, Any]]


# Lazy initialization to prevent slow startup
_llm = None
_graph = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = GroqClient()
    return _llm


def agent_node(state: ChatState) -> Dict:
    messages = state["messages"]
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    llm = get_llm()
    reply = llm.chat_with_tools(messages, tools=TOOLS, caller="agent")
    
    new_message = {"role": "assistant", "content": reply.get("content", "")}
    if reply.get("tool_calls"):
        new_message["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": json.dumps(tc["function"]["arguments"]),
                },
            }
            for tc in reply["tool_calls"]
        ]

    return {"messages": messages + [new_message]}


def tools_node(state: ChatState) -> Dict:
    messages = state["messages"]
    last = messages[-1]
    tool_messages = []

    for call in last.get("tool_calls", []):
        fn = call["function"]["name"]
        raw_args = call["function"]["arguments"]
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)

        handler = TOOL_REGISTRY.get(fn)
        start = time.time()
        success = True
        error = None
        try:
            result = handler(args) if handler else f"Unknown tool: {fn}"
        except Exception as e:
            success = False
            error = str(e)
            result = f"Tool '{fn}' failed: {e}"
        finally:
            log_tool_call(
                tool_name=fn,
                latency_ms=int((time.time() - start) * 1000),
                success=success,
                error=error
            )

        tool_messages.append({
            "role": "tool",
            "tool_call_id": call["id"],
            "name": fn,
            "content": result,
        })

    return {"messages": messages + tool_messages}


def route_after_agent(state: ChatState) -> str:
    last = state["messages"][-1]
    return "tools" if last.get("tool_calls") else END


def build_graph():
    builder = StateGraph(ChatState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tools_node)
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run_turn(history: List[Dict[str, Any]], user_message: str) -> List[Dict[str, Any]]:
    """Run one turn of conversation."""
    messages = history + [{"role": "user", "content": user_message}]
    graph = get_graph()
    final_state = graph.invoke({"messages": messages})
    return final_state["messages"]