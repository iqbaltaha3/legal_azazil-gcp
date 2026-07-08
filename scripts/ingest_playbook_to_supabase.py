# scripts/ingest_playbook_to_supabase.py
"""
Ingests the synthetic negotiation-playbook JSON directly into Supabase
(negotiation_playbook table) -- no ChromaDB staging step. Same
text-construction logic as the original ingest_playbook.py, just writing
to Postgres instead.

Usage:
    export SUPABASE_DB_URL="postgresql://postgres:[password]@[host]:5432/postgres"
    python scripts/ingest_playbook_to_supabase.py
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sentence_transformers import SentenceTransformer
from config.settings import TABLES, EMBEDDING_MODEL
from utils.supabase_ingest import SupabaseIngestor

WRITE_BATCH_SIZE = 200
REQUIRED_FIELDS = ["counterparty", "industry", "year", "lesson", "batna", "zopa", "fallback", "escalation"]


def main():
    json_path = "data/playbooks/negotiation_playbook_dataset.json"
    if not os.path.exists(json_path):
        alt_path = "data/playbooks/negotiation_playbook_dataset.json"
        if os.path.exists(alt_path):
            json_path = alt_path
        else:
            print(f"❌ File not found at {json_path} or {alt_path}")
            print("   Please place the file in the 'playbooks/' folder or at the root.")
            return

    print(f"📥 Loading playbook entries from: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    print(f"✅ Loaded {len(entries)} entries.")

    ingestor = SupabaseIngestor(TABLES["playbook"])
    ingestor.truncate()

    embedder = SentenceTransformer(EMBEDDING_MODEL)

    total = 0
    pending = []
    for i, entry in enumerate(entries):
        if not all(k in entry for k in REQUIRED_FIELDS):
            print(f"⚠️ Skipping entry {i}: missing fields")
            continue

        text = (
            f"Deal with {entry['counterparty']} ({entry['industry']}, {entry['year']}): "
            f"{entry['lesson']}. "
            f"BATNA: {entry['batna']}. ZOPA: {entry['zopa']}. "
            f"Fallback: {entry['fallback']}. Escalation: {entry['escalation']}."
        )
        emb = embedder.encode(text).tolist()
        meta = {
            "counterparty": entry["counterparty"],
            "industry": entry["industry"],
            "year": entry["year"],
            "lesson": entry["lesson"],
            "batna": entry["batna"],
            "zopa": entry["zopa"],
            "fallback": entry["fallback"],
            "escalation": entry["escalation"],
            "source": "synthetic",
        }

        pending.append((f"synth_{total}", text, meta, emb))
        total += 1

        if len(pending) >= WRITE_BATCH_SIZE:
            ingestor.insert_batch(pending)
            pending = []
            print(f"   Ingested {total} entries")

    if pending:
        ingestor.insert_batch(pending)

    ingestor.reindex()
    ingestor.close()
    print("   You can now query playbook entries by counterparty.")


if __name__ == "__main__":
    main()
