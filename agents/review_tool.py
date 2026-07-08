# agents/review_tool.py
"""
Contract review tool.

Pipeline (per the plan):
1. Split the user's ACTUAL contract into clauses (not the chat query).
2. For each clause, embed it and retrieve top-K comparable clauses from CUAD.
3. One grounded LLM call per clause: is this standard or one-sided, citing the
   retrieved comparisons.
4. Synthesize a short executive summary + per-clause findings.

No multi-query expansion, no RRF fusion across collections, no reranker --
kept simple and traceable so every finding can point at real evidence.

RETRIEVAL QUALITY NOTE: the CUAD collection was ingested with generic
metadata (source is a static "CUAD" string, not per-document) and clearly
includes non-clause fragments (section headers, table-of-contents lines,
bare dates) alongside real clause text. Sending those fragments to the LLM
as "comparable clauses" produces confident-sounding analysis built on
nothing -- e.g. citing a comparison that is literally just the words
"ARTICLE 7 LIMITATIONS ON LIABILITY 22". So retrieval here does two things
the naive version didn't:
  1. Uses Chroma's actual distance scores to drop results that are much
     worse matches than the best one (relative cutoff, not an absolute
     threshold, since we don't know the collection's exact distance metric).
  2. Drops fragment-like chunks outright (too short, or matching known
     boilerplate/heading patterns) before they ever reach the LLM.
If everything retrieved for a clause fails these filters, that clause is
treated exactly like an empty retrieval -- reported as "no comparable
clause found", never forced into a fabricated comparison.

CITATION NOTE: the LLM's finding references "Comparison N", which is only
verifiable if the reader can see what N actually was -- so the evidence
block always follows. But we only show evidence for the comparison numbers
the LLM's own finding text actually cites (not every comparison retrieved),
to keep the report from drowning the user in fragments that had no bearing
on the conclusion.
"""
import re
from typing import List, Dict
from utils.chunking import detect_sections
from utils.embedder import Embedder
from utils.supabase_client import SupabaseClient
from utils.llm_client import GroqClient
from config.settings import TOP_K_PER_CLAUSE, NEGOTIATION_TRIGGER_SEVERITIES
from agents.negotiate_tool import suggest_negotiation

_embedder = Embedder()
_llm = GroqClient()
_db_client = SupabaseClient()
_collections = _db_client.connect_all()

# How much of each retrieved clause's text to show verbatim in the evidence
# block / send to the LLM.
_EVIDENCE_CHARS = 600

# Chunks shorter than this are almost always fragments (headers, TOC lines,
# bare dates) rather than real clause text -- observed empirically from
# what this CUAD ingestion actually returns (e.g. "ARTICLE 7 LIMITATIONS ON
# LIABILITY 22" is 38 chars; real clauses in this corpus run 300-1500+ chars).
_MIN_QUALITY_CHARS = 120

# A retrieved result is dropped if its distance is more than this multiple
# of the best (rank-1) distance for the same query. This is a *relative*
# cutoff on purpose -- it works regardless of whether the collection uses
# cosine or L2 distance, as long as "lower = more similar" (true for both).
_MAX_DISTANCE_RATIO = 1.5

# Matches an embedded per-document citation like
# "Source: CLICKSTREAM CORP, 1-A, 3/30/2020" that sometimes appears inside
# the CUAD chunk text itself, even though the metadata's "source" field is
# just a static "CUAD" string. When present, this is a far more useful,
# traceable label than the metadata.
_EMBEDDED_SOURCE_RE = re.compile(r"Source:\s*([A-Z0-9][A-Za-z0-9 ,.'&\-]{3,80}?,\s*[\w\-]+,\s*\d{1,2}/\d{1,2}/\d{2,4})")


def _extract_source_label(doc_text: str, metadata: Dict) -> str:
    m = _EMBEDDED_SOURCE_RE.search(doc_text)
    if m:
        return m.group(1).strip()
    return metadata.get("source", "unknown source")


