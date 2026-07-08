#!/usr/bin/env python3
"""
Azazil Legal AI -- Streamlit frontend.

This is now a thin HTTP client -- all computation (LLM calls, retrieval,
tool execution, metrics logging) happens in the FastAPI backend
(backend/main.py). Run the backend first:

    uvicorn backend.main:app --reload --port 8000

then this app:

    streamlit run app/chat_app.py

Set BACKEND_URL if the backend isn't on the default local address.
"""
import os
import re
import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "https://azazil-backend-866345821059.asia-south1.run.app").rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("BACKEND_TIMEOUT_SECONDS", "120"))

# ----------------------------------------------------------------------------
# Page config & styling
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Azazil Legal AI", page_icon="⚖️", layout="centered")

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .block-container {padding-top: 1.5rem; padding-bottom: 3rem; max-width: 920px;}

    .tool-badge {
        display: inline-block;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        padding: 0.15rem 0.6rem;
        border-radius: 999px;
        margin-bottom: 0.5rem;
        color: #ffffff;
    }
    .tool-review { background-color: #2f5d8a; }
    .tool-maud   { background-color: #6a3d9a; }
    .tool-draft  { background-color: #1e7a4f; }
    .tool-search { background-color: #b5651d; }

    .severity-high   { color: #c0392b; font-weight: 700; }
    .severity-medium { color: #b7791f; font-weight: 700; }
    .severity-low    { color: #2f7a3d; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚖️ Azazil Legal AI")
st.caption(
    "Contract review and drafting, grounded in real contract precedent -- "
    "not just LLM opinion."
)

TOOL_META = {
    "review_contract": {"label": "Contract Review", "css": "tool-review"},
    "benchmark_ma_provision": {"label": "M&A Benchmarking", "css": "tool-maud"},
    "draft_contract": {"label": "Contract Drafting", "css": "tool-draft"},
    "web_search": {"label": "Web Search", "css": "tool-search"},
}

EXAMPLE_PROMPTS = [
    "Review this NDA for one-sided clauses: [paste contract]",
    "Draft a consulting agreement between Acme Corp and Jane Doe, 12-month term",
    "Is a 3.5% termination fee typical for a merger agreement this size?",
    "What does 'force majeure' mean in a commercial lease?",
]

tab_chat, tab_about, tab_metrics = st.tabs(["💬 Chat", "ℹ️ How this works", "📊 Metrics"])

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
SEVERITY_RE = re.compile(r"\bSeverity:\s*(High|Medium|Low)\b", re.IGNORECASE)


def _colorize_severities(text: str) -> str:
    def repl(m):
        level = m.group(1).capitalize()
        css_class = {"High": "severity-high", "Medium": "severity-medium", "Low": "severity-low"}[level]
        return f'Severity: <span class="{css_class}">{level}</span>'
    return SEVERITY_RE.sub(repl, text)


def _call_backend_chat(history, message):
    """Returns (updated_history, reply, tool_used, tool_outputs) or raises."""
    resp = httpx.post(
        f"{BACKEND_URL}/api/chat",
        json={"history": history, "message": message},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    tool_outputs = [(t["name"], t["content"]) for t in data.get("tool_outputs", [])]
    return data["history"], data["reply"], data.get("tool_used"), tool_outputs


def _get_json(path, params=None, default=None):
    try:
        resp = httpx.get(f"{BACKEND_URL}/api{path}", params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return default


# ----------------------------------------------------------------------------
# Chat tab
# ----------------------------------------------------------------------------
with tab_chat:
    if "history" not in st.session_state:
        st.session_state.history = []
    if "display" not in st.session_state:
        st.session_state.display = []
    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = None

    if not st.session_state.display:
        st.markdown("**Try asking:**")
        cols = st.columns(2)
        for i, example in enumerate(EXAMPLE_PROMPTS):
            with cols[i % 2]:
                if st.button(example, key=f"example_{i}", use_container_width=True):
                    st.session_state.pending_prompt = example

    for msg in st.session_state.display:
        with st.chat_message(msg["role"]):
            tool_used = msg.get("tool_used")
            if tool_used and tool_used in TOOL_META:
                meta = TOOL_META[tool_used]
                st.markdown(
                    f'<span class="tool-badge {meta["css"]}">{meta["label"]}</span>',
                    unsafe_allow_html=True,
                )
            for tool_name, tool_content in msg.get("tool_outputs", []):
                label = TOOL_META.get(tool_name, {}).get("label", tool_name)
                with st.expander(f"Full {label} output (with citations)"):
                    st.markdown(_colorize_severities(tool_content), unsafe_allow_html=True)
            st.write(msg["content"])

    typed_prompt = st.chat_input(
        "Paste a contract to review, describe what you'd like drafted, "
        "or ask a legal question..."
    )
    prompt = typed_prompt or st.session_state.pending_prompt
    st.session_state.pending_prompt = None

    if prompt:
        st.session_state.display.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Working through this..."):
                try:
                    updated_history, assistant_reply, tool_used, tool_outputs = _call_backend_chat(
                        st.session_state.history, prompt
                    )
                    st.session_state.history = updated_history
                except httpx.ConnectError:
                    assistant_reply = (
                        f"Could not reach the backend at {BACKEND_URL}. "
                        f"Make sure it's running: `uvicorn backend.main:app --reload --port 8000`"
                    )
                    tool_used, tool_outputs = None, []
                except httpx.HTTPStatusError as e:
                    assistant_reply = f"Backend error ({e.response.status_code}): {e.response.text}"
                    tool_used, tool_outputs = None, []

            if tool_used and tool_used in TOOL_META:
                meta = TOOL_META[tool_used]
                st.markdown(
                    f'<span class="tool-badge {meta["css"]}">{meta["label"]}</span>',
                    unsafe_allow_html=True,
                )
            for tool_name, tool_content in tool_outputs:
                label = TOOL_META.get(tool_name, {}).get("label", tool_name)
                with st.expander(f"Full {label} output (with citations)"):
                    st.markdown(_colorize_severities(tool_content), unsafe_allow_html=True)

            st.write(assistant_reply)

        st.session_state.display.append({
            "role": "assistant",
            "content": assistant_reply,
            "tool_outputs": tool_outputs,
            "tool_used": tool_used,
        })

    if st.session_state.display:
        if st.button("🗑️ Clear conversation"):
            st.session_state.history = []
            st.session_state.display = []
            st.rerun()

# ----------------------------------------------------------------------------
# How this works tab -- plain-language explanation for non-technical users
# ----------------------------------------------------------------------------
with tab_about:
    st.subheader("What Azazil can do")
    st.markdown(
        """
Azazil is a legal assistant that only tells you things it can back up with a
real source -- it doesn't just guess based on general LLM knowledge. It reads
your message, decides which of four tools fits best, and uses that tool
automatically. You don't need to pick a mode yourself.
"""
    )

    st.markdown("#### The four tools")

    with st.container(border=True):
        st.markdown("**📘 Contract Review** *(general contracts)*")
        st.markdown(
            "Paste a contract (NDA, employment agreement, consulting agreement, "
            "lease, etc.). Azazil splits it into individual clauses, compares "
            "each one against a database of real SEC-filed contracts, and flags "
            "anything unusually one-sided -- with a severity rating (High / "
            "Medium / Low) and a citation to the specific comparison it used. "
            "For anything flagged Medium or High, it automatically adds "
            "**negotiation guidance**: a concrete fallback position, based on "
            "lessons from real past negotiations, so you know not just *what's* "
            "wrong but *what to do about it*."
        )

    with st.container(border=True):
        st.markdown("**🤝 M&A Benchmarking** *(merger agreements)*")
        st.markdown(
            "For merger & acquisition documents specifically -- termination "
            "fees, no-shop clauses, fiduciary-outs, MAC (material adverse "
            "change) clauses, closing conditions. These don't work like normal "
            "contract clauses; \"market standard\" here usually means a "
            "typical *range* (e.g. a 2-4% termination fee) rather than a single "
            "right answer. This tool checks your provision against a database "
            "of real merger agreements and tells you whether it leans "
            "buyer-favorable, seller-favorable, or sits at market standard."
        )

    with st.container(border=True):
        st.markdown("**✍️ Contract Drafting**")
        st.markdown(
            "Describe what you need (\"NDA between two startups, 2-year term\") "
            "and Azazil drafts it using real contract templates as a "
            "foundation -- it won't invent clause language out of thin air. "
            "You can also ask it to *revise* a contract you already reviewed; "
            "just say \"yes, please draft the fix\" and it will carry your "
            "original contract forward and only change the flagged clauses."
        )

    with st.container(border=True):
        st.markdown("**🌐 Web Search**")
        st.markdown(
            "For anything outside the contract database -- current statutes, "
            "recent case law, or general legal questions -- Azazil searches "
            "the web instead of guessing from memory."
        )

    st.markdown("#### Reading a review or benchmark report")
    st.markdown(
        """
- **Severity: High** :red[●] -- clearly deviates from market norms; worth pushing back on.
- **Severity: Medium** :orange[●] -- somewhat unusual; worth a closer look.
- **Severity: Low** :green[●] -- close enough to standard; low priority.
- **"No comparable clauses found"** means the database had nothing genuinely
  similar to compare against -- Azazil will say this explicitly rather than guess.
- Every finding cites which comparison (e.g. *Comparison 2*) it's based on, so
  you can always trace a conclusion back to its source.
"""
    )

    st.markdown("#### A few tips")
    st.markdown(
        """
- Paste the **full contract text**, not a summary -- the review tool needs
  actual clause language to compare against the database.
- You can have a back-and-forth: review a contract, then just say "yes, draft
  the revised version" without re-pasting anything.
- Expand **"Full ... output"** below any reply to see the complete,
  unabridged report -- Azazil's chat reply may lightly reformat, but the
  expander always has the full grounded findings.
"""
    )

    st.info(
        "Azazil is a drafting and analysis aid, not a substitute for review by "
        "a licensed attorney, especially for anything you intend to sign.",
        icon="⚠️",
    )

# ----------------------------------------------------------------------------
# Metrics tab
# ----------------------------------------------------------------------------
with tab_metrics:
    st.subheader("LLM Calls")
    st.caption(
        "Every model call the assistant makes -- deciding which tool to use, "
        "analyzing a clause, drafting text -- is logged here, read from the "
        "backend's /api/metrics endpoints."
    )

    llm_summary = _get_json("/metrics/llm-summary", default={})
    if llm_summary:
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total calls", llm_summary.get("total_calls", 0))
        col2.metric("Avg latency (ms)", llm_summary.get("avg_latency_ms", 0))
        col3.metric("Prompt tokens (est.)", llm_summary.get("total_prompt_tokens", 0))
        col4.metric("Completion tokens (est.)", llm_summary.get("total_completion_tokens", 0))
        col5.metric("Errors", llm_summary.get("errors", 0))
    else:
        st.caption(f"Could not reach backend metrics at {BACKEND_URL}. Is it running?")

    latency_series = _get_json("/metrics/latency", default=[])
    if latency_series:
        st.write("Latency over time (most recent 200 calls)")
        st.line_chart({"latency_ms": [row[1] for row in latency_series]})

    st.write("Recent LLM calls")
    recent_llm = _get_json("/metrics/recent-llm", params={"limit": 50}, default=[])
    if recent_llm:
        st.dataframe(recent_llm, use_container_width=True, hide_index=True)
    else:
        st.caption("No LLM calls logged yet. Use the Chat tab to generate activity.")

    st.markdown("---")
    st.subheader("Tool Calls")
    st.caption(
        "Which of the four tools (review, M&A benchmarking, drafting, web "
        "search) the assistant actually invoked, and how long each took."
    )

    tool_summary = _get_json("/metrics/tool-summary", default=[])
    if tool_summary:
        st.dataframe(tool_summary, use_container_width=True, hide_index=True)
    else:
        st.caption("No tool calls logged yet.")

    st.write("Recent tool calls")
    recent_tools = _get_json("/metrics/recent-tools", params={"limit": 50}, default=[])
    if recent_tools:
        st.dataframe(recent_tools, use_container_width=True, hide_index=True)
    else:
        st.caption("No tool calls logged yet.")

    if st.button("🔄 Refresh metrics"):
        st.rerun()
