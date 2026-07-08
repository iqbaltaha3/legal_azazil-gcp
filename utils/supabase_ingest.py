# utils/supabase_ingest.py
"""
Shared write-side helper for the four ingest_*_to_supabase.py scripts.
Complements utils/supabase_client.py (which is read-only, used by the
agents at query time) with the insert/truncate operations ingestion needs.

Kept separate from supabase_client.py on purpose: agents/*.py should never
be able to accidentally truncate or write to these tables, so the
write-capable code path only exists here, only imported by scripts/.
"""
import json
from typing import List, Tuple, Dict, Optional

import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values

from config.settings import SUPABASE_DB_URL

# BAAI/bge-small-en (see config/settings.py EMBEDDING_MODEL) outputs
# 384-dim vectors -- must match the vector(384) column in supabase_setup.sql.
EXPECTED_EMBEDDING_DIM = 384


class SupabaseIngestor:
    """One Postgres table, opened for writing. Use as:

        ingestor = SupabaseIngestor("legal_risks")
        ingestor.truncate()
        ingestor.insert_batch(rows)   # call repeatedly as you generate chunks
        ingestor.reindex()
        ingestor.close()
    """

    def __init__(self, table_name: str):
        if not SUPABASE_DB_URL:
            raise RuntimeError(
                "SUPABASE_DB_URL is not set. Set it to your Supabase direct "
                "Postgres connection string (see scripts/supabase_setup.sql)."
            )
        self.table_name = table_name
        self.conn = psycopg2.connect(SUPABASE_DB_URL)
        # Supabase installs extensions (including pgvector) into an
        # "extensions" schema, not "public" -- without this, register_vector()
        # fails with "vector type not found" even though the extension is enabled.
        with self.conn.cursor() as cur:
            cur.execute("SET search_path TO public, extensions")
        register_vector(self.conn)
        self._inserted = 0
        self._skipped_bad_dim = 0

    def truncate(self):
        with self.conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {self.table_name} RESTART IDENTITY")
        self.conn.commit()
        print(f"🧹 Truncated {self.table_name} (fresh import).")

    def insert_batch(self, rows: List[Tuple[str, str, Optional[Dict], List[float]]]):
        """
        rows: list of (legacy_id, document, metadata_dict_or_none, embedding)
        Validates embedding dimension per-row so one malformed row can't
        abort the whole batch; bad rows are skipped and logged.
        """
        clean_rows = []
        for legacy_id, document, metadata, embedding in rows:
            if embedding is None or len(embedding) != EXPECTED_EMBEDDING_DIM:
                self._skipped_bad_dim += 1
                dim = len(embedding) if embedding is not None else "None"
                print(f"  WARNING: skipping {legacy_id} -- embedding dim {dim} != {EXPECTED_EMBEDDING_DIM}")
                continue
            clean_rows.append((legacy_id, document, json.dumps(metadata or {}), list(embedding)))

        if not clean_rows:
            return

        try:
            with self.conn.cursor() as cur:
                execute_values(
                    cur,
                    f"INSERT INTO {self.table_name} (chroma_id, document, metadata, embedding) VALUES %s",
                    clean_rows,
                    template="(%s, %s, %s::jsonb, %s::vector)",
                )
            self.conn.commit()
            self._inserted += len(clean_rows)
        except Exception as e:
            self.conn.rollback()
            print(f"  WARNING: batch of {len(clean_rows)} rows failed and was skipped: {e}")

    def reindex(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute(f"REINDEX INDEX {self.table_name}_embedding_idx")
            self.conn.commit()
            print(f"  Reindexed {self.table_name}_embedding_idx.")
        except Exception as e:
            self.conn.rollback()
            print(f"  WARNING: could not reindex {self.table_name}_embedding_idx: {e}")

    def close(self):
        print(f"✅ Done! Inserted {self._inserted} rows into {self.table_name}"
              + (f" ({self._skipped_bad_dim} skipped for bad embedding dim)." if self._skipped_bad_dim else "."))
        self.conn.close()
