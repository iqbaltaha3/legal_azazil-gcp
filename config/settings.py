# config/settings.py -- environment variables and constants
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
VECTOR_STORE_DIR = BASE_DIR / "vector_stores"  # bring your existing vector_stores folder here

# --- Supabase (Postgres + pgvector) -- primary vector store after the
# ChromaDB -> Supabase migration (see scripts/supabase_setup.sql and
# scripts/migrate_chroma_to_supabase.py). ---
load_dotenv()

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "")

# Table names in Postgres. Also doubles as the historical Chroma collection
# name (see CHROMA_COLLECTIONS below) -- kept identical on purpose so the
# migration script's source/destination names line up 1:1.
TABLES = {
    "risk": "legal_risks",           # CUAD clauses -- review tool
    "templates": "contract_templates",  # NDA/contract templates -- draft tool
    "playbook": "negotiation_playbook",  # synthetic negotiation lessons -- negotiate_tool
    "maud": "maud_clauses",           # M&A agreement chunks -- maud_tool
}

# --- ChromaDB -- retained ONLY for scripts/migrate_chroma_to_supabase.py to
# read your existing on-disk vector_stores/ folder one last time. Nothing in
# agents/*.py or utils/supabase_client.py uses these at runtime anymore. ---
CHROMA_DB_PATHS = {
    "risk": str(VECTOR_STORE_DIR / "legal_risk_db"),
    "templates": str(VECTOR_STORE_DIR / "contract_templates_db"),
    "playbook": str(VECTOR_STORE_DIR / "negotiation_playbook_db"),
    "maud": str(VECTOR_STORE_DIR / "maud_db"),
}
CHROMA_COLLECTIONS = TABLES

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
EMBEDDING_MODEL = "BAAI/bge-small-en"

# Retrieval
TOP_K_PER_CLAUSE = 3
TOP_K_PLAYBOOK = 3   # negotiation lessons retrieved per flagged clause
TOP_K_MAUD = 5        # comparable MAUD chunks retrieved per M&A provision/section

# Only auto-suggest negotiation guidance for clauses at/above this severity
NEGOTIATION_TRIGGER_SEVERITIES = {"high", "medium"}

# Tavily
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
