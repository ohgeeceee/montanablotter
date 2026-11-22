# Setting up support@mtblotter.com via Zoho Mail (free tier)

DNS is hosted on Cloudflare. Zoho requires domain verification + MX records.

## Step 1 — Sign up at Zoho
1. Go to https://www.zoho.com/mail/ → "Sign Up Free".
2. Create account, choose "Add your own domain": mtblotter.com.
3. Zoho will give a TXT verification record like:
   `zoho-verification=zb...` (value shown in Zoho admin).

## Step 2 — Add verification TXT in Cloudflare
Cloudflare → mtblotter.com → DNS → Add record:
- Type: TXT
- Name: @ (or mtblotter.com)
- Content: zoho-verification=zb... (from Zoho)
- TTL: Auto
Wait for Zoho to verify (usually minutes).

## Step 3 — Add MX records (Cloudflare DNS)
| Type | Name | Content | Priority | TTL |
|------|------|---------|----------|-----|
| MX | @ | mx.zoho.com | 10 | Auto |
| MX | @ | mx2.zoho.com | 20 | Auto |
| MX | @ | mx3.zoho.com | 50 | Auto |

Remove any existing MX records for @ first.

## Step 4 — SPF (TXT)
- Name: @
- Content: v=spf1 include:zoho.com ~all

## Step 5 — DKIM (TXT)
In Zoho admin → Mail → DKIM → Generate. You'll get a selector (e.g. `zoho`) and a long TXT value.
- Name: zoho._domainkey
- Content: (paste the long value from Zoho)

## Step 6 — DMARC (TXT)
- Name: _dmarc
- Content: v=DMARC1; p=none; rua=mailto:support@mtblotter.com

## Step 7 — Create mailbox
In Zoho admin → Users → Add User:
- Email: support@mtblotter.com
- Set password.

## Step 8 — Verify
- Send a test email to support@mtblotter.com from an external address.
- Reply from support@mtblotter.com.
- Check spam score at https://www.mail-tester.com.

## Notes
- Free tier limits: 5 users, 5GB/user, webmail only (no IMAP/POP on free tier for custom domains? Actually Zoho free tier supports IMAP/POP with limits). For a support inbox, webmail + mobile app is fine.
- If you want IMAP/SMTP access for the app, upgrade to Zoho Mail Lite ($1/user/mo) or use a transactional provider for outbound.
- Keep Cloudflare proxy OFF (grey cloud) for MX records — MX must be DNS-only.
