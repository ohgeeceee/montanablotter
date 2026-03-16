from __future__ import annotations

from pathlib import Path
from typing import Any

import config


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.12g}" for value in values) + "]"


class PgVectorMeetingStore:
    def __init__(self, dsn: str | None = None, *, schema: str | None = None) -> None:
        self.dsn = dsn or config.SUPABASE_PGVECTOR_DSN
        self.schema = schema or config.SUPABASE_PGVECTOR_SCHEMA
        if not self.dsn:
            raise RuntimeError("MB_SUPABASE_PGVECTOR_DSN is required")

    def connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required for Supabase / PostgreSQL writes") from exc
        return psycopg.connect(self.dsn)

    def ensure_schema(self, sql_path: str | Path | None = None) -> None:
        path = Path(sql_path or Path(__file__).resolve().parents[1] / "sql" / "meeting_pdf_vector_schema.sql")
        sql = path.read_text(encoding="utf-8")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

    def upsert_document(
        self,
        *,
        extraction_result,
        chunk_payloads: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> int:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.meeting_document_texts (
                        meeting_slug,
                        meeting_title,
                        body_name,
                        location_name,
                        source_url,
                        document_url,
                        file_name,
                        markdown_content,
                        text_content,
                        extraction_method,
                        is_scanned,
                        page_count,
                        content_sha256
                    ) VALUES (
                        %(meeting_slug)s,
                        %(meeting_title)s,
                        %(body_name)s,
                        %(location_name)s,
                        %(source_url)s,
                        %(document_url)s,
                        %(file_name)s,
                        %(markdown_content)s,
                        %(text_content)s,
                        %(extraction_method)s,
                        %(is_scanned)s,
                        %(page_count)s,
                        %(content_sha256)s
                    )
                    ON CONFLICT (document_url) DO UPDATE SET
                        meeting_slug = EXCLUDED.meeting_slug,
                        meeting_title = EXCLUDED.meeting_title,
                        body_name = EXCLUDED.body_name,
                        location_name = EXCLUDED.location_name,
                        source_url = EXCLUDED.source_url,
                        file_name = EXCLUDED.file_name,
                        markdown_content = EXCLUDED.markdown_content,
                        text_content = EXCLUDED.text_content,
                        extraction_method = EXCLUDED.extraction_method,
                        is_scanned = EXCLUDED.is_scanned,
                        page_count = EXCLUDED.page_count,
                        content_sha256 = EXCLUDED.content_sha256,
                        updated_at = timezone('utc', now())
                    RETURNING id
                    """,
                    {
                        "meeting_slug": metadata.get("meeting_slug", ""),
                        "meeting_title": metadata.get("meeting_title", ""),
                        "body_name": metadata.get("body_name", ""),
                        "location_name": metadata.get("location_name", ""),
                        "source_url": metadata.get("source_url", ""),
                        "document_url": metadata.get("document_url", ""),
                        "file_name": Path(extraction_result.pdf_path).name,
                        "markdown_content": extraction_result.markdown,
                        "text_content": extraction_result.text,
                        "extraction_method": extraction_result.extraction_method,
                        "is_scanned": extraction_result.is_scanned,
                        "page_count": extraction_result.page_count,
                        "content_sha256": extraction_result.content_sha256,
                    },
                )
                document_id = int(cur.fetchone()[0])

                cur.execute(
                    f"DELETE FROM {self.schema}.meeting_document_chunks WHERE document_id = %s",
                    (document_id,),
                )

                for chunk in chunk_payloads:
                    cur.execute(
                        f"""
                        INSERT INTO {self.schema}.meeting_document_chunks (
                            document_id,
                            chunk_index,
                            chunk_markdown,
                            chunk_text,
                            embedding
                        ) VALUES (
                            %(document_id)s,
                            %(chunk_index)s,
                            %(chunk_markdown)s,
                            %(chunk_text)s,
                            %(embedding)s::vector
                        )
                        """,
                        {
                            "document_id": document_id,
                            "chunk_index": chunk["chunk_index"],
                            "chunk_markdown": chunk["chunk_markdown"],
                            "chunk_text": chunk["chunk_text"],
                            "embedding": _vector_literal(chunk["embedding"]),
                        },
                    )
            conn.commit()
        return document_id

    def semantic_search(self, *, query_embedding: list[float], match_count: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            with conn.cursor(row_factory=None) as cur:
                cur.execute(
                    f"""
                    SELECT
                        chunk.id,
                        chunk.document_id,
                        doc.meeting_slug,
                        doc.meeting_title,
                        doc.body_name,
                        doc.location_name,
                        doc.document_url,
                        chunk.chunk_index,
                        chunk.chunk_text,
                        1 - (chunk.embedding <=> %s::vector) AS similarity
                    FROM {self.schema}.meeting_document_chunks AS chunk
                    JOIN {self.schema}.meeting_document_texts AS doc
                      ON doc.id = chunk.document_id
                    ORDER BY chunk.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (_vector_literal(query_embedding), _vector_literal(query_embedding), match_count),
                )
                rows = cur.fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "chunk_id": row[0],
                    "document_id": row[1],
                    "meeting_slug": row[2],
                    "meeting_title": row[3],
                    "body_name": row[4],
                    "location_name": row[5],
                    "document_url": row[6],
                    "chunk_index": row[7],
                    "chunk_text": row[8],
                    "similarity": float(row[9]),
                }
            )
        return results
