# Azazil Legal AI

A conversational legal assistant, split into a FastAPI backend and a
Streamlit frontend, backed by Supabase (Postgres + pgvector). One LangGraph
orchestrator LLM chooses between four tools, each grounded in a real
contract/deal database -- never plain LLM opinion:

- **review_contract** -- chunks the user's contract into clauses, retrieves
  comparable clauses from CUAD (real SEC-filed contracts, `legal_risks`
  table) per clause, and asks the LLM to flag non-standard/one-sided clauses
  with cited comparisons and a severity rating. Any clause flagged Medium or
  High severity automatically also gets **negotiation guidance** (a fallback
  position / redline suggestion), retrieved from a negotiation-playbook
  database of past deal lessons (BATNA / ZOPA / fallback / escalation,
  `negotiation_playbook` table) and grounded the same way.
- **benchmark_ma_provision** -- for merger & acquisition documents
  specifically (termination fees, no-shop clauses, fiduciary-outs, MAC-outs,
  closing conditions). Retrieves comparable provisions from MAUD (real
  merger agreements, `maud_clauses` table) and reports whether a provision
  is buyer-favorable, seller-favorable, or market-standard, with cited
  comparisons. Distinct from `review_contract` because M&A "market standard"
  is usually a range, not a single right answer, and the source corpus is
  M&A-specific rather than general commercial contracts. Like
  `review_contract`, Medium/High-deviation provisions also get negotiation
  guidance auto-attached.
- **draft_contract** -- retrieves comparable templates (`contract_templates`
  table) and drafts (or revises) a contract grounded in them.
- **web_search** -- Tavily search for anything outside the corpus
  (statutes, case law, general questions).

No fixed pipeline, no intent-classifier node, no multi-query expansion, no
RRF fusion, no reranker. Intent is resolved by the LLM's own tool selection.

## Architecture

```
app/chat_app.py  (Streamlit)
      |  HTTP (httpx)
      v
backend/main.py  (FastAPI)
  backend/api/chat.py     -- POST /api/chat
  backend/api/metrics.py  -- GET  /api/metrics/*
      |
      v
agents/orchestrator_graph.py  (LangGraph agent loop, picks a tool per turn)
      |
      +--> review_contract ---------> negotiate_tool (auto, if Medium/High severity)
      |         |                          |
      |         v                          v
      |     legal_risks (CUAD)      negotiation_playbook
      |
      +--> benchmark_ma_provision -> maud_clauses (MAUD)
      |         |                          |
      |         v                          v
      |     (also auto-chains)      negotiation_playbook
      |
      +--> draft_contract ---------> contract_templates
      |
      +--> web_search --------------> Tavily

utils/supabase_client.py -- read-only vector search (agents use this)
utils/supabase_ingest.py -- write/truncate (only scripts/ingest_*.py use this)
```

The frontend and backend are separate processes: the Streamlit app holds
conversation state (`st.session_state`) and calls the backend over HTTP; the
backend itself is stateless per-request -- it receives the full message
history each turn and returns the updated history, so there's no
server-side session store to manage.

## Setup

### 1. Supabase (Postgres + pgvector)

Create a Supabase project, then run `scripts/supabase_setup.sql` in its SQL
editor. This creates the `vector` extension, the four tables
(`legal_risks`, `contract_templates`, `negotiation_playbook`,
`maud_clauses`), their `ivfflat` indexes, and one `match_<table>` function
per table used for similarity search.

If you hit `psycopg2.ProgrammingError: vector type not found in the
database` later: Supabase installs extensions into an `extensions` schema,
not `public`. Run once in the SQL editor:
```sql
alter database postgres set search_path to public, extensions;
```
(The code also sets `search_path` per-connection as a safety net, so this
should self-heal even without the ALTER, but the ALTER is the cleaner
permanent fix.)

### 2. Ingest your data

```bash
pip install -r requirements-ingest.txt
export SUPABASE_DB_URL="postgresql://postgres:[password]@[host]:5432/postgres"

python scripts/ingest_cuad_to_supabase.py       # expects data/cuad/**/*.txt
python scripts/ingest_maud_to_supabase.py       # expects maud/contract_*.txt
python scripts/ingest_playbook_to_supabase.py   # expects playbooks/negotiation_playbook_dataset.json
python scripts/ingest_templates_to_supabase.py  # expects templates/**/*.{docx,txt,md}
```
Each script truncates its own table before inserting, so re-running one is
always safe and won't duplicate rows or affect the other three tables.
Adjust the hardcoded `root_folder` path at the top of a script if your data
lives somewhere else (e.g. under `data/`).

### 3. Backend

```bash
pip install -r requirements-backend.txt
export GROQ_API_KEY=...
export GROQ_MODEL=llama-3.3-70b-versatile   # optional, this is the default
export TAVILY_API_KEY=...
export SUPABASE_DB_URL=...

uvicorn backend.main:app --reload --port 8000
```

### 4. Frontend

```bash
pip install -r requirements-frontend.txt
export BACKEND_URL=http://localhost:8000   # optional, this is the default

streamlit run app/chat_app.py
```

