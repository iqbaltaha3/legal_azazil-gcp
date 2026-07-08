from typing import Optional

from utils.embedder import Embedder
from utils.supabase_client import SupabaseClient
from utils.llm_client import GroqClient

_embedder = Embedder()
_llm = GroqClient()
_db = SupabaseClient()


def _retrieve_full_templates(
    query_text: str,
    n_results: int = 3
):
    try:

        emb = _embedder.encode(query_text)

        rows = _db.search_full_templates(
            emb,
            limit=n_results
        )

        templates = []

        for row in rows:

            templates.append(
                {
                    "id": row[0],
                    "template_name": row[1],
                    "template_type": row[2],
                    "content": row[3],
                    "metadata": row[4]
                }
            )

        return templates

    except Exception as e:

        print(
            f"Template retrieval error: {e}"
        )

        return []


def draft_contract(
    instructions: str,
    source_contract: Optional[str] = None
) -> str:

    if not instructions.strip():

        return (
            "No drafting instructions were provided."
        )

    templates = _retrieve_full_templates(
        instructions,
        n_results=3
    )

    if not templates:

        return (
            "No suitable contract templates "
            "were found in the template library."
        )

    template_block = "\n\n====================\n\n".join(
        f"""
TEMPLATE NAME:
{t['template_name']}

TEMPLATE TYPE:
{t['template_type']}

CONTENT:
{t['content'][:8000]}
"""
        for t in templates
    )

    if source_contract:

        prompt = f"""
You are a senior commercial attorney.

Your task is to revise an existing contract.

Use the retrieved full contract templates as market-standard precedents.

Only modify sections necessary to satisfy the user's instructions.

Preserve all other provisions.

========================
USER CONTRACT
========================

{source_contract[:10000]}

========================
REVISION REQUEST
========================

{instructions}

========================
REFERENCE TEMPLATES
========================

{template_block}

Output the complete revised contract.
"""

    else:

        prompt = f"""
You are a senior commercial attorney.

Generate a contract using the retrieved full contract templates as the primary precedent.

Follow the structure and style of the templates.

Do not invent unusual clauses.

Use market-standard drafting.

========================
USER REQUEST
========================

{instructions}

========================
REFERENCE TEMPLATES
========================

{template_block}

Generate a complete contract.

Include:

- Parties
- Definitions
- Term
- Fees
- Confidentiality
- IP Ownership
- Warranties
- Indemnity
- Limitation of Liability
- Termination
- Governing Law
- Notices
- Entire Agreement
- Signature Block

Output plain text only.
"""

    return _llm.generate(
        prompt,
        max_tokens=4000,
        temperature=0.3,
        caller="draft_tool.draft"
    )