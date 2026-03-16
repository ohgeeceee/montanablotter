CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.meeting_document_texts (
    id BIGSERIAL PRIMARY KEY,
    meeting_slug TEXT,
    meeting_title TEXT,
    body_name TEXT,
    location_name TEXT,
    source_url TEXT,
    document_url TEXT NOT NULL UNIQUE,
    file_name TEXT,
    markdown_content TEXT NOT NULL,
    text_content TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    is_scanned BOOLEAN NOT NULL DEFAULT FALSE,
    page_count INTEGER NOT NULL DEFAULT 0,
    content_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_meeting_document_texts_meeting_slug
    ON public.meeting_document_texts(meeting_slug);

CREATE TABLE IF NOT EXISTS public.meeting_document_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES public.meeting_document_texts(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_markdown TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_meeting_document_chunks_document
    ON public.meeting_document_chunks(document_id);

CREATE INDEX IF NOT EXISTS idx_meeting_document_chunks_embedding
    ON public.meeting_document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE OR REPLACE FUNCTION public.match_meeting_document_chunks(
    query_embedding vector(1536),
    match_count integer DEFAULT 10
)
RETURNS TABLE (
    chunk_id bigint,
    document_id bigint,
    meeting_slug text,
    meeting_title text,
    body_name text,
    location_name text,
    document_url text,
    chunk_index integer,
    chunk_text text,
    similarity double precision
)
LANGUAGE sql
AS $$
    SELECT
        chunk.id AS chunk_id,
        doc.id AS document_id,
        doc.meeting_slug,
        doc.meeting_title,
        doc.body_name,
        doc.location_name,
        doc.document_url,
        chunk.chunk_index,
        chunk.chunk_text,
        1 - (chunk.embedding <=> query_embedding) AS similarity
    FROM public.meeting_document_chunks AS chunk
    JOIN public.meeting_document_texts AS doc
      ON doc.id = chunk.document_id
    ORDER BY chunk.embedding <=> query_embedding
    LIMIT match_count;
$$;
