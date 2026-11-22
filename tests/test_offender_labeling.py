"""A registered violent offender must never be published as a sex offender.

Regression test for the complaint that Joshua Harlow Stevens -- a registered
VIOLENT offender -- was displayed as a sex offender. The registry covers both
groups, so every public label must come from the registrant's own
``offender_type``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.persons.offender_labels import registry_style

ROOT = Path(__file__).resolve().parent.parent

PUBLIC_TEMPLATES = [
    "person_profile.html",
    "sex_offender_county.html",
    "sex_offender_updates.html",
    "sex_offender_alerts.html",
    "crime_map.html",
]

BLANKET_LABELS = [
    "Registered Sex Offender",
    "registered sex offenders",
    "Sex offender registry",
    "Sex Offender Density",
]


@pytest.mark.parametrize(
    "offender_type,expected",
    [
        ("VIOLENT", "Registered Violent Offender"),
        ("violent", "Registered Violent Offender"),
        ("SEXUAL", "Registered Sex Offender"),
        ("SEXUAL/VIOLENT", "Registered Violent & Sex Offender"),
        ("", "Registered Offender"),
        (None, "Registered Offender"),
    ],
)
def test_registry_style_labels_by_type(offender_type, expected):
    assert registry_style(offender_type)["label"] == expected


def test_registry_style_distinguishes_violent_from_sexual():
    violent = registry_style("VIOLENT")
    sexual = registry_style("SEXUAL")
    assert "Violent" in violent["label"]
    assert "Sex" not in violent["label"]
    assert "Sex" in sexual["label"]
    assert "Violent" not in sexual["label"]


@pytest.mark.parametrize("template", PUBLIC_TEMPLATES)
def test_public_templates_do_not_hardcode_blanket_labels(template):
    """A blanket label in a template is how the mislabel shipped."""
    path = ROOT / "templates" / template
    assert path.is_file(), f"missing template {template}"
    body = path.read_text(encoding="utf-8", errors="replace")
    for label in BLANKET_LABELS:
        for match in re.finditer(re.escape(label), body, re.IGNORECASE):
            line_no = body[: match.start()].count("\n") + 1
            line = body.splitlines()[line_no - 1].strip()
            if line.startswith("#") or line.startswith("{#"):
                continue
            if "${" in line or "{{ " in line:
                continue
            pytest.fail(f"{template}:{line_no} hardcodes blanket label {label!r}")


def test_person_profile_labels_from_offender_type():
    """The profile page must read the type, not assume sexual."""
    body = (ROOT / "templates" / "person_profile.html").read_text(encoding="utf-8")
    assert "offender_registry_style(sex_offender.offender_type)" in body
    assert "Registered Sex Offender" not in body


def test_county_counts_api_reports_type_split():
    """The county-density API must not collapse violent and sexual offenders."""
    body = (ROOT / "blueprints" / "api.py").read_text(encoding="utf-8")
    assert "sexual_count" in body
    assert "violent_count" in body
