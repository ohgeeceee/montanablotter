# Email Image Blotter Setup Summary

## What was done

1. **Created `/root/montanablotter/email_image_blotter.py`**
   - Extends the existing `EmailWorker` class to handle image attachments
   - Converts image attachments (jpg, png, etc.) to PDF using Pillow
   - Detects county/agency from email subject/sender via keyword maps
   - Ingests through the existing `processor.py` pipeline with full deduplication
   - Marks processed emails as read and moves them to `Processed` folder

2. **Updated `.env`**
   - Reverted to working IONOS credentials (`records@montanablotter.com`)
   - Gmail App Password was failing authentication (needs 2FA + fresh App Password)

3. **Added cron job**
   - Runs every 15 minutes via `job_runner.py` for logging/timeout handling
   - Log file: `/root/montanablotter/email_image_blotter.log`

## Files

| File | Purpose |
|------|---------|
| `/root/montanablotter/email_image_blotter.py` | Main script |
| `/root/montanablotter/email_blotter_ingest.py` | Original standalone script (kept for reference) |
| `/root/montanablotter/scripts/ops/email_blotter_cron.sh` | Cron wrapper (optional) |
| `/root/montanablotter/docs/email_blotter_ingest.md` | Documentation |

## How it works

1. Polls IMAP inbox for **unread** emails
2. Checks for PDF attachments first (uses existing `email_worker.py` logic)
3. If no PDF, checks for **image attachments** (jpg, png, gif, bmp, tiff, webp)
4. Converts images to PDF using Pillow (or img2pdf if installed)
5. Deduplicates via `source_documents` SHA-256 hash
6. Calls `process_new_blotter()` to parse, store incidents, generate posts, dispatch alerts
7. Moves email to `Processed` folder

## County/Agency Detection

Edit these dictionaries in `email_image_blotter.py` to match your senders:

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

## Test run

```bash
cd /root/montanablotter
source venv/bin/activate
python3 email_image_blotter.py
```

## Logs

- Manual run: stdout
- Cron run: `/root/montanablotter/email_image_blotter.log`

## To use Gmail instead of IONOS

If you want to switch to `montanblotter@gmail.com` later:

1. Enable 2-Step Verification at https://myaccount.google.com/security
2. Generate fresh App Password at https://myaccount.google.com/apppasswords
3. Update `.env`:
   ```
   MB_EMAIL_USER=montanblotter@gmail.com
   MB_EMAIL_PASSWORD=yournew16charapppassword
   MB_IMAP_SERVER=imap.gmail.com
   ```
4. Test: `python3 email_image_blotter.py`

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `No image attachments` | Sender may embed images inline. Check raw email `Content-Disposition` |
| County not detected | Add sender keywords to `COUNTY_MAP`/`AGENCY_MAP` |
| `img2pdf not installed` | Falls back to Pillow automatically. Optional: `pip install img2pdf` |
| IMAP login fails | Check credentials in `.env`, verify IMAP enabled on mail provider |
