# Supabase Meeting Search Pipeline

This pipeline converts downloaded meeting PDFs into Markdown or OCR text,
chunks the content, embeds each chunk, and stores it in Supabase PostgreSQL
with `pgvector`.

## Components

- `meeting_pdf_pipeline/extractor.py`
  - prefers `markitdown`
  - falls back to `PyMuPDF`
  - uses Tesseract OCR when the PDF is scanned/image-only
- `meeting_pdf_pipeline/chunking.py`
  - splits long Markdown into retrieval-sized chunks
- `meeting_pdf_pipeline/embeddings.py`
  - calls the OpenAI embeddings API via HTTP
- `meeting_pdf_pipeline/store.py`
  - upserts full document text and vectorized chunks into PostgreSQL
- `sql/meeting_pdf_vector_schema.sql`
  - creates the `pgvector` extension, tables, index, and SQL search function

## Required Environment

- `MB_SUPABASE_PGVECTOR_DSN`
- `MB_EMBEDDING_API_KEY` or `OPENAI_API_KEY`
- optional `MB_EMBEDDING_MODEL`
- optional `MB_EMBEDDING_DIMENSIONS`
- optional `MB_TESSERACT_CMD`

## Install

```bash
cd /root/montanablotter
./venv/bin/pip install -r requirements.txt
```

## Apply Schema

Run the SQL in Supabase SQL editor or from the CLI:

```sql
\i sql/meeting_pdf_vector_schema.sql
```

## Ingest One PDF

```bash
cd /root/montanablotter
./venv/bin/python -m meeting_pdf_pipeline.runner ingest /path/to/agenda.pdf \
  --ensure-schema \
  --meeting-slug billings-city-council-2026-03-14 \
  --meeting-title "Billings City Council Regular Meeting" \
  --body-name "Billings City Council" \
  --location-name "Billings" \
  --source-url "https://agendas.montanablotter.com/meetings/billings-city-council-2026-03-14" \
  --document-url "https://www.billingsmt.gov/AgendaCenter/ViewFile/Agenda/_03142026-1234"
```

## Search

```bash
cd /root/montanablotter
./venv/bin/python -m meeting_pdf_pipeline.runner search "tribal water rights" --top-k 10
```

## Notes

- Semantic search works best on chunk rows, not one giant full-document vector.
- The pipeline stores both full Markdown and plain text so you can support
  exact-match and semantic retrieval together.
- The current schema uses `vector(1536)` for `text-embedding-3-small`.
  If you change embedding dimensions, update the SQL schema to match.
