#!/usr/bin/env python3
"""
Legal Azazil — Professional Legal Chatbot
Baby Blue & Baby Pink Theme — Final Refined UI
"""

import os
import re
import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKEND_URL = os.getenv("BACKEND_URL", "https://azazil-backend-866345821059.asia-south1.run.app").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("BACKEND_TIMEOUT_SECONDS", "120"))

TOOL_META = {
    "review_contract": {
        "label": "Contract Review",
        "css": "tool-review",
        "icon": "📄",
        "desc": "Paste any contract to get a detailed risk analysis, clause‑by‑clause commentary, and actionable recommendations.",
    },
    "benchmark_ma_provision": {
        "label": "M&A Benchmarking",
        "css": "tool-maud",
        "icon": "📊",
        "desc": "Compare your M&A provisions against market standards using our extensive database of precedents.",
    },
    "draft_contract": {
        "label": "Contract Drafting",
        "css": "tool-draft",
        "icon": "✍️",
        "desc": "Describe your business deal and we'll generate a customized, legally‑sound draft agreement.",
    },
    "web_search": {
        "label": "Legal Research",
        "css": "tool-search",
        "icon": "🔍",
        "desc": "Ask any legal question and get curated answers from verified legal sources and case law.",
    },
}

SEVERITY_RE = re.compile(r"\bSeverity:\s*(High|Medium|Low)\b", re.IGNORECASE)

def _colorize_severities(text: str) -> str:
    def repl(m):
        level = m.group(1).capitalize()
        css = {"High": "severity-high", "Medium": "severity-medium", "Low": "severity-low"}[level]
        return f'Severity: <span class="{css}">{level}</span>'
    return SEVERITY_RE.sub(repl, text)

