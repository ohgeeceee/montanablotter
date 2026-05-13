# iCourtCase Playwright Capture

Use this when the direct HTTP scraper is blocked and you need live portal paths/cookies.

## Run capture

```bash
cd /root/montanablotter
./venv/bin/python scripts/maintenance/capture_icourtcase_session.py --wait-seconds 240 --include-cookies
```

During the capture window:

1. Log in to the court portal.
2. Open `Cases` -> `Civil Case`.
3. Run one or more searches.

Outputs:

- `reports/civil_filings/icourtcase_capture.json`
- `reports/civil_filings/icourtcase_capture.env`

## Apply captured settings

Copy values from `icourtcase_capture.env` into a secure sidecar file:

```bash
mkdir -p /root/montanablotter/.secrets
cp reports/civil_filings/icourtcase_capture.env /root/montanablotter/.secrets/icourtcase.env
chmod 600 /root/montanablotter/.secrets/icourtcase.env
```

Run production wrapper (includes preflight):

```bash
./scripts/ops/icourtcase_civil_ingest.sh --county Yellowstone
```

Key settings:

- `ICOURTCASE_BASE_URLS`
- `ICOURTCASE_SEARCH_PATHS`
- `ICOURTCASE_COOKIE_HEADER`
