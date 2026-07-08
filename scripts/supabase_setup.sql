-- ============================================================================
-- Azazil Legal AI -- Supabase (Postgres + pgvector) setup
-- Run this once in the Supabase SQL editor (or via psql) before running
-- scripts/migrate_chroma_to_supabase.py
--
-- Embedding dimension is 384 because the embedder is BAAI/bge-small-en
-- (see utils/embedder.py / config/settings.py EMBEDDING_MODEL). If you ever
-- change the embedding model, the vector(384) dimension below must change
-- to match -- Postgres will reject vectors of the wrong dimension at
-- insert time, so this isn't a silent failure mode, just something to
-- keep in sync.
-- ============================================================================

create extension if not exists vector;

-- One table per collection, mirroring the four ChromaDB collections:
--   legal_risks           -- CUAD clauses,                 used by review_contract
--   contract_templates    -- NDA/contract templates,        used by draft_contract
--   negotiation_playbook  -- synthetic negotiation lessons, used by negotiate_tool
--   maud_clauses          -- MAUD merger-agreement chunks,  used by benchmark_ma_provision
--
-- chroma_id is kept purely for traceability back to the original Chroma
-- record during/after migration -- not used at query time.

create table if not exists legal_risks (
    id bigint generated always as identity primary key,
    chroma_id text,
    document text not null,
    metadata jsonb not null default '{}'::jsonb,
    embedding vector(384) not null
);

create table if not exists contract_templates (
    id bigint generated always as identity primary key,
    chroma_id text,
    document text not null,
    metadata jsonb not null default '{}'::jsonb,
    embedding vector(384) not null
);

create table if not exists negotiation_playbook (
    id bigint generated always as identity primary key,
    chroma_id text,
    document text not null,
    metadata jsonb not null default '{}'::jsonb,
    embedding vector(384) not null
);

create table if not exists maud_clauses (
    id bigint generated always as identity primary key,
    chroma_id text,
    document text not null,
    metadata jsonb not null default '{}'::jsonb,
    embedding vector(384) not null
);

-- Approximate-nearest-neighbor indexes (cosine distance) for fast retrieval.
-- ivfflat requires at least a small amount of data to build meaningfully;
-- if a table is empty when you run this, create the index AFTER migrating
-- data instead (index creation is cheap to re-run).
create index if not exists legal_risks_embedding_idx
    on legal_risks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists contract_templates_embedding_idx
    on contract_templates using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists negotiation_playbook_embedding_idx
    on negotiation_playbook using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists maud_clauses_embedding_idx
    on maud_clauses using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ============================================================================
-- match_<table> functions: one per table (Postgres functions can't take a
-- table name as a parameter without dynamic SQL, and dynamic SQL loses the
-- query planner's ability to use the vector index well -- so one function
-- per table is the standard, fast Supabase/pgvector pattern).
--
-- Each returns rows shaped to match what utils/supabase_client.py expects:
-- id, document, metadata, and distance (cosine distance, lower = more
-- similar -- same direction Chroma uses, so the existing relative
-- distance-ratio filtering logic in review_tool.py / maud_tool.py works
-- unchanged).
-- ============================================================================

create or replace function match_legal_risks(
    query_embedding vector(384),
    match_count int default 5
)
returns table (id bigint, document text, metadata jsonb, distance float)
language sql stable
as $$
    select id, document, metadata, embedding <=> query_embedding as distance
    from legal_risks
    order by embedding <=> query_embedding
    limit match_count;
$$;

create or replace function match_contract_templates(
    query_embedding vector(384),
    match_count int default 5
)
returns table (id bigint, document text, metadata jsonb, distance float)
language sql stable
as $$
    select id, document, metadata, embedding <=> query_embedding as distance
    from contract_templates
    order by embedding <=> query_embedding
    limit match_count;
$$;

create or replace function match_negotiation_playbook(
    query_embedding vector(384),
    match_count int default 5
)
returns table (id bigint, document text, metadata jsonb, distance float)
language sql stable
as $$
    select id, document, metadata, embedding <=> query_embedding as distance
    from negotiation_playbook
    order by embedding <=> query_embedding
    limit match_count;
$$;

create or replace function match_maud_clauses(
    query_embedding vector(384),
    match_count int default 5
)
returns table (id bigint, document text, metadata jsonb, distance float)
language sql stable
as $$
    select id, document, metadata, embedding <=> query_embedding as distance
    from maud_clauses
    order by embedding <=> query_embedding
    limit match_count;
$$;

-- ============================================================================
-- After running this file:
-- 1. Get your direct Postgres connection string from
--    Supabase dashboard -> Project Settings -> Database -> Connection string
--    (use the direct connection, not the pooled/transaction-mode one, for
--    the migration script -- pooled connections don't reliably support the
--    prepared-statement style psycopg2 + pgvector uses).
-- 2. Set it as SUPABASE_DB_URL in your environment, e.g.:
--      export SUPABASE_DB_URL="postgresql://postgres:[password]@[host]:5432/postgres"
-- 3. Run: python scripts/migrate_chroma_to_supabase.py
-- ============================================================================
