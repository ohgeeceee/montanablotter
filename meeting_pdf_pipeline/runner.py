from __future__ import annotations

import argparse
import json
from pathlib import Path

from .chunking import chunk_markdown
from .embeddings import OpenAIEmbeddingClient
from .extractor import MeetingPDFMarkdownExtractor
from .store import PgVectorMeetingStore


def _build_chunk_payloads(markdown: str, embedding_client: OpenAIEmbeddingClient) -> list[dict]:
    chunks = chunk_markdown(markdown)
    if not chunks:
        return []
    embeddings = embedding_client.embed_texts(chunks)
    payloads = []
    for index, (chunk_markdown_value, embedding) in enumerate(zip(chunks, embeddings)):
        payloads.append(
            {
                "chunk_index": index,
                "chunk_markdown": chunk_markdown_value,
                "chunk_text": chunk_markdown_value,
                "embedding": embedding,
            }
        )
    return payloads


def ingest_pdf(args) -> int:
    extractor = MeetingPDFMarkdownExtractor()
    embedding_client = OpenAIEmbeddingClient()
    store = PgVectorMeetingStore()
    if args.ensure_schema:
        store.ensure_schema()

    extraction = extractor.convert(args.pdf_path)
    payloads = _build_chunk_payloads(extraction.markdown, embedding_client)
    document_id = store.upsert_document(
        extraction_result=extraction,
        chunk_payloads=payloads,
        metadata={
            "meeting_slug": args.meeting_slug,
            "meeting_title": args.meeting_title,
            "body_name": args.body_name,
            "location_name": args.location_name,
            "source_url": args.source_url,
            "document_url": args.document_url or Path(args.pdf_path).resolve().as_uri(),
        },
    )
    print(
        json.dumps(
            {
                "document_id": document_id,
                "pdf_path": args.pdf_path,
                "extraction_method": extraction.extraction_method,
                "is_scanned": extraction.is_scanned,
                "page_count": extraction.page_count,
                "chunk_count": len(payloads),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def search_documents(args) -> int:
    embedding_client = OpenAIEmbeddingClient()
    store = PgVectorMeetingStore()
    query_embedding = embedding_client.embed_text(args.query)
    results = store.semantic_search(query_embedding=query_embedding, match_count=args.top_k)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Process meeting PDFs into pgvector-backed semantic search.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Convert a PDF, embed it, and upsert it into Supabase/Postgres.")
    ingest.add_argument("pdf_path")
    ingest.add_argument("--meeting-slug", default="")
    ingest.add_argument("--meeting-title", default="")
    ingest.add_argument("--body-name", default="")
    ingest.add_argument("--location-name", default="")
    ingest.add_argument("--source-url", default="")
    ingest.add_argument("--document-url", default="")
    ingest.add_argument("--ensure-schema", action="store_true")
    ingest.set_defaults(handler=ingest_pdf)

    search = subparsers.add_parser("search", help="Run semantic search over meeting-document chunks.")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=10)
    search.set_defaults(handler=search_documents)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
