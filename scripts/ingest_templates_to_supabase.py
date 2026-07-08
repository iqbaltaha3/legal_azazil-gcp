# scripts/ingest_templates_to_supabase.py

"""
Ingest contract template TXT files directly into Supabase.

Features:
- TXT files only
- Resume support
- No truncate()
- Continues after crashes
- Batch inserts

Usage:
    python scripts/ingest_templates_to_supabase.py
"""

import sys
import os
import glob

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
    Returns number of chunks already stored.
    Used for resume support.
    """
    cur = ingestor.conn.cursor()

    try:
        cur.execute(
            f"SELECT COUNT(*) FROM {TABLES['templates']}"
        )
        return cur.fetchone()[0]

    finally:
        cur.close()


def main():

    root_folder = "data/templates"

    print(
        f"📥 Ingesting contract templates from: {root_folder}"
    )

    if not os.path.exists(root_folder):
        print(f"❌ Folder not found: {root_folder}")
        return

    files = glob.glob(
        os.path.join(root_folder, "**", "*.txt"),
        recursive=True
    )

    files.sort()

    print(f"✅ Found {len(files)} template files.")

    if not files:
        return

    print("🔌 Connecting to Supabase...")
    ingestor = SupabaseIngestor(TABLES["templates"])

    existing_chunks = get_existing_chunk_count(ingestor)

    print(
        f"📌 Found {existing_chunks:,} existing template chunks"
    )

    print("🤖 Loading embedding model...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    print("✅ Embedding model loaded")

    total_chunks_seen = 0
    pending = []

    for file_index, file_path in enumerate(files):

        try:

            with open(
                file_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:
                text = f.read()

            if not text.strip():
                continue

            sections = detect_sections(text)

            if not sections:
                sections = [("FULL", text)]

            for section_title, section_text in sections:

                if len(section_text.strip()) < 100:
                    continue

                chunks = chunk_section(
                    section_text,
                    max_chunk_size=2000,
                    overlap=200
                )

                for j, chunk in enumerate(chunks):

                    chunk_id = (
                        f"template_{total_chunks_seen}"
                    )

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
                        "source": "template_library",
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
                            f"template_{total_chunks_seen - j}"
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

                        ingestor.insert_batch(
                            pending
                        )

                        print(
                            f"   → Ingested up to "
                            f"{total_chunks_seen:,} chunks"
                        )

                        pending = []

            print(
                f"📄 [{file_index + 1}/{len(files)}] "
                f"{os.path.basename(file_path)} "
                f"→ {total_chunks_seen:,} chunks seen"
            )

        except Exception as e:

            print(
                f"❌ Error processing "
                f"{file_path}: {e}"
            )

            continue

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
        f"✅ Template ingestion complete "
        f"({total_chunks_seen:,} chunks)"
    )


if __name__ == "__main__":
    main()