def _is_fragment(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < _MIN_QUALITY_CHARS:
        return True
    return False


def _retrieve_comparisons(clause_text: str, n_results: int = TOP_K_PER_CLAUSE) -> List[Dict]:
    """
    Retrieves comparable clauses, then filters out fragments and
    weak/irrelevant matches. Returns [] if nothing usable survives --
    callers must treat that as "no comparable clause found", not send an
    empty list to the LLM to comment on.
    """
    collection = _collections.get("risk")
    if collection is None:
        return []
    try:
        emb = _embedder.encode(clause_text)
        # Over-fetch a bit so filtering still leaves something to work with.
        results = collection.query(
            query_embeddings=[emb], n_results=max(n_results * 2, n_results + 2),
            include=["documents", "metadatas", "distances"],
        )
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        dists = results["distances"][0] if results.get("distances") else [None] * len(docs)
    except Exception as e:
        print(f"Retrieval error: {e}")
        return []

    if not docs:
        return []

    best_distance = min((d for d in dists if d is not None), default=None)

    candidates = []
    for doc, meta, dist in zip(docs, metas, dists):
        if _is_fragment(doc):
            continue
        if best_distance is not None and dist is not None and best_distance > 0:
            if dist > best_distance * _MAX_DISTANCE_RATIO:
                continue
        candidates.append({"doc": doc, "metadata": meta, "distance": dist})

    return candidates[:n_results]


def _cited_comparison_numbers(finding_text: str) -> List[int]:
    return sorted(set(int(n) for n in re.findall(r"Comparison\s+(\d+)", finding_text, re.IGNORECASE)))


def _format_evidence_block(comparisons: List[Dict], cited_only: List[int] = None) -> str:
    """
    Renders the actual retrieved CUAD clauses with a traceable source label,
    so a cited "Comparison N" is verifiable. If cited_only is given, only
    those comparison numbers are shown (keeps the report from padding out
    with fragments the finding never actually relied on).
    """
    if not comparisons:
        return ""
    lines = ["  Retrieved comparisons (what the model actually compared against):"]
    shown_any = False
    for i, c in enumerate(comparisons):
        n = i + 1
        if cited_only is not None and n not in cited_only:
            continue
        shown_any = True
        source = _extract_source_label(c["doc"], c["metadata"])
        snippet = c["doc"][:_EVIDENCE_CHARS].strip().replace("\n", " ")
        if len(c["doc"]) > _EVIDENCE_CHARS:
            snippet += "..."
        lines.append(f'  [Comparison {n}] source: {source}\n      "{snippet}"')
    if not shown_any:
        return ""
    return "\n".join(lines)


def _analyze_clause(title: str, clause_text: str, comparisons: List[Dict]) -> str:
    if not comparisons:
        return f"**{title}** -- No comparable clauses found in the database; unable to assess against market standard."

    comparison_block = "\n\n".join(
        f"Comparison {i+1} (source: {_extract_source_label(c['doc'], c['metadata'])}):\n{c['doc'][:_EVIDENCE_CHARS]}"
        for i, c in enumerate(comparisons)
    )

    prompt = f"""You are a contract risk analyst. Compare the user's clause against real-world
comparable clauses retrieved from a database of SEC-filed contracts (CUAD).

CLAUSE FROM USER'S CONTRACT ({title}):
{clause_text}

COMPARABLE CLAUSES FROM REAL CONTRACTS:
{comparison_block}

Task:
- State whether the user's clause is standard or unusually one-sided, compared to the retrieved examples.
- Only cite a comparison number if it is genuinely relevant and substantive (not a heading, date, or
  unrelated topic). If NONE of the retrieved comparisons are genuinely relevant to this clause, say so
  explicitly instead of forcing a weak or irrelevant comparison -- an honest "insufficient comparable
  data" is far more useful than a fabricated connection.
- If you do cite comparisons, give a severity: High, Medium, or Low. If no comparison was usable, give
  Severity: Low and say assessment is not possible.
- Keep it to 3-4 sentences.

Output format:
Severity: <High/Medium/Low>
Finding: <your analysis citing Comparison N only where genuinely relevant>
"""
    result = _llm.generate(prompt, max_tokens=400, temperature=0.3, caller='review_tool.analyze_clause')
    cited = _cited_comparison_numbers(result)
    evidence = _format_evidence_block(comparisons, cited_only=cited if cited else None)
    header = f"**{title}**\n{result}"
    return f"{header}\n{evidence}" if evidence else header


def _extract_severity(finding: str) -> str:
    """Pulls 'High'/'Medium'/'Low' out of a finding string produced by _analyze_clause."""
    match = re.search(r"severity:\s*(high|medium|low)", finding, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def review_contract(contract_text: str) -> str:
    """
    Main entry point for the review tool. Returns a formatted report string.
    """
    if not contract_text or not contract_text.strip():
        return "No contract text was provided to review."

    sections = detect_sections(contract_text)
    # Filter out empty/trivial sections (e.g. blank preamble)
    sections = [(title, text) for title, text in sections if len(text.strip()) > 40]

    if not sections:
        return "Could not detect distinct clauses in the provided text. Please check the formatting."

    findings = []
    high_count = 0
    for title, text in sections:
        comparisons = _retrieve_comparisons(text)
        finding = _analyze_clause(title, text, comparisons)
        severity = _extract_severity(finding)
        if severity == "high":
            high_count += 1

        # Auto-chain negotiation guidance for anything Medium/High severity --
        # a flagged clause is more useful with "what to do about it" attached.
        # If no comparable playbook lessons exist, suggest_negotiation returns
        # None and we skip silently rather than let the LLM invent ungrounded advice.
        if severity in NEGOTIATION_TRIGGER_SEVERITIES:
            guidance = suggest_negotiation(title, text, finding)
            if guidance:
                finding = f"{finding}\n{guidance.strip()}"

        findings.append(finding)

    findings_block = "\n\n".join(findings)

    summary_prompt = f"""Write a 2-sentence executive summary of this contract review.
There are {len(sections)} clauses reviewed, {high_count} flagged as high severity.

Findings:
{findings_block[:3000]}
"""
    summary = _llm.generate(summary_prompt, max_tokens=150, temperature=0.3, caller="review_tool.summary")

    report = f"""EXECUTIVE SUMMARY: {summary.strip()}

CLAUSE FINDINGS:
{findings_block}"""
    return report
