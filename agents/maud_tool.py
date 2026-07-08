# agents/maud_tool.py
"""
M&A provision benchmarking tool, standalone (LLM-selectable). Grounded in the
MAUD collection: chunks of real merger agreements, split by section header the
same way review_tool splits a user's contract (see scripts/ingest_maud_txt.py).

Distinct from review_contract/legal_risk_db: CUAD covers general commercial
contracts, MAUD covers M&A-specific deal points (termination fees, no-shop /
fiduciary-out, MAC-outs, closing conditions, etc.) where "market standard"
means something specific to merger agreements and is usually expressed in
frequency/range terms rather than plain standard-vs-one-sided.

Accepts either:
  - a single provision/clause pasted in isolation, or
  - a full M&A agreement (or excerpt with multiple sections), which gets
    split via detect_sections and benchmarked section-by-section like review_contract.

Like review_contract, any provision flagged Medium or High severity/deviation
automatically gets negotiation guidance chained on via agents/negotiate_tool.py
(the same negotiation-playbook lookup review_contract uses) -- a flagged M&A
provision is more useful with a concrete fallback position attached than a
bare "this deviates from market norm" verdict.

RETRIEVAL QUALITY: same issue as review_tool -- naive section/chunk splitting
of real agreements produces fragments (headers, short boilerplate) alongside
real provision text. Those fragments are filtered out before ever reaching
the LLM, and comparisons are dropped if their distance is much worse than
the best match for the same query, so "Comparison N" never points at
something irrelevant just to satisfy a forced citation.
"""
import re
from typing import List, Dict
from utils.chunking import detect_sections
from utils.embedder import Embedder
from utils.supabase_client import SupabaseClient
from utils.llm_client import GroqClient
from config.settings import TOP_K_MAUD, NEGOTIATION_TRIGGER_SEVERITIES
from agents.negotiate_tool import suggest_negotiation

_embedder = Embedder()
_llm = GroqClient()
_db_client = SupabaseClient()
_collections = _db_client.connect_all()

# Kept equal to what's actually sent to the LLM in _benchmark_provision, so
# the evidence block reflects exactly what the model saw.
_EVIDENCE_CHARS = 600

# Same rationale as review_tool.py: short chunks are almost always headers
# or boilerplate fragments, not real provision text.
_MIN_QUALITY_CHARS = 120

# Relative distance cutoff -- drop results much worse than the best match
# for the same query, regardless of the collection's exact distance metric.
_MAX_DISTANCE_RATIO = 1.5


def _is_fragment(text: str) -> bool:
    return len(text.strip()) < _MIN_QUALITY_CHARS


def _retrieve_maud_chunks(text: str, n_results: int = TOP_K_MAUD) -> List[Dict]:
    collection = _collections.get("maud")
    if collection is None:
        return []
    try:
        emb = _embedder.encode(text)
        results = collection.query(
            query_embeddings=[emb], n_results=max(n_results * 2, n_results + 2),
            include=["documents", "metadatas", "distances"],
        )
        docs = results["documents"][0] if results["documents"] else []
        metas = results["metadatas"][0] if results["metadatas"] else []
        dists = results["distances"][0] if results.get("distances") else [None] * len(docs)
    except Exception as e:
        print(f"MAUD retrieval error: {e}")
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
    if not comparisons:
        return ""
    lines = ["  Retrieved comparisons (what the model actually compared against):"]
    shown_any = False
    for i, c in enumerate(comparisons):
        n = i + 1
        if cited_only is not None and n not in cited_only:
            continue
        shown_any = True
        section = c["metadata"].get("section", "unknown section")
        file = c["metadata"].get("file", "unknown file")
        snippet = c["doc"][:_EVIDENCE_CHARS].strip().replace("\n", " ")
        if len(c["doc"]) > _EVIDENCE_CHARS:
            snippet += "..."
        lines.append(f'  [Comparison {n}] section: {section} | source file: {file}\n      "{snippet}"')
    if not shown_any:
        return ""
    return "\n".join(lines)