def _call_backend_chat(history, message):
    resp = httpx.post(
        f"{BACKEND_URL}/api/chat",
        json={"history": history, "message": message},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    tool_outputs = [(t["name"], t["content"]) for t in data.get("tool_outputs", [])]
    return data["history"], data["reply"], data.get("tool_used"), tool_outputs

# ---------------------------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Legal Azazil",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — Baby Blue & Baby Pink Theme (Final)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        /* ---- Global ---- */
        * {
            box-sizing: border-box;
        }
        .stApp {
            background: #F5F9FF !important;
            font-family: 'Inter', 'Segoe UI', Roboto, sans-serif;
        }
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 1rem;
            max-width: 1100px;
        }

        /* ---- Header (Centered, Larger, Creative) ---- */
        .app-header {
            text-align: center;
            padding: 1.8rem 0.5rem 1.2rem 0.5rem;
            margin-bottom: 2rem;
            background: linear-gradient(135deg, #D4E8FF 0%, #FFE0ED 100%);
            border-radius: 30px;
            border: 1px solid #C5DFFF;
            box-shadow: 0 6px 20px rgba(168, 216, 234, 0.2);
            position: relative;
        }
        .app-header .main-title {
            font-size: 3.2rem;
            font-weight: 800;
            color: #2A4B7C;
            letter-spacing: -0.02em;
            margin: 0;
            line-height: 1.2;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .app-header .main-title .pink {
            color: #B85C7A;
            background: rgba(255, 182, 193, 0.3);
            padding: 0 12px;
            border-radius: 40px;
            font-weight: 700;
        }
        .app-header .tagline {
            font-size: 1.1rem;
            color: #3A5A8A;
            margin-top: 0.3rem;
            font-weight: 400;
            letter-spacing: 0.04em;
            opacity: 0.85;
        }
        .app-header .badge {
            display: inline-block;
            background: #FFB6C1;
            color: white;
            font-size: 0.7rem;
            font-weight: 700;
            padding: 0.2rem 1rem;
            border-radius: 30px;
            margin-top: 0.2rem;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }

        /* ---- Sidebar ---- */
        .css-1d391kg, .css-1d391kg .css-1d391kg {
            background: #FFFFFFF0 !important;
            backdrop-filter: blur(2px);
            border-right: 1px solid #DCE8F5;
        }
        .sidebar-title {
            font-size: 1.5rem;
            font-weight: 800;
            color: #2A4B7C;
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 1.2rem;
            justify-content: center;
        }
        .sidebar-title .pink {
            color: #B85C7A;
            background: rgba(255, 182, 193, 0.2);
            padding: 0 8px;
            border-radius: 20px;
        }
        .sidebar-section {
            background: #F8FAFF;
            border-radius: 16px;
            padding: 1rem 1.2rem;
            margin-bottom: 1.5rem;
            border: 1px solid #E0E7FF;
        }
        .sidebar-section h4 {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #64748B;
            margin-bottom: 0.8rem;
            border-bottom: 1px solid #E2E8F0;
            padding-bottom: 0.4rem;
        }
        .tool-card {
            background: white;
            border-radius: 12px;
            padding: 0.6rem 0.9rem;
            margin-bottom: 0.6rem;
            border-left: 4px solid #A8D8EA;
            transition: all 0.15s ease;
            box-shadow: 0 1px 2px rgba(0,0,0,0.02);
        }
        .tool-card:hover {
            transform: translateX(3px);
            box-shadow: 0 4px 10px rgba(168, 216, 234, 0.2);
        }
        .tool-card .tool-name {
            font-weight: 600;
            font-size: 0.9rem;
            color: #1E2937;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .tool-card .tool-desc {
            font-size: 0.75rem;
            color: #475569;
            margin-top: 2px;
            line-height: 1.4;
        }
        .tool-card.pink {
            border-left-color: #FFB6C1;
        }
        .tool-card.purple {
            border-left-color: #C9A8EB;
        }
        .tool-card.green {
            border-left-color: #A8E6CF;
        }

        /* ---- Chat Input ---- */
        .stChatFloatingInput {
            max-width: 900px;
            margin: 0 auto;
        }
        .stChatFloatingInput > div > div {
            border-radius: 30px !important;
            border: 1px solid #D4E8FF !important;
            background: white;
            padding: 0.3rem 0.3rem 0.3rem 1.2rem;
            box-shadow: 0 2px 8px rgba(168, 216, 234, 0.15);
        }
        .stChatFloatingInput textarea {
            font-size: 0.95rem;
        }
        .stChatFloatingInput button {
            background: #FFB6C1 !important;
            border-radius: 30px !important;
            padding: 0.4rem 1.8rem !important;
            color: white !important;
            font-weight: 600 !important;
            border: none !important;
            transition: all 0.2s;
        }
        .stChatFloatingInput button:hover {
            background: #FF9EAB !important;
            transform: scale(1.02);
            box-shadow: 0 4px 12px rgba(255, 182, 193, 0.4);
        }

        /* ---- Chat Messages ---- */
        .stChatMessage {
            border-radius: 16px !important;
            padding: 0.9rem 1.2rem !important;
            margin-bottom: 0.8rem !important;
            border: 1px solid #E6EDFF;
            background: white;
        }
        .stChatMessage.user {
            background: #E8F0FE !important;
            border-color: #C5DFFF !important;
        }
        .stChatMessage.assistant {
            background: white !important;
            border-color: #E6EDFF !important;
        }

        /* ---- Tool Badge ---- */
        .tool-badge {
            display: inline-block;
            font-size: 0.7rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            padding: 0.2rem 0.8rem;
            border-radius: 30px;
            color: white;
            margin-bottom: 0.5rem;
            background: #A8D8EA;
        }
        .tool-badge.tool-review { background: #A8D8EA; }
        .tool-badge.tool-maud   { background: #C9A8EB; }
        .tool-badge.tool-draft  { background: #A8E6CF; }
        .tool-badge.tool-search { background: #FFB6C1; }

        /* ---- Severity ---- */
        .severity-high   { color: #DC2626; font-weight: 700; }
        .severity-medium { color: #D97706; font-weight: 700; }
        .severity-low    { color: #15803D; font-weight: 700; }

        /* ---- Expander ---- */
        .streamlit-expanderHeader {
            background: #F8FAFF !important;
            border-radius: 10px !important;
            border: 1px solid #DCE8F5 !important;
            font-weight: 500;
            color: #1E2937;
            font-size: 0.85rem;
        }
        .streamlit-expanderContent {
            background: #FCFDFF !important;
            border-radius: 0 0 10px 10px !important;
            border: 1px solid #DCE8F5 !important;
            border-top: none !important;
        }

        /* ---- Sidebar Button ---- */
        .stButton button {
            background: white !important;
            border: 1px solid #D4E8FF !important;
            border-radius: 30px !important;
            color: #1E2937 !important;
            font-weight: 500 !important;
            transition: all 0.2s;
        }
        .stButton button:hover {
            background: #E8F0FE !important;
            border-color: #A8D8EA !important;
        }

        /* ---- Spinner ---- */
        .stSpinner > div {
            border-top-color: #FFB6C1 !important;
        }

        /* ---- Responsive ---- */
        @media (max-width: 768px) {
            .app-header .main-title {
                font-size: 2.2rem;
            }
            .app-header .tagline {
                font-size: 0.9rem;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-title">
            ⚖️ Legal <span class="pink">Azazil</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="sidebar-section">
            <h4>🛠️ Available Tools</h4>
        """,
        unsafe_allow_html=True,
    )

    for key, meta in TOOL_META.items():
        color_class = ""
        if "review" in key:
            color_class = ""
        elif "benchmark" in key:
            color_class = "purple"
        elif "draft" in key:
            color_class = "green"
        elif "search" in key:
            color_class = "pink"
        st.markdown(
            f"""
            <div class="tool-card {color_class}">
                <div class="tool-name">{meta['icon']} {meta['label']}</div>
                <div class="tool-desc">{meta['desc']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

    if st.button("Clear Conversation", use_container_width=True):
        for key in ["history", "display"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()

    st.markdown(
        """
        <div style="font-size: 0.65rem; color: #94A3B8; text-align: center; margin-top: 1.5rem;">
            Legal Azazil v2.0
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Main Chat Interface
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="app-header">
        <div class="main-title">
            ⚖️ Legal <span class="pink">Azazil</span>
        </div>
        <div class="tagline">Contract Review · Drafting · Benchmarking · Research</div>
        <div class="badge">✨ Intelligent Legal Assistant</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if "history" not in st.session_state:
    st.session_state.history = []
if "display" not in st.session_state:
    st.session_state.display = []

for msg in st.session_state.display:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            tool_used = msg.get("tool_used")
            if tool_used and tool_used in TOOL_META:
                meta = TOOL_META[tool_used]
                st.markdown(f'<span class="tool-badge {meta["css"]}">{meta["icon"]} {meta["label"]}</span>', unsafe_allow_html=True)

            for tool_name, content in msg.get("tool_outputs", []):
                label = TOOL_META.get(tool_name, {}).get("label", tool_name)
                icon = TOOL_META.get(tool_name, {}).get("icon", "📎")
                with st.expander(f"{icon} Full {label} Output"):
                    st.markdown(_colorize_severities(content), unsafe_allow_html=True)

        st.write(msg["content"])

prompt = st.chat_input("Describe your legal task or paste contract text...")

if prompt:
    st.session_state.display.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Legal Azazil is analyzing..."):
            try:
                updated_history, reply, tool_used, tool_outputs = _call_backend_chat(
                    st.session_state.history, prompt
                )
                st.session_state.history = updated_history
            except httpx.TimeoutException:
                reply = "⏳ The backend took too long to respond. Please try again."
                tool_used = None
                tool_outputs = []
            except httpx.HTTPStatusError as e:
                reply = f"⚠️ Server error: {e.response.status_code}. Please try later."
                tool_used = None
                tool_outputs = []
            except Exception as e:
                reply = f"🔌 Connection error: {str(e)}"
                tool_used = None
                tool_outputs = []

        if tool_used and tool_used in TOOL_META:
            meta = TOOL_META[tool_used]
            st.markdown(f'<span class="tool-badge {meta["css"]}">{meta["icon"]} {meta["label"]}</span>', unsafe_allow_html=True)

        for tool_name, content in tool_outputs:
            label = TOOL_META.get(tool_name, {}).get("label", tool_name)
            icon = TOOL_META.get(tool_name, {}).get("icon", "📎")
            with st.expander(f"{icon} Full {label} Output"):
                st.markdown(_colorize_severities(content), unsafe_allow_html=True)

        st.write(reply)

    st.session_state.display.append({
        "role": "assistant",
        "content": reply,
        "tool_used": tool_used,
        "tool_outputs": tool_outputs
    })