A `.env.example` is included with all of the above variables -- copy it to
`.env` and fill in values; `config/settings.py` loads it automatically via
`python-dotenv`.

## Files

```
config/settings.py            -- SUPABASE_DB_URL, TABLES, model name, retrieval K values
utils/embedder.py              -- sentence-transformers wrapper (BAAI/bge-small-en, 384-dim)
utils/chunking.py              -- splits contract text into clauses by legal headers
utils/supabase_client.py       -- READ-ONLY: vector search, used by agents/*.py at query time
utils/supabase_ingest.py       -- WRITE: truncate/insert, used only by scripts/ingest_*.py
utils/llm_client.py            -- Groq API client (OpenAI-compatible), incl. chat_with_tools()
utils/metrics.py               -- SQLite logging of every LLM/tool call
agents/review_tool.py          -- review_contract; auto-chains negotiate_tool
agents/maud_tool.py            -- benchmark_ma_provision; auto-chains negotiate_tool
agents/negotiate_tool.py       -- internal-only: playbook-grounded fallback/redline guidance
agents/draft_tool.py           -- draft_contract implementation
agents/search_tool.py          -- web_search (Tavily) implementation
agents/orchestrator_graph.py   -- LangGraph agent loop wiring the four tools together
backend/main.py                -- FastAPI app
backend/api/chat.py            -- POST /api/chat
backend/api/metrics.py         -- GET /api/metrics/*
app/chat_app.py                -- Streamlit UI (Chat / How this works / Metrics tabs), HTTP client only
scripts/supabase_setup.sql     -- one-time: extension, tables, indexes, match functions
scripts/ingest_*_to_supabase.py -- one per data source, writes directly to Supabase
```

## Example inputs, by tool

**review_contract** (general commercial contract):
```
Can you review this NDA for one-sided clauses?
[paste a general contract -- NDA, consulting agreement, employment agreement, etc.]
```

**benchmark_ma_provision** (M&A-specific -- use words like "merger agreement",
"termination fee", "no-shop", "fiduciary-out", "MAC clause"):
```
Is this termination fee provision in line with market practice?
Section 8.3 Termination Fee. ... Company shall pay Parent a termination fee
equal to $15,000,000, representing approximately 4.2% of the aggregate
merger consideration.
```

**draft_contract**:
```
Draft a consulting agreement between Acme Corp and Jane Doe, 12-month term.
```
or, chained after a review:
```
Yes, please draft a revised version fixing the flagged clauses.
```

**web_search**:
```
Is there recent case law on non-compete enforceability in California?
```

**No tool** (answered directly, no retrieval):
```
What does "force majeure" mean?
```

To check routing directly without the UI:
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"history": [], "message": "Is a 3.5% termination fee typical for a merger agreement this size?"}' \
  | jq .tool_used
# -> "benchmark_ma_provision"
```

## Metrics

Every LLM call (`generate` and `chat_with_tools`) and every tool execution is
logged to `metrics.db` (SQLite, on the backend) via `utils/metrics.py`:
timestamp, caller, model, latency, estimated prompt/completion tokens,
temperature, tool-calls-returned count, success/error. The Streamlit
**Metrics** tab reads this through `GET /api/metrics/*` on the backend --
it no longer touches the sqlite file directly.

Token counts come from Groq's `response.usage` (real counts) when the API
returns them; otherwise a rough `chars / 4` estimate fills the gap so the
columns are never empty.

## Notes

- `review_contract`, `benchmark_ma_provision`, and `draft_contract` are
  plain Python functions -- testable independently of the LLM, the graph,
  or the HTTP layer.
- `orchestrator_graph.py`'s `SYSTEM_PROMPT` is the main "personality"
  control point: which tool fits which document type, always offering to
  draft after a review, passing `source_contract` back in on revision. Tune
  this first if routing behavior needs adjusting.
- Retrieval in `review_tool.py`/`maud_tool.py` filters out short/fragment
  chunks (headers, TOC lines, bare dates -- under 120 chars) and drops
  results whose distance is notably worse than the best match for the same
  query, before anything reaches the LLM. If a clause/provision has nothing
  genuinely comparable, the tool says so explicitly rather than forcing a
  citation -- keep it that way; silent fallback to ungrounded LLM opinion is
  what caused the original accuracy complaints on this project.
- Evidence blocks (the actual retrieved text + source metadata under each
  finding) only show comparisons the LLM's own finding text cites by
  number, not every comparison retrieved -- keeps the report from padding
  out with fragments that had no bearing on the conclusion.
- `negotiate_tool.py` is intentionally **not** exposed as a standalone
  LLM-selectable tool -- it's auto-chained by both `review_contract` and
  `benchmark_ma_provision` so negotiation guidance is never missing from a
  flagged finding, and never invoked without a specific clause/provision
  and finding to ground it.
- `utils/supabase_ingest.py` (write-capable) is only ever imported by
  `scripts/ingest_*.py`. `agents/*.py` only imports `utils/supabase_client.py`
  (read-only), so nothing in the running app can accidentally truncate or
  write to these tables.