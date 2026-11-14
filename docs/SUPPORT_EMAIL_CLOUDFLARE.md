# Setting up support@mtblotter.com via Cloudflare Email Routing (free)

Cloudflare Email Routing forwards inbound mail for a custom address (e.g. support@mtblotter.com)
to a personal inbox (Gmail/Outlook) at no cost. No mailbox hosting needed.

## Prerequisites
- Domain mtblotter.com already on Cloudflare DNS (it is).
- A personal destination inbox (e.g. your Gmail) to receive forwarded mail.

## Step 1 — Enable Email Routing
1. Cloudflare dashboard → mtblotter.com → Email → Email Routing.
2. Click "Get started" / "Enable Email Routing".
3. Cloudflare will auto-add the required MX + TXT (SPF) records. Approve them.
   (These replace any existing MX records — remove old ones first if migrating.)

## Step 2 — Verify destination address
1. In Email Routing → Destination addresses → Add destination.
2. Enter your personal Gmail/Outlook address.
3. Click the verification link sent to that inbox.

## Step 3 — Create the custom alias
1. Email Routing → Routing rules → Create address.
2. Custom address: support
3. Action: Send to → your verified destination address.
4. Save. (Catch-all optional: route *@mtblotter.com → destination.)

## Step 4 — (Optional) Free SMTP sending
Cloudflare Email Routing is receive-only. To SEND as support@mtblotter.com:
- Option A: Use Gmail "Send mail as" with an SMTP provider (Mailgun/SendGrid/Amazon SES) — add SPF/DKIM for that provider.
- Option B: Use a transactional provider (Resend, Postmark) for outbound only.
- Option C: Keep Zoho/Mailgun for outbound SMTP if you also need to reply from support@.

## Step 5 — Test
Send an email from an external address to support@mtblotter.com; confirm it arrives in your personal inbox. Reply from your personal inbox (the From will be your personal address unless you configure "Send as" in Gmail).

## Notes
- Cloudflare Email Routing is receive/forward only — no hosted mailbox, no IMAP.
- For a real support inbox with replies from support@, pair with an SMTP provider or use Zoho.
- Catch-all rule is useful so any address at mtblotter.com forwards.
