"""Tests for shared HTML li-list warrant parsing."""

from services.ingestion.warrants.list_items import looks_like_warrant_name, parse_li_warrant_list


def test_looks_like_warrant_name_filters_navigation():
    assert looks_like_warrant_name("Smith, John")
    assert not looks_like_warrant_name("Home")
    assert not looks_like_warrant_name("No Comma Name")


def test_parse_li_warrant_list_deduplicates_same_name():
    html = "<ul><li>Smith, John</li><li>Smith, John</li></ul>"
    records = parse_li_warrant_list(
        html,
        county="Test",
        source_url="https://example.test",
        source_prefix="test-warrant",
    )
    assert len(records) == 2
    assert records[0].source_record_id == "test-warrant:john-smith"
    assert records[1].source_record_id == "test-warrant:john-smith:2"
