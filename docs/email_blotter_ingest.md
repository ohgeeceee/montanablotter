# Email Blotter Ingest

## What it does

`email_blotter_ingest.py` polls your Gmail/IMAP inbox for unread emails with "Blotter" in the subject, downloads any image attachments, converts them into a single PDF, checks if that blotter was already uploaded (via `source_documents` hash deduplication), and ingests it into montanablotter.com via the existing `processor.py` pipeline.

After processing, emails are marked as read and moved to the `Processed` IMAP folder.

## Files

- `/root/montanablotter/email_blotter_ingest.py` — main script
- `/root/montanablotter/scripts/ops/email_blotter_cron.sh` — cron wrapper

## Prerequisites

1. **IMAP access enabled** on your Gmail account (or whatever provider sends the blotters).
2. If using Gmail, you likely need an **App Password** instead of your regular password.
3. `Pillow` is already installed on this VPS. If you want better PDF quality, optionally install `img2pdf`:
   ```bash
   source /root/montanablotter/venv/bin/activate
   pip install img2pdf
   ```

## Configuration

All settings are read from `config.py` (which loads from `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `MB_EMAIL_USER` | — | IMAP username |
| `MB_EMAIL_PASSWORD` | — | IMAP password (App Password for Gmail) |
| `MB_IMAP_SERVER` | `imap.ionos.com` | IMAP server |
| `MB_IMAP_PORT` | `993` | IMAP SSL port |
| `MB_UPLOAD_DIR` | `/root/montanablotter/uploads` | Where generated PDFs are saved |
| `MB_PROCESSED_FOLDER` | `Processed` | IMAP folder to move handled emails |
| `MB_BLOTTER_SUBJECT_KEYWORD` | `Blotter` | Subject filter for unread search |

### County / Agency Detection

The script tries to auto-detect county and agency from the email subject and sender. Edit these dictionaries near the top of `email_blotter_ingest.py` to match your sources:

```python
COUNTY_MAP = {
    "hill county": "Hill",
    "hill sheriff": "Hill",
    "havre": "Hill",
    # ... add more
}

AGENCY_MAP = {
    "hill county": "Hill County Sheriff's Office",
    "havre police": "Havre Police Department",
    # ... add more
}
```

## Manual test run

```bash
cd /root/montanablotter
source venv/bin/activate
python3 email_blotter_ingest.py
```

## Cron setup

Add to the user's crontab (runs every 15 minutes):

```bash
crontab -e
```

Paste:

```
*/15 * * * * /root/montanablotter/scripts/ops/email_blotter_cron.sh >> /var/log/montanablotter/email_blotter.log 2>&1
```

Or run once to test via the wrapper:

```bash
/root/montanablotter/scripts/ops/email_blotter_cron.sh
```

## Logs

- Stdout from the script goes to `/var/log/montanablotter/email_blotter.log` when run via cron.
- The script also prints to stdout so you can see real-time output when running manually.

## Duplicate detection

Two layers:
1. **Source document hash** — the generated PDF is SHA-256 hashed and stored in `source_documents`. If the same email/images are processed again, `ensure_source_document()` returns the existing row and the script skips.
2. **Blotter-level dedupe** — `processor.py` already checks `blotters.source_document_id` and skips duplicates at ingest time.
3. **Incident-level dedupe** — `processor.py` also deduplicates individual incidents by `(cfs_number, date, time, incident_type, location)` before inserting into `records`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Email credentials not configured` | Add `MB_EMAIL_USER` and `MB_EMAIL_PASSWORD` to `.env` |
| `No image attachments` | The sender may be embedding images inline rather than as attachments. Check `Content-Disposition` in the raw email. |
| IMAP login fails with Gmail | Enable 2FA and generate an App Password; use that as `MB_EMAIL_PASSWORD` |
| `img2pdf` not found | Install with `pip install img2pdf`, or the script will fall back to Pillow |
| County not detected | Add the sender's domain/subject keywords to `COUNTY_MAP` and `AGENCY_MAP` |

## Security notes

- Credentials live in `.env`, never in the script.
- The script only touches emails matching `UNSEEN SUBJECT "Blotter"`.
- Failed emails are left unread so they can be retried or inspected manually.
