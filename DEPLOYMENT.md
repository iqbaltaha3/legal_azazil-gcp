# Deployment Guide — Azazil Legal AI

This document covers deploying the existing app (unchanged behavior) to:

- **Backend (FastAPI)** → Google Cloud Run
- **Frontend (Streamlit)** → Streamlit Cloud
- **Database / Vector store** → Supabase (pgvector) — already configured
- **LLM** → Groq API
- **Embeddings** → SentenceTransformer, local, CPU

Nothing about the orchestrator, tools, retrieval logic, prompts, LangGraph
flow, API routes, Streamlit UI, metrics, or database schema was changed.
The only additions are deployment plumbing: `Dockerfile`, `.dockerignore`,
and a small addition to `requirements-backend.txt` (CPU-only PyTorch index).

## What was added/changed for deployment

| File | Status | Why |
|---|---|---|
| `Dockerfile` | **new** | Builds the FastAPI backend for Cloud Run. Installs `requirements-backend.txt`, copies only `agents/`, `backend/`, `config/`, `utils/` (the only packages the backend imports), and runs `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`. |
| `.dockerignore` | **new** | Keeps `.env`, `.git`, venvs, and frontend/ingestion-only files out of the image and build context. |
| `requirements-backend.txt` | **modified** | Added one line, `--extra-index-url https://download.pytorch.org/whl/cpu`, so `pip install torch==2.5.1` pulls the CPU-only wheel instead of PyPI's default CUDA build. Same torch version, same CPU device (`utils/embedder.py` already hardcodes `device="cpu"`) — this only avoids shipping multiple unnecessary GBs of unused NVIDIA libraries in the container. |

Everything else in the repo is exactly as it was.

## Prerequisites

- A Google Cloud project with billing enabled
- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- Your Supabase project already set up (tables + `match_*` functions from
  `scripts/supabase_setup.sql`) — unchanged, still required
- API keys: `GROQ_API_KEY`, `TAVILY_API_KEY`, and your Supabase
  **direct** connection string (`SUPABASE_DB_URL`)

## 1. Deploy the backend to Cloud Run

From the project root (this directory):

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# Build the image (Cloud Build has full internet access, so the
# PyTorch CPU wheel index resolves fine even though it isn't on
# the default PyPI index)
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/azazil-backend

# Deploy
gcloud run deploy azazil-backend \
  --image gcr.io/YOUR_PROJECT_ID/azazil-backend \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --port 8080 \
  --set-env-vars GROQ_MODEL=llama-3.3-70b-versatile \
  --set-env-vars SUPABASE_DB_URL="postgresql://postgres:PASSWORD@YOUR_SUPABASE_HOST:5432/postgres" \
  --set-env-vars GROQ_API_KEY="your-groq-key" \
  --set-env-vars TAVILY_API_KEY="your-tavily-key"
```

Get the deployed URL:

```bash
gcloud run services describe azazil-backend --region us-central1 --format='value(status.url)'
```

Verify it's alive:

```bash
curl https://YOUR-SERVICE-URL/health
# {"status":"ok"}
```

> For production, prefer Secret Manager over plaintext `--set-env-vars` for
> `GROQ_API_KEY`, `TAVILY_API_KEY`, and `SUPABASE_DB_URL`
> (`gcloud secrets create ...` + `--set-secrets` on the deploy command).
> Either way works with the app unchanged, since it just reads
> `os.getenv(...)`.

## 2. Deploy the frontend to Streamlit Cloud

1. Push this repo (minus `.env`, which is already git-ignored) to GitHub.
2. In Streamlit Cloud, create a new app pointing at `app/chat_app.py`.
3. Set the requirements file to `requirements-frontend.txt`.
4. Under **Settings → Secrets**, add:

   ```
   BACKEND_URL = "https://YOUR-SERVICE-URL"
   BACKEND_TIMEOUT_SECONDS = "120"
   ```

That's it — `app/chat_app.py` already reads `BACKEND_URL` via
`os.getenv`, so no code changes are needed on the frontend side.

## 3. Local development (unchanged)

```bash
pip install -r requirements-backend.txt -r requirements-frontend.txt
uvicorn backend.main:app --reload --port 8000
# in a second terminal
streamlit run app/chat_app.py
```

`.env` in the project root is still picked up locally via
`python-dotenv` (`config/settings.py`), exactly as before.

## Security note

The included `.env` contains live-looking API keys and a Supabase DB
password in plaintext. `.dockerignore` keeps it out of the container image,
and `.gitignore` keeps it out of git — but if this repo has been shared or
committed anywhere with `.env` included, rotate the Groq key, Tavily key,
and Supabase DB password before going further.
