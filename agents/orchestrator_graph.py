# agents/orchestrator_graph.py
"""
Conversational orchestrator built on LangGraph.

Instead of a fixed sequence of agents, this is a single LLM node that can
call tools (review_contract, draft_contract, web_search) as needed, loop
until it has what it needs, then respond in plain text. Intent detection
happens implicitly through tool selection -- no separate classifier node.
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
  severity, it also automatically attaches negotiation guidance (a suggested
  fallback position / redline, grounded in a negotiation-playbook database)
  -- this is already built into the tool's output, you don't need a separate
  call for it.
- benchmark_ma_provision: benchmarks M&A / merger-agreement provisions
  (e.g. termination fees, no-shop clauses, fiduciary-outs, MAC-outs, closing
  conditions) against the MAUD database of real merger agreements, flagging
  whether a provision is buyer-favorable, seller-favorable, or market-standard,
  with cited comparisons. Like review_contract, any provision flagged Medium
  or High severity/deviation also automatically gets negotiation guidance
  attached -- already built into the tool's output, no separate call needed.
  Use this instead of review_contract when the contract in question is a
  merger agreement / M&A deal document, or when the user specifically asks
  about M&A deal-point market practice. It accepts either a full agreement
  or a single pasted provision.
- draft_contract: drafts a new contract or revises an existing one, always
  grounded in retrieved contract templates.
- web_search: looks up current information not covered by the contract
  database (e.g. statutes, recent case law, general legal questions).

Rules:
- If the user provides or references a general commercial contract and wants
  it checked/analyzed, call review_contract with the full contract text.
- If the user provides or references a merger agreement / M&A document, or
  asks about M&A-specific deal points (termination fees, no-shop, fiduciary
  duties, MAC clauses, etc.), call benchmark_ma_provision instead.
- When you receive a review_contract or benchmark_ma_provision result, present
  it to the user ESSENTIALLY VERBATIM -- the executive summary and the full
  clause/provision-by-clause findings, including severities, cited
  comparisons, and any attached negotiation guidance. Do not compress it into
  a vague one-paragraph summary; the per-clause detail is the entire value of
  these tools. Only lightly reformat for readability if needed.
- After presenting a review_contract or benchmark_ma_provision result, ALWAYS
  ask the user if they'd like you to draft a revised version using
  market-standard clauses. Do not draft automatically.
- If the user asks you to draft/generate/write a new contract, or says yes to
  drafting after a review, call draft_contract. If revising after a review,
  pass the original contract text as source_contract.
- If the user asks something outside the contract database's scope (current
  law, recent news, general questions), use web_search.
- Otherwise, just respond directly -- not every message needs a tool call.
- Keep responses clear and professional. Do not use emojis.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "review_contract",
            "description": "Review a contract clause-by-clause against real SEC-filed contracts, flagging non-standard or one-sided clauses with cited comparisons.",
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
            "description": "Benchmark M&A / merger-agreement provisions (termination fees, no-shop, fiduciary-outs, MAC-outs, closing conditions, etc.) against the MAUD database of real merger agreements, flagging buyer/seller-favorable or market-standard language with cited comparisons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "provision_text": {"type": "string", "description": "The M&A provision, or full merger agreement text, to benchmark."}
                },
                "required": ["provision_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_contract",
            "description": "Draft a new contract or revise an existing one, grounded in retrieved contract templates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instructions": {"type": "string", "description": "What to draft: contract type, parties, term, or which clauses to fix."},
                    "source_contract": {"type": "string", "description": "Original contract text, if revising after a review. Omit for a brand new draft."},
                },
                "required": ["instructions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information outside the contract database (current law, case law, general questions).",
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


_llm = GroqClient()


def agent_node(state: ChatState) -> Dict:
    messages = state["messages"]
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    reply = _llm.chat_with_tools(messages, tools=TOOLS, caller="agent")
    new_message = {"role": "assistant", "content": reply.get("content", "")}
    if reply.get("tool_calls"):
        # Store in OpenAI/Groq wire format (arguments as JSON string) so this
        # message can be replayed back to the API on the next turn.
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
            log_tool_call(tool_name=fn, latency_ms=int((time.time() - start) * 1000),
                           success=success, error=error)

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


_graph = build_graph()


def run_turn(history: List[Dict[str, Any]], user_message: str) -> List[Dict[str, Any]]:
    """
    Run one turn of conversation. `history` is the prior message list
    (excluding system prompt -- that's added automatically). Returns the
    updated message list, which the caller should store and pass back in
    on the next turn.
    """
    messages = history + [{"role": "user", "content": user_message}]
    final_state = _graph.invoke({"messages": messages})
    return final_state["messages"]