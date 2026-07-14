# SYSTEM.md — Azazil Legal AI

Technical/architectural reference. For a plain-English tour of *what this does*, see the README.

---

## 1. Repository layout

```
legal-azazil-chatbot/
├── app/
│   └── chat_app.py                 Streamlit UI: Chat / How this works / Metrics tabs — HTTP client only
├── backend/
│   ├── main.py                       FastAPI app
│   └── api/
│       ├── chat.py                     POST /api/chat
│       └── metrics.py                   GET /api/metrics/*
├── agents/
│   ├── orchestrator_graph.py           LangGraph agent loop; SYSTEM_PROMPT is the routing "brain"
│   ├── review_tool.py                   review_contract; auto-chains negotiate_tool
│   ├── maud_tool.py                     benchmark_ma_provision; auto-chains negotiate_tool
│   ├── negotiate_tool.py                 internal-only; never LLM-selectable directly
│   ├── draft_tool.py                      draft_contract
│   └── search_tool.py                      web_search (Tavily)
├── utils/
│   ├── embedder.py                    sentence-transformers wrapper (BAAI/bge-small-en, 384-dim)
│   ├── chunking.py                     splits contract text into clauses by legal headers
│   ├── supabase_client.py               READ-ONLY vector search — used by agents/*.py
│   ├── supabase_ingest.py                WRITE (truncate/insert) — used ONLY by scripts/ingest_*.py
│   ├── llm_client.py                      Groq client (OpenAI-compatible), incl. chat_with_tools()
│   └── metrics.py                          logs every LLM/tool call to Postgres (see §5)
├── config/
│   └── settings.py                     SUPABASE_DB_URL, TABLES, model name, retrieval K values
└── scripts/
    ├── supabase_setup.sql                one-time: pgvector extension, 4 tables, match_<table>() fns
    ├── ingest_cuad_to_supabase.py
    ├── ingest_maud_to_supabase.py
    ├── ingest_playbook_to_supabase.py
    ├── ingest_templates_to_supabase.py
    └── migrate_chroma_to_supabase.py       one-time migration script (see §6)
```

**Reading order:** `agents/orchestrator_graph.py` (the routing brain) → `agents/review_tool.py` (the most detailed tool, sets the pattern the others follow) → `utils/supabase_client.py` → `config/settings.py`.

---

## 2. Request flow

```
Streamlit (chat_app.py)
    │  POST /api/chat  { history: [...], message: "..." }
    ▼
backend/api/chat.py  (stateless — receives full history every call)
    ▼
agents/orchestrator_graph.py  (LangGraph agent loop)
    │  reads SYSTEM_PROMPT + TOOLS schema, lets the model choose a tool via
    │  Groq's tool-calling (chat_with_tools())
    ▼
one of: review_contract | benchmark_ma_provision | draft_contract | web_search | (no tool — direct answer)
    │
    ├─▶ review_contract / benchmark_ma_provision
    │        → chunk the contract → embed each clause → vector search Supabase
    │        → per-clause grounded LLM call → if severity is Medium/High,
    │          auto-invoke negotiate_tool (not LLM-selectable on its own)
    │
    └─▶ draft_contract → retrieve comparable templates → LLM drafts/revises

Response returned as updated `history` back to Streamlit.
```

The backend is **stateless per request** — no server-side session store. Every call carries the entire conversation `history`; the backend's only job is to append to it and hand it back. This is a deliberate simplicity trade-off (§7).

---

## 3. Retrieval architecture: Postgres + pgvector, shaped like the old ChromaDB API

`utils/supabase_client.py` is a compatibility shim: it reproduces ChromaDB's exact old query shape (`collection.query(...) -> {"documents": [[...]], "metadatas": [[...]], "distances": [[...]]}`) but backs it with a Postgres `match_<table>(query_embedding, match_count)` function (defined in `scripts/supabase_setup.sql`) using the `pgvector` extension, instead of Chroma's HNSW index. This means `agents/review_tool.py`, `maud_tool.py`, `negotiate_tool.py`, and `draft_tool.py` required **no logic changes** during the migration — only the client underneath changed. Distance is cosine distance in both systems (lower = more similar), so all the existing relative-distance filtering logic carried over unchanged.

There are two, deliberately separate, database-facing modules:

| Module | Capability | Used by |
|---|---|---|
| `utils/supabase_client.py` | **Read-only** vector search | every `agents/*.py` file, at chat time |
| `utils/supabase_ingest.py` | **Write** (truncate + insert) | only `scripts/ingest_*.py`, run manually/offline |

Nothing reachable from a live chat conversation can write to, truncate, or otherwise modify the four corpora (`legal_risks`, `maud_clauses`, `negotiation_playbook`, `contract_templates`) — that capability exists in a module the running app never imports.

---

## 4. Retrieval quality filtering (the part that actually keeps this grounded)

Documented directly in `review_tool.py`'s module docstring: the CUAD ingestion is known to include non-clause fragments (headers, table-of-contents lines, bare dates) alongside real clause text, because metadata was ingested generically rather than per-document. Sending a fragment like `"ARTICLE 7 LIMITATIONS ON LIABILITY 22"` to the LLM as a "comparable clause" produces confident-sounding analysis grounded in nothing. Two filters run before anything reaches the LLM:

