# scripts/ingest_maud_to_supabase.py

"""
Ingest MAUD merger-agreement text files directly into Supabase.

Features:
- Resume support
- No table truncation
- Skips already-ingested chunks
- Continues from last successful insert

Usage:
    python scripts/ingest_maud_to_supabase.py
"""

import sys
import os
import glob
import re

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

from sentence_transformers import SentenceTransformer
from config.settings import TABLES, EMBEDDING_MODEL
from utils.chunking import detect_sections, chunk_section
from utils.supabase_ingest import SupabaseIngestor

WRITE_BATCH_SIZE = 200


def get_existing_chunk_count(ingestor):
    """
    Returns the number of chunks already present in Supabase.
    """
    cur = ingestor.conn.cursor()

    try:
        cur.execute(f"SELECT COUNT(*) FROM {TABLES['maud']}")
        count = cur.fetchone()[0]
        return count

    finally:
        cur.close()


def main():

    root_folder = "data/maud"

    print(f"📥 Ingesting MAUD from text files in: {root_folder}")

    if not os.path.exists(root_folder):
        print(f"❌ Folder not found: {root_folder}")
        return

    txt_files = glob.glob(
        os.path.join(root_folder, "contract_*.txt")
    )

    txt_files.sort(
        key=lambda x: int(
            re.search(
                r"contract_(\d+)\.txt",
                x
            ).group(1)
        )
    )

    print(f"✅ Found {len(txt_files)} text files.")

    if not txt_files:
        return

    print("🔌 Connecting to Supabase...")
    ingestor = SupabaseIngestor(TABLES["maud"])

    existing_chunks = get_existing_chunk_count(ingestor)

    print(
        f"📌 Found {existing_chunks:,} chunks already in Supabase"
    )

    print("🤖 Loading embedding model...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    print("✅ Embedding model loaded")

    total_chunks_seen = 0
    pending = []

    for file_idx, file_path in enumerate(txt_files):

        with open(
            file_path,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as f:
            text = f.read()

        sections = detect_sections(text)

        for section_title, section_text in sections:

            chunks = chunk_section(
                section_text,
                max_chunk_size=2000,
                overlap=200
            )

            for j, chunk in enumerate(chunks):

                chunk_id = f"maud_{total_chunks_seen}"

                # Resume logic
                if total_chunks_seen < existing_chunks:
                    total_chunks_seen += 1
                    continue

                emb = embedder.encode(chunk).tolist()

                is_parent = (
                    j == 0 and len(chunks) == 1
                )

                meta = {
                    "section": section_title,
                    "source": "MAUD",
                    "file": os.path.basename(file_path),
                    "chunk_type":
                        "parent"
                        if (
                            is_parent
                            or (
                                j == 0
                                and len(chunks) > 1
                            )
                        )
                        else "child"
                }

                if len(chunks) > 1 and j > 0:
                    meta["parent_id"] = (
                        f"maud_{total_chunks_seen - j}"
                    )

                pending.append(
                    (
                        chunk_id,
                        chunk,
                        meta,
                        emb
                    )
                )

                total_chunks_seen += 1

                if len(pending) >= WRITE_BATCH_SIZE:

                    ingestor.insert_batch(pending)

                    print(
                        f"   → Ingested up to chunk "
                        f"{total_chunks_seen:,}"
                    )

                    pending = []

        print(
            f"📄 [{file_idx + 1}/{len(txt_files)}] "
            f"Processed: {os.path.basename(file_path)} "
            f"→ {total_chunks_seen:,} chunks seen"
        )

    if pending:
        print(
            f"💾 Writing final batch "
            f"({len(pending)} chunks)"
        )

        ingestor.insert_batch(pending)

    print("🔄 Reindexing...")
    ingestor.reindex()

    ingestor.close()

    print(
        f"✅ MAUD ingestion complete "
        f"({total_chunks_seen:,} total chunks)"
    )


if __name__ == "__main__":
    main()