# backend/api/chat.py
"""
POST /api/chat -- the only endpoint the Streamlit frontend needs for the
conversation itself. Stateless by design: the frontend keeps the full
message history in st.session_state and sends it back each turn (same
contract agents/orchestrator_graph.run_turn already expects) -- no
server-side session store needed.
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.orchestrator_graph import run_turn

router = APIRouter()


class ChatRequest(BaseModel):
    history: List[Dict[str, Any]] = []
    message: str


class ToolOutput(BaseModel):
    name: str
    content: str


class ChatResponse(BaseModel):
    history: List[Dict[str, Any]]
    reply: str
    tool_used: Optional[str] = None
    tool_outputs: List[ToolOutput] = []


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty.")

    try:
        updated_messages = run_turn(req.history, req.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"orchestrator error: {e}")

    # Collect every tool result produced THIS turn (everything after the
    # most recent user message), same logic the old in-process chat_app.py
    # used to do client-side -- now centralized here so any frontend
    # (Streamlit, a future web UI, mobile, etc.) gets the same structured
    # response instead of re-parsing the raw message list itself.
    tool_outputs: List[ToolOutput] = []
    for m in reversed(updated_messages):
        if m.get("role") == "tool":
            tool_outputs.append(ToolOutput(name=m.get("name", "tool"), content=m["content"]))
        elif m.get("role") == "user":
            break
    tool_outputs.reverse()
    tool_used = tool_outputs[-1].name if tool_outputs else None

    reply = ""
    for m in reversed(updated_messages):
        if m.get("role") == "assistant" and m.get("content"):
            reply = m["content"]
            break

    return ChatResponse(
        history=updated_messages,
        reply=reply,
        tool_used=tool_used,
        tool_outputs=tool_outputs,
    )
