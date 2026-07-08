# utils/supabase_client.py
"""
Supabase (Postgres + pgvector) client, replacing ChromaDB.

Design goal: keep agents/review_tool.py, agents/maud_tool.py,
agents/negotiate_tool.py, and agents/draft_tool.py essentially unchanged.
They all call the same shape:

    collection.query(query_embeddings=[emb], n_results=n, include=[...])
    -> {"documents": [[...]], "metadatas": [[...]], "distances": [[...]]}

SupabaseCollection below reproduces that exact return shape, backed by a
`match_<table>(query_embedding, match_count)` Postgres function (see
scripts/supabase_setup.sql) instead of Chroma's HNSW index. `distance` is
cosine distance (lower = more similar), same direction Chroma uses, so the
existing relative distance-ratio filtering in review_tool.py / maud_tool.py
needs no changes.

Requires: psycopg2-binary, pgvector (the Python package, for vector type
adaptation -- `pip install pgvector`).
"""
import threading
from typing import List, Dict, Optional

import numpy as np
from psycopg2 import pool as pg_pool
from pgvector.psycopg2 import register_vector

from config.settings import SUPABASE_DB_URL, TABLES

_lock = threading.Lock()
_connection_pool: Optional[pg_pool.SimpleConnectionPool] = None


def _get_pool() -> pg_pool.SimpleConnectionPool:
    global _connection_pool
    with _lock:
        if _connection_pool is None:
            if not SUPABASE_DB_URL:
                raise RuntimeError(
                    "SUPABASE_DB_URL is not set. Set it to your Supabase direct "
                    "Postgres connection string (see scripts/supabase_setup.sql)."
                )
            _connection_pool = pg_pool.SimpleConnectionPool(1, 10, dsn=SUPABASE_DB_URL)
    return _connection_pool


def _get_conn():
    """Checks out a pooled connection and registers the pgvector type adapter
    on it, as required so psycopg2 knows how to bind numpy arrays to the
    Postgres `vector` column type."""
    pool = _get_pool()
    conn = pool.getconn()
    register_vector(conn)

    with conn.cursor() as cur:
        cur.execute("SET search_path TO public, extensions")

    return pool, conn


class SupabaseCollection:
    """One table + its match_<table> function, addressed the same way a
    Chroma collection was: collection.query(query_embeddings=[emb], n_results=n)."""

    def __init__(self, table_name: str):
        self.table_name = table_name
        self.match_function = f"match_{table_name}"

    def query(self, query_embeddings: List[List[float]], n_results: int = 5, include=None) -> Dict:
        """
        Mirrors chromadb.Collection.query()'s return shape:
        {"documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
        `include` is accepted for interface compatibility but ignored --
        this always returns documents, metadatas, and distances, since the
        migrated agents/*.py callers always want all three.
        """
        if not query_embeddings:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

        embedding = np.asarray(query_embeddings[0], dtype=np.float32)

        pool, conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT document, metadata, distance FROM {self.match_function}(%s, %s)",
                    (embedding, n_results),
                )
                rows = cur.fetchall()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)
            

        documents = [r[0] for r in rows]
        metadatas = [r[1] if r[1] is not None else {} for r in rows]
        distances = [float(r[2]) for r in rows]
        return {"documents": [documents], "metadatas": [metadatas], "distances": [distances]}

    def count(self) -> int:
        pool, conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {self.table_name}")
                return cur.fetchone()[0]
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)


class SupabaseClient:
    """Drop-in replacement for utils.db_client.ChromaClient. connect_all()
    returns the same {"risk": ..., "templates": ..., "playbook": ..., "maud": ...}
    shape agents/*.py already expect -- just backed by Postgres tables instead
    of Chroma persistent collections."""

    def __init__(self):
        self.collections = {}

    def connect_all(self) -> Dict[str, SupabaseCollection]:
        for name, table_name in TABLES.items():
            try:
                self.collections[name] = SupabaseCollection(table_name)
            except Exception as e:
                print(f"Failed to prepare Supabase collection {name}: {e}")
                self.collections[name] = None
        return self.collections

    def search_full_templates(self, embedding, limit: int = 3):
        """Returns full (untruncated) contract template rows for the closest
        matches, via match_contract_templates_full(embedding, match_count).
        Kept as a distinct method (rather than folded into
        SupabaseCollection.query()) since it returns full row tuples
        (id, template_name, template_type, content, metadata), not the
        Chroma-shaped documents/metadatas/distances dict the rest of the
        client mirrors."""
        embedding = np.asarray(embedding, dtype=np.float32)

        pool, conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        template_name,
                        template_type,
                        content,
                        metadata
                    FROM match_contract_templates_full(
                        %s,
                        %s
                    )
                    """,
                    (embedding, limit),
                )
                return cur.fetchall()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool.putconn(conn)