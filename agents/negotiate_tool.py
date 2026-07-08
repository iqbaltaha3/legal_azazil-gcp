# agents/negotiate_tool.py
"""
Negotiation playbook lookup. NOT exposed as a standalone LLM tool -- this is
auto-chained by review_tool.py for any clause flagged Medium/High severity,
so a review always comes with "what do I actually do about this" guidance
instead of leaving the user to figure out next steps themselves.

The playbook DB (see scripts/ingest_playbook.py) holds synthetic
counterparty-negotiation lessons, each with BATNA / ZOPA / fallback /
escalation fields. Retrieval is by semantic similarity between the flagged
clause text and the lesson text, same pattern as review_tool/draft_tool.

The LLM's guidance references "Lesson N", which is only verifiable if the
underlying retrieved lesson (counterparty, industry, year, and the actual
BATNA/ZOPA/fallback/escalation fields) is shown alongside it -- so every
guidance string is followed by that raw evidence, not just the LLM's prose.
"""
from typing import List, Dict, Optional
from utils.embedder import Embedder
from utils.supabase_client import SupabaseClient
from utils.llm_client import GroqClient
from config.settings import TOP_K_PLAYBOOK

_embedder = Embedder()
_llm = GroqClient()
_db_client = SupabaseClient()
_collections = _db_client.connect_all()


def _retrieve_playbook_entries(clause_text: str, n_results: int = TOP_K_PLAYBOOK) -> List[Dict]:
    collection = _collections.get("playbook")
    if collection is None:
        return []
    try:
        emb = _embedder.encode(clause_text)
        results = collection.query(query_embeddings=[emb], n_results=n_results)
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        return [{"doc": d, "metadata": m} for d, m in zip(docs, metas)]
    except Exception as e:
        print(f"Playbook retrieval error: {e}")
        return []


def _format_evidence_block(entries: List[Dict]) -> str:
    """
    Renders the actual retrieved playbook lessons with their real
    counterparty/industry/year/BATNA/ZOPA/fallback/escalation fields, so
    "Lesson N" in the LLM's guidance is traceable to a real past deal instead
    of being an unverifiable citation.
    """
    if not entries:
        return ""
    lines = ["  Retrieved negotiation lessons (what the model actually drew on):"]
    for i, e in enumerate(entries):
        m = e["metadata"]
        lines.append(
            f"  [Lesson {i+1}] counterparty: {m.get('counterparty', 'unknown')} | "
            f"industry: {m.get('industry', 'unknown')} | year: {m.get('year', 'unknown')}\n"
            f"      Lesson: {m.get('lesson', '')}\n"
            f"      BATNA: {m.get('batna', '')}\n"
            f"      ZOPA: {m.get('zopa', '')}\n"
            f"      Fallback: {m.get('fallback', '')}\n"
            f"      Escalation: {m.get('escalation', '')}"
        )
    return "\n".join(lines)


def suggest_negotiation(clause_title: str, clause_text: str, finding_text: str) -> Optional[str]:
    """
    Returns a short negotiation-guidance string (with a trailing evidence
    block of the real retrieved lessons) for a flagged clause, or None if no
    comparable playbook lessons were found (caller should skip silently
    rather than let the LLM invent generic advice with no grounding).
    """
    entries = _retrieve_playbook_entries(clause_text)
    if not entries:
        return None

    lessons_block = "\n\n".join(
        f"Lesson {i+1} (counterparty: {e['metadata'].get('counterparty', 'unknown')}, "
        f"industry: {e['metadata'].get('industry', 'unknown')}, "
        f"year: {e['metadata'].get('year', 'unknown')}):\n"
        f"  Lesson: {e['metadata'].get('lesson', '')}\n"
        f"  BATNA: {e['metadata'].get('batna', '')}\n"
        f"  ZOPA: {e['metadata'].get('zopa', '')}\n"
        f"  Fallback: {e['metadata'].get('fallback', '')}\n"
        f"  Escalation: {e['metadata'].get('escalation', '')}"
        for i, e in enumerate(entries)
    )

    prompt = f"""You are a contract negotiation advisor. A clause in the user's contract
was flagged as non-standard/one-sided. Using the retrieved negotiation lessons from
comparable past deals below, recommend how to negotiate this clause.

FLAGGED CLAUSE ({clause_title}):
{clause_text[:1500]}

REVIEW FINDING:
{finding_text}

COMPARABLE NEGOTIATION LESSONS:
{lessons_block}

Task:
- Recommend a concrete fallback position and/or redline for this clause.
- You MUST cite which Lesson number(s) your recommendation draws on.
- Briefly note the escalation path if the counterparty won't move.
- Keep it to 3-4 sentences. Do not repeat the review finding, just give guidance.

Output format:
Negotiation Guidance: <your recommendation citing Lesson N>
"""
    result = _llm.generate(prompt, max_tokens=300, temperature=0.3, caller="negotiate_tool.suggest")
    evidence = _format_evidence_block(entries)
    return f"{result.strip()}\n{evidence}"
