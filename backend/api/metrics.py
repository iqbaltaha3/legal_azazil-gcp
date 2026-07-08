# backend/api/metrics.py
"""
Thin GET wrappers around utils/metrics.py (SQLite-backed LLM/tool call
logging) so the Streamlit Metrics tab -- or any other client -- can read
them over HTTP instead of importing the sqlite file directly.
"""
from fastapi import APIRouter

from utils.metrics import (
    get_llm_call_summary,
    get_tool_call_summary,
    get_recent_llm_calls,
    get_recent_tool_calls,
    get_latency_over_time,
)

router = APIRouter()


@router.get("/metrics/llm-summary")
def llm_summary():
    return get_llm_call_summary()


@router.get("/metrics/tool-summary")
def tool_summary():
    return get_tool_call_summary()


@router.get("/metrics/recent-llm")
def recent_llm(limit: int = 50):
    return get_recent_llm_calls(limit=limit)


@router.get("/metrics/recent-tools")
def recent_tools(limit: int = 50):
    return get_recent_tool_calls(limit=limit)


@router.get("/metrics/latency")
def latency():
    return get_latency_over_time()
