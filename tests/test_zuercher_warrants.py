"""Tests for Zuercher portal warrant parsing."""

from services.ingestion.warrants.zuercher import (
    JAIL_HOLD_WARRANT_TYPE,
    parse_zuercher_jail_warrant_holds,
    parse_zuercher_warrant_rows,
)


def test_parse_zuercher_warrant_rows_extracts_records():
    rows = [
        {
            "name": "SMITH, JOHN A",
            "charges": "FAILURE TO APPEAR",
            "bond": "$500.00",
            "court": "Carbon County Justice Court",
        }
    ]
    records = parse_zuercher_warrant_rows(
        rows,
        county="Carbon",
        source_url="https://carbon-so-mt.zuercherportal.com/#/warrants",
    )
    assert len(records) == 1
    assert records[0].person_name == "John A Smith"
    assert records[0].county == "Carbon"
    assert records[0].charges_text == "FAILURE TO APPEAR"
    assert records[0].bond_amount == "$500.00"
    assert records[0].issued_by == "Carbon County Justice Court"


def test_parse_zuercher_jail_warrant_holds_filters_warrant_holds():
    rows = [
        {
            "name": "DOE, JANE",
            "hold_reasons": "Local Warrant: Bench warrant;<br />Bond - Cash/Surety, $50000.00;",
        },
        {
            "name": "SMITH, BOB",
            "hold_reasons": "Hold for DOC;",
        },
    ]
    records = parse_zuercher_jail_warrant_holds(
        rows,
        county="Jefferson",
        source_url="https://jefferson-so-mt.zuercherportal.com/#/inmates",
    )
    assert len(records) == 1
    assert records[0].person_name == "Jane Doe"
    assert records[0].warrant_type == JAIL_HOLD_WARRANT_TYPE
    assert "Jail roster hold" in records[0].charges_text
    assert "Bench warrant" in records[0].charges_text
    assert records[0].source_record_id.startswith("zuercher-jail-hold:jefferson:")
