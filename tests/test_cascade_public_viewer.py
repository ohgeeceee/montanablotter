from services.ingestion.fetchers.cascade_public_viewer import (
    OcrToken,
    _records_from_tokens,
)


def token(text, left, top, width=60, height=20):
    return OcrToken(text, left, top, width, height, 95.0)


def test_records_from_spatial_ocr_tokens():
    width, height = 2600, 2000
    tokens = [
        token("DOE,", 150, 550), token("JANE", 150, 580),
        token("34", 570, 550), token("00123456", 760, 550),
        token("09/03/26", 1020, 550), token("14:20", 1140, 550),
        token("45-1-101", 1320, 550), token("Sample", 1430, 550), token("charge", 1550, 550),
        token("SMITH,", 150, 850), token("JOHN", 150, 880),
        token("41", 570, 850), token("00654321", 760, 850),
        token("09/02/26", 1020, 850), token("08:05", 1140, 850),
        token("61-8-401", 1320, 850), token("Another", 1430, 850), token("charge", 1580, 850),
    ]
    records = _records_from_tokens(
        tokens,
        image_width=width,
        image_height=height,
        source_url="https://example.test/roster",
    )

    assert len(records) == 2
    assert records[0].person_name == "Doe, Jane"
    assert records[0].booking_number == "00123456"
    assert records[0].booking_at == "2026-09-03 14:20:00"
    assert records[0].source_record_id == "cascade:00123456:2026-09-03 14:20:00"
    assert "Sample charge" in records[0].charges_summary
    assert records[1].person_name == "Smith, John"


def test_incomplete_spatial_row_is_rejected():
    tokens = [
        token("DOE,", 150, 550), token("JANE", 150, 580),
        token("09/03/26", 1020, 550), token("14:20", 1140, 550),
    ]
    assert _records_from_tokens(
        tokens,
        image_width=2600,
        image_height=2000,
        source_url="https://example.test/roster",
    ) == []