def _benchmark_provision(title: str, provision_text: str, comparisons: List[Dict]) -> str:
    if not comparisons:
        return (f"**{title}** -- No comparable provisions found in the MAUD database; "
                f"unable to benchmark against market practice.")

    comparison_block = "\n\n".join(
        f"Comparison {i+1} (section: {c['metadata'].get('section', 'unknown')}, "
        f"source file: {c['metadata'].get('file', 'unknown')}):\n{c['doc'][:_EVIDENCE_CHARS]}"
        for i, c in enumerate(comparisons)
    )

    prompt = f"""You are an M&A deal-points analyst. Benchmark the user's provision against
real merger-agreement language retrieved from the MAUD database.

PROVISION FROM USER'S AGREEMENT ({title}):
{provision_text}

COMPARABLE PROVISIONS FROM REAL MERGER AGREEMENTS (MAUD):
{comparison_block}

Task:
- State whether this provision is buyer-favorable, seller-favorable, or market-standard,
  compared to the retrieved examples.
- Only cite a comparison number if it is genuinely relevant and substantive (not a heading,
  date, or unrelated topic). If NONE of the retrieved comparisons are genuinely relevant,
  say so explicitly instead of forcing a weak or irrelevant comparison.
- If relevant, note where the provision falls relative to typical market ranges
  (e.g. termination fee percentage, no-shop exceptions, MAC-out scope).
- Give a severity/deviation rating: High, Medium, or Low (how far from market norm). If no
  comparison was usable, give Severity: Low and say assessment is not possible.
- Keep it to 3-4 sentences.

Output format:
Severity: <High/Medium/Low>
Finding: <your analysis citing Comparison N only where genuinely relevant>
"""
    result = _llm.generate(prompt, max_tokens=400, temperature=0.3, caller="maud_tool.benchmark")
    cited = _cited_comparison_numbers(result)
    evidence = _format_evidence_block(comparisons, cited_only=cited if cited else None)
    header = f"**{title}**\n{result}"
    return f"{header}\n{evidence}" if evidence else header


def _extract_severity(finding: str) -> str:
    """Pulls 'High'/'Medium'/'Low' out of a finding string produced by _benchmark_provision."""
    match = re.search(r"severity:\s*(high|medium|low)", finding, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def benchmark_ma_provision(provision_text: str) -> str:
    """
    Main entry point. If given a full agreement/excerpt with multiple
    detectable sections, benchmarks each section separately (like
    review_contract). If given a single isolated provision, benchmarks it directly.
    """
    if not provision_text or not provision_text.strip():
        return "No M&A provision or agreement text was provided to benchmark."

    sections = detect_sections(provision_text)
    sections = [(title, text) for title, text in sections if len(text.strip()) > 40]

    # Fall back to treating the whole input as one provision if section
    # detection didn't find meaningful structure (e.g. a single pasted clause).
    if not sections:
        sections = [("Provision", provision_text.strip())]

    findings = []
    high_count = 0
    for title, text in sections:
        comparisons = _retrieve_maud_chunks(text)
        finding = _benchmark_provision(title, text, comparisons)
        severity = _extract_severity(finding)
        if severity == "high":
            high_count += 1

        # Auto-chain negotiation guidance for Medium/High deviation provisions,
        # same pattern review_tool.py uses for CUAD clauses -- a flagged M&A
        # provision is more useful with a concrete fallback position attached
        # than just a "this deviates from market norm" verdict. If no
        # comparable playbook lessons exist, suggest_negotiation returns None
        # and we skip silently rather than let the LLM invent ungrounded advice.
        if severity in NEGOTIATION_TRIGGER_SEVERITIES:
            guidance = suggest_negotiation(title, text, finding)
            if guidance:
                finding = f"{finding}\n{guidance.strip()}"

        findings.append(finding)

    findings_block = "\n\n".join(findings)

    if len(sections) > 1:
        summary_prompt = f"""Write a 2-sentence executive summary of this M&A provision benchmarking.
There are {len(sections)} provisions benchmarked, {high_count} flagged as high deviation from market norm.

Findings:
{findings_block[:3000]}
"""
        summary = _llm.generate(summary_prompt, max_tokens=150, temperature=0.3, caller="maud_tool.summary")
        return f"""EXECUTIVE SUMMARY: {summary.strip()}

PROVISION FINDINGS:
{findings_block}"""

    return findings_block