1. **Length filter** — chunks under `_MIN_QUALITY_CHARS` (120 chars) are dropped outright; real clauses in this corpus empirically run 300–1500+ characters.
2. **Relative distance-ratio filter** — a result is dropped if its distance is worse than `_MAX_DISTANCE_RATIO` (1.5×) times the best (rank-1) match's distance for the same query. This is a *relative*, not absolute, cutoff specifically so it works regardless of whether a given collection's distance metric is cosine or L2 — as long as "lower is more similar" holds (true for both).

If every candidate for a clause fails these filters, the tool reports "no comparable clause found" — the same as an empty retrieval — rather than forcing a fabricated comparison. This is the single most load-bearing design decision in the codebase; it's the mechanism that makes the "always grounded" claim actually true rather than aspirational.

**Citation display is also filtered**: the evidence block shown to the user only includes the comparison numbers the LLM's own finding text explicitly cites — not every comparison that was retrieved — so the report doesn't get padded with fragments that had no bearing on the actual conclusion.

---

## 5. Metrics: migrated from local SQLite to Postgres for a concrete infrastructure reason

`utils/metrics.py`'s docstring explains this directly: metrics were previously logged to a local SQLite file (`metrics.db`). On Cloud Run, each container instance has its own ephemeral filesystem and disk — meaning a local file (a) doesn't survive instance restarts/redeploys, and (b) isn't shared across multiple concurrently running instances, so the Metrics tab would only ever show a fraction of real activity under any real traffic. Metrics now write to two Postgres tables (`llm_calls`, `tool_calls`) in the same Supabase database as everything else, via the same connection pool — durable and consistent across instances. The public API (`init_metrics_db()`, logging function signatures) was kept identical, so `llm_client.py`, `orchestrator_graph.py`, and `backend/api/metrics.py` required no changes during this migration.

Token counts use Groq's real `response.usage` figures when the API returns them; otherwise a `chars / 4` estimate fills the gap so the metrics columns are never blank.

---

## 6. The Chroma → Supabase migration, and why it happened

`config/settings.py` retains `CHROMA_DB_PATHS` and `CHROMA_COLLECTIONS` constants, but marked explicitly as retained **only** so `scripts/migrate_chroma_to_supabase.py` can do a one-time read of a legacy on-disk `vector_stores/` folder. Nothing in `agents/*.py` or `utils/supabase_client.py` touches ChromaDB at runtime anymore. The underlying reason (same as the metrics migration): Cloud Run's ephemeral, per-instance filesystem doesn't suit a local on-disk vector store any better than it suited local SQLite — a durable, shared Postgres store was the fix for both.

---

## 7. Key architectural decisions

| Decision | Why |
|---|---|
| Tool selection via the LLM's own tool-calling, no separate intent classifier | Fewer moving parts, one less model call, and the routing logic lives in one editable place — `orchestrator_graph.py`'s `SYSTEM_PROMPT` — rather than a separate classifier that could disagree with the main model. |
| No multi-query expansion, no RRF fusion, no reranker | Kept deliberately simple and traceable, per `review_tool.py`'s docstring — every finding needs to point at real, inspectable evidence; added retrieval-fusion machinery would make that harder to audit, not easier. |
| `negotiate_tool` is not a directly LLM-selectable tool | Negotiation guidance without a specific flagged clause/provision to ground it isn't useful — it's deliberately only reachable as an automatic follow-up to a Medium/High-severity finding from `review_contract` or `benchmark_ma_provision`. |
| Stateless backend, full history sent every request | No server-side session store to build, expire, or lose — simpler operationally, at the cost of larger request payloads as a conversation grows. |
| Separate read vs. write Supabase modules | A structural (not just conventional) guarantee that a chat conversation can never truncate or corrupt the underlying legal corpora. |

---

## 8. Known shortcomings

- **CUAD ingestion metadata is generic**, not per-document (`review_tool.py`'s own docstring) — the corpus's source attribution is a static string, not a real per-document citation. The length + distance-ratio filters compensate for the resulting fragment noise, but they're a mitigation, not a fix to the underlying ingestion quality.
- **No conversation persistence** — since the backend is stateless and the frontend only holds history in Streamlit's `st.session_state`, a page refresh loses the conversation entirely; there's no database-backed chat history.
- **Retrieval quality depends entirely on embedding model coverage** — `BAAI/bge-small-en` (384-dim) is a small, general-purpose embedding model; highly specialized or unusual legal phrasing may not retrieve well, and there's no fallback keyword search alongside the vector search.
- **`_MAX_DISTANCE_RATIO` and `_MIN_QUALITY_CHARS` are hand-tuned constants** derived empirically from this specific CUAD ingestion (per code comments) — re-ingesting from a differently-formatted source could silently change what counts as a "good enough" match without these thresholds being revisited.
- **No rate limiting or per-user auth** visible in `backend/api/chat.py` — anyone who can reach the API can trigger arbitrary tool calls and spend Groq/Tavily quota.
- **Growing request payloads** — because the full message history travels with every request, very long conversations mean larger and larger payloads and more context sent to the LLM on every turn, with no visible truncation/summarization strategy for old messages.
