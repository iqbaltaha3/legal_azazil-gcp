# utils/metrics.py
"""
Structured logging of LLM-specific metrics, so the Metrics tab has real
data to show instead of guesses.

Previously backed by a local SQLite file (metrics.db). On Cloud Run the
container filesystem is ephemeral and each instance gets its own disk, so
a local file (a) doesn't survive instance churn/redeploys and (b) isn't
shared across concurrently running instances -- the Metrics tab would only
ever show a fraction of real activity. This now writes to two Postgres
tables (llm_calls, tool_calls) in the same Supabase database everything
else already uses, via the same connection pool utils/supabase_client.py
maintains, so metrics are durable and consistent across all instances.

Public API is unchanged -- same function names, same signatures, same
return shapes -- so utils/llm_client.py, agents/orchestrator_graph.py, and
backend/api/metrics.py require no changes.
"""
from datetime import datetime, timezone

from utils.supabase_client import _get_pool

_TABLES_READY = False


def _connect():
    pool = _get_pool()
    conn = pool.getconn()
    return pool, conn


def init_metrics_db():
    """Idempotent -- safe to call on every import, same as the old sqlite
    version. Wrapped in try/except so a transient Supabase connectivity
    blip during container cold start logs a warning instead of crashing
    the whole app at import time (utils/llm_client.py imports this
    module)."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TEXT,
                    caller TEXT,
                    model TEXT,
                    mode TEXT,
                    latency_ms INTEGER,
                    prompt_chars INTEGER,
                    completion_chars INTEGER,
                    est_prompt_tokens INTEGER,
                    est_completion_tokens INTEGER,
                    temperature REAL,
                    tool_calls_returned INTEGER,
                    success INTEGER,
                    error TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TEXT,
                    tool_name TEXT,
                    latency_ms INTEGER,
                    success INTEGER,
                    error TEXT
                )
            """)
        conn.commit()
        _TABLES_READY = True
    except Exception as e:
        conn.rollback()
        print(f"[utils.metrics] Failed to initialize metrics tables: {e}")
    finally:
        pool.putconn(conn)


def log_llm_call(caller, model, mode, latency_ms, prompt_chars, completion_chars,
                  temperature, tool_calls_returned=0, success=True, error=None,
                  prompt_tokens=0, completion_tokens=0):
    est_prompt = prompt_tokens or int(prompt_chars / 4)
    est_completion = completion_tokens or int(completion_chars / 4)
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO llm_calls
                (timestamp, caller, model, mode, latency_ms, prompt_chars, completion_chars,
                 est_prompt_tokens, est_completion_tokens, temperature, tool_calls_returned, success, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                datetime.now(timezone.utc).isoformat(), caller, model, mode, latency_ms,
                prompt_chars, completion_chars,
                est_prompt, est_completion,
                temperature, tool_calls_returned, int(success), error,
            ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[utils.metrics] Failed to log LLM call: {e}")
    finally:
        pool.putconn(conn)


def log_tool_call(tool_name, latency_ms, success=True, error=None):
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO tool_calls (timestamp, tool_name, latency_ms, success, error)
                VALUES (%s, %s, %s, %s, %s)
            """, (datetime.now(timezone.utc).isoformat(), tool_name, latency_ms, int(success), error))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[utils.metrics] Failed to log tool call: {e}")
    finally:
        pool.putconn(conn)


def get_llm_call_summary():
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT COUNT(*), AVG(latency_ms), SUM(est_prompt_tokens), SUM(est_completion_tokens),
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)
                FROM llm_calls
            """)
            total, avg_latency, prompt_tokens, completion_tokens, errors = c.fetchone()
    finally:
        pool.putconn(conn)
    return {
        "total_calls": total or 0,
        "avg_latency_ms": round(float(avg_latency or 0), 1),
        "total_prompt_tokens": prompt_tokens or 0,
        "total_completion_tokens": completion_tokens or 0,
        "errors": errors or 0,
    }


def get_tool_call_summary():
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT tool_name, COUNT(*), AVG(latency_ms),
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)
                FROM tool_calls
                GROUP BY tool_name
            """)
            rows = c.fetchall()
    finally:
        pool.putconn(conn)
    return [
        {"tool": r[0], "calls": r[1], "avg_latency_ms": round(float(r[2] or 0), 1), "errors": r[3]}
        for r in rows
    ]


def get_recent_llm_calls(limit=50):
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT timestamp, caller, model, mode, latency_ms,
                       est_prompt_tokens, est_completion_tokens, tool_calls_returned, success, error
                FROM llm_calls
                ORDER BY id DESC LIMIT %s
            """, (limit,))
            rows = c.fetchall()
    finally:
        pool.putconn(conn)
    cols = ["timestamp", "caller", "model", "mode", "latency_ms",
            "prompt_tokens", "completion_tokens", "tool_calls_returned", "success", "error"]
    return [dict(zip(cols, r)) for r in rows]


def get_recent_tool_calls(limit=50):
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT timestamp, tool_name, latency_ms, success, error
                FROM tool_calls
                ORDER BY id DESC LIMIT %s
            """, (limit,))
            rows = c.fetchall()
    finally:
        pool.putconn(conn)
    cols = ["timestamp", "tool_name", "latency_ms", "success", "error"]
    return [dict(zip(cols, r)) for r in rows]


def get_latency_over_time(limit=200):
    pool, conn = _connect()
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT timestamp, latency_ms FROM llm_calls ORDER BY id DESC LIMIT %s
            """, (limit,))
            rows = c.fetchall()
    finally:
        pool.putconn(conn)
    return list(reversed(rows))  # chronological order for charting


init_metrics_db()