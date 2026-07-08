# scripts/ingest_templates_full_to_supabase.py

"""
Ingest full contract templates into Supabase.

Each template file becomes ONE row.

No chunking.
No section splitting.
No parent/child relationships.

Usage:
    python scripts/ingest_templates_full_to_supabase.py
"""

import os
import sys
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
from psycopg2.extras import Json

from config.settings import EMBEDDING_MODEL
from utils.supabase_ingest import SupabaseIngestor


TABLE_NAME = "contract_templates_full"


def get_template_type(filename: str) -> str:

    name = filename.lower()

    if "nda" in name:
        return "nda"

    if "saas" in name:
        return "saas"

    if "employment" in name:
        return "employment"

    if "consulting" in name:
        return "consulting"

    if "license" in name:
        return "licensing"

    if "lease" in name:
        return "lease"

    if "privacy" in name:
        return "privacy"

    if "terms" in name:
        return "terms"

    return "general"


def template_exists(conn, template_id):

    cur = conn.cursor()

    try:

        cur.execute(
            f"""
            SELECT 1
            FROM {TABLE_NAME}
            WHERE id = %s
            LIMIT 1
            """,
            (template_id,)
        )

        return cur.fetchone() is not None

    finally:

        cur.close()


def main():

    root_folder = "data/templates"

    print(
        f"\n📥 Ingesting full templates from: "
        f"{root_folder}\n"
    )

    if not os.path.exists(root_folder):

        print(
            f"❌ Folder not found: {root_folder}"
        )

        return

    files = glob.glob(
        os.path.join(root_folder, "**", "*.txt"),
        recursive=True
    )

    files.sort()

    print(
        f"✅ Found {len(files)} template files\n"
    )

    if not files:
        return

    print("🔌 Connecting to Supabase...")

    ingestor = SupabaseIngestor(TABLE_NAME)

    print("🤖 Loading embedding model...")

    embedder = SentenceTransformer(
        EMBEDDING_MODEL
    )

    print("✅ Model loaded\n")

    inserted = 0
    skipped = 0

    conn = ingestor.conn

    for i, file_path in enumerate(files):

        try:

            with open(
                file_path,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:

                text = f.read()

            if not text.strip():

                print(
                    f"⚠️ Empty file: {file_path}"
                )

                continue

            filename = os.path.basename(
                file_path
            )

            template_id = (
                os.path.splitext(filename)[0]
            )

            if template_exists(
                conn,
                template_id
            ):

                skipped += 1

                print(
                    f"⏭️ Skipping existing: "
                    f"{filename}"
                )

                continue

            embedding = embedder.encode(
                text
            ).tolist()

            template_type = (
                get_template_type(filename)
            )

            metadata = {
                "source": "template_library",
                "file": filename
            }

            cur = conn.cursor()

            cur.execute(
                f"""
                INSERT INTO {TABLE_NAME}
                (
                    id,
                    template_name,
                    template_type,
                    content,
                    metadata,
                    embedding
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    template_id,
                    template_id.replace(
                        "_",
                        " "
                    ).title(),
                    template_type,
                    text,
                    Json(metadata),
                    embedding
                )
            )

            conn.commit()

            cur.close()

            inserted += 1

            print(
                f"📄 [{i+1}/{len(files)}] "
                f"Inserted: {filename}"
            )

        except Exception as e:

            print(
                f"❌ Error processing "
                f"{file_path}"
            )

            print(e)

            try:
                conn.rollback()
            except:
                pass

    print("\n====================")
    print("INGESTION COMPLETE")
    print("====================")
    print(f"✅ Inserted: {inserted}")
    print(f"⏭️ Skipped: {skipped}")
    print("====================\n")

    conn.close()


if __name__ == "__main__":
    main()