# backend/main.py
"""
Azazil Legal AI API.

Run with:
    uvicorn backend.main:app --reload --port 8000

(from the project root, so `agents`, `utils`, `config` are importable --
the sys.path insert below also covers running this file directly, or
uvicorn being invoked from a different working directory.)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import chat, metrics

app = FastAPI(title="Azazil Legal AI API", version="1.0.0")

# The Streamlit frontend calls this server-side (not from the browser), so
# CORS isn't strictly required for that path -- but it's enabled anyway in
# case you hit the API directly from a browser-based client later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(metrics.router, prefix="/api", tags=["metrics"])


@app.get("/health")
def health():
    return {"status": "ok"}
