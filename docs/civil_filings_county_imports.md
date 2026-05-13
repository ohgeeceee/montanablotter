# Civil Filings County Imports

This project now supports all 56 Montana counties for civil filing ingestion.

## Generate county templates

```bash
cd /root/montanablotter
./venv/bin/python scripts/maintenance/generate_civil_county_templates.py
```

Templates are written to:

- `data/civil_filings/import_templates/<county_key>.csv`

Each file includes one scaffold row. Replace scaffold values with real records and keep headers unchanged.

## Batch ingest county files

If you have county files named like `yellowstone.csv`, `missoula.csv`, etc:

```bash
cd /root/montanablotter
./venv/bin/python -m services.ingestion.civil_violations_pipeline \
  --no-live \
  --civil-import-dir data/civil_filings/import_templates
```

## Field schema

Required in practice:

- `county`
- `case_number`

Strongly recommended:

- `filing_date` (`YYYY-MM-DD`)
- `case_type_code` (examples: `UD`, `DV`, `CC`)
- `caption`
- `address`

Optional:

- `city`
- `case_type_label`
- `plaintiff_name`
- `defendant_name`
- `case_status`
- `source_url`
- `source_record_id`

