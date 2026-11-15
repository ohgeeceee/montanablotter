#!/usr/bin/env python3
"""Guard against mislabelling violent offenders as sex offenders.

The Montana registry covers BOTH violent and sexual offenders. Several public
surfaces used to print a blanket "sex offender" label for every registrant,
which published registered violent offenders as sex offenders.

This check runs the three invariants that prevent a recurrence:

  1. TEMPLATE  -- no public template may carry a blanket sex-offender label
     (the person profile used to hardcode "Registered Sex Offender").
  2. DATA      -- every active registrant must carry a non-empty offender_type,
     otherwise the label logic falls back to a generic string.
  3. API       -- /api/geo/sex-offender-county-counts must report the
     violent/sexual split, and the parts must sum to the total.

Exit codes: 0 = pass, 1 = mislabel risk found, 2 = could not run.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SITE_URL = os.environ.get("MB_HEALTH_URL", "http://127.0.0.1:5000").rstrip("/")

# Label text that must never appear as an unconditional statement about a
# registrant. Matched case-insensitively across public templates.
BLANKET_LABELS = [
    "Registered Sex Offender",
    "registered sex offenders",
    "Sex offender registry",
    "Sex Offender Density",
]

# Templates that render individual registrants. Admin templates are excluded:
# an operator's screen naming the registry is not a public mislabel.
PUBLIC_TEMPLATES = [
    "person_profile.html",
    "sex_offender_county.html",
    "sex_offender_updates.html",
    "sex_offender_alerts.html",
    "crime_map.html",
]


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def check_templates(failures: list[str]) -> None:
    for name in PUBLIC_TEMPLATES:
        path = ROOT / "templates" / name
        if not path.is_file():
            failures.append(f"template missing: {name}")
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for label in BLANKET_LABELS:
            # Ignore comments and computed values -- an explanatory comment, or
            # a string built from the registrant's own type, is fine.
            for match in re.finditer(re.escape(label), body, re.IGNORECASE):
                line_no = body[: match.start()].count("\n") + 1
                line = body.splitlines()[line_no - 1].strip()
                if line.startswith("#") or line.startswith("{#"):
                    continue
                if "${" in line or "{{ " in line:
                    continue
                failures.append(f"{name}:{line_no} blanket label {label!r}")


def check_data(failures: list[str]) -> None:
    sys.path.insert(0, str(ROOT))
    try:
        import config
        import sqlite3
    except Exception as exc:  # pragma: no cover
        failures.append(f"could not import DB stack: {exc}")
        return

    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN COALESCE(offender_type,'')='' THEN 1 ELSE 0 END) AS blank, "
            "SUM(CASE WHEN lower(offender_type) LIKE '%sexual%' THEN 1 ELSE 0 END) AS sexual, "
            "SUM(CASE WHEN lower(offender_type) NOT LIKE '%sexual%' THEN 1 ELSE 0 END) AS non_sexual "
            "FROM sex_offenders WHERE status='active'"
        ).fetchone()
        conn.close()
    except Exception as exc:
        failures.append(f"could not read sex_offenders: {exc}")
        return

    total = int(rows["total"] or 0)
    blank = int(rows["blank"] or 0)
    sexual = int(rows["sexual"] or 0)
    non_sexual = int(rows["non_sexual"] or 0)

    print(f"active registrants: {total} (sexual={sexual}, non-sexual={non_sexual}, blank type={blank})")
    if total == 0:
        failures.append("sex_offenders has no active rows")
    if blank:
        failures.append(
            f"{blank} active registrant(s) have no offender_type; the label logic cannot "
            "distinguish violent from sexual"
        )
    if non_sexual == 0:
        failures.append("no non-sexual (violent) registrants present — label path untested")


def check_api(failures: list[str]) -> None:
    url = f"{SITE_URL}/api/geo/sex-offender-county-counts"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            import json

            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        failures.append(f"could not reach {url}: {exc.reason}")
        return
    except Exception as exc:
        failures.append(f"could not parse {url}: {exc}")
        return

    counts = payload.get("counts") or []
    if not counts:
        failures.append(f"{url} returned no county counts")
        return
    for item in counts:
        if "sexual_count" not in item or "violent_count" not in item:
            failures.append(
                f"{url} is missing the sexual/violent split for {item.get('county')!r}"
            )
            continue
        parts = int(item["sexual_count"]) + int(item["violent_count"])
        if parts != int(item["count"]):
            failures.append(
                f"{url}: {item.get('county')} parts ({parts}) != total ({item['count']})"
            )


def main() -> int:
    _load_env()
    failures: list[str] = []
    check_templates(failures)
    check_data(failures)
    check_api(failures)

    if failures:
        print("MISLABEL RISK:")
        for f in failures:
            print("  -", f)
        return 1
    print("OK: no blanket sex-offender labels; registry type split intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
