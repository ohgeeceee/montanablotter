"""Copy and HTML rendering for the paid daily jail-roster promotion."""
from __future__ import annotations

from html import escape


CAMPAIGN_NAME = 'Daily Jail Roster - Subscriber Upgrade'
SUBJECT = 'New Montana jail bookings, delivered in one daily email'

PLAIN_BODY = """Hi [Name],

You already follow Montana Blotter by email. Now you can have new jail bookings delivered in one daily roster instead of checking county sites yourself.

Blotter Plus — $7.99/month
  - Daily new-booking emails for up to 5 counties
  - 12 months of searchable history
  - Name and keyword watchlists

Blotter Pro — $26/month
  - One statewide daily jail-roster email
  - New bookings from every Montana county feed currently available
  - Full archive access, case tracking, and exports

Choose your plan: [PricingURL]

Roster availability follows each county's official publishing schedule. A booking is an allegation, not proof of guilt.

You received this because you subscribed to Montana Blotter emails.
Unsubscribe: [UnsubscribeURL]

Montana Blotter
Public records, made useful
"""


def build_html_body(name: str, pricing_url: str, unsubscribe_url: str) -> str:
    """Return a compact, email-client-safe HTML version of the promotion."""
    safe_name = escape(name or 'there')
    safe_pricing_url = escape(pricing_url, quote=True)
    safe_unsubscribe_url = escape(unsubscribe_url, quote=True)
    return f'''<!doctype html>
<html><body style="margin:0;background:#f1f5f9;font-family:Arial,sans-serif;color:#0f172a">
<div style="max-width:620px;margin:0 auto;padding:28px 16px">
  <div style="background:#0f172a;border-radius:16px;padding:28px;color:#fff">
    <div style="font-size:12px;font-weight:700;letter-spacing:.12em;color:#93c5fd;text-transform:uppercase">Montana Blotter</div>
    <h1 style="font-size:28px;line-height:1.2;margin:10px 0 12px">New jail bookings in one daily email</h1>
    <p style="font-size:16px;line-height:1.6;margin:0;color:#cbd5e1">Hi {safe_name}, get new Montana jail bookings without checking county sites yourself.</p>
  </div>
  <div style="background:#fff;padding:26px;border-radius:0 0 16px 16px;border:1px solid #e2e8f0">
    <h2 style="font-size:19px;margin:0 0 8px">Blotter Plus — $7.99/month</h2>
    <p style="line-height:1.6;margin:0 0 20px;color:#475569">Daily new-booking emails for up to 5 counties, 12 months of searchable history, and name or keyword watchlists.</p>
    <h2 style="font-size:19px;margin:0 0 8px">Blotter Pro — $26/month</h2>
    <p style="line-height:1.6;margin:0 0 24px;color:#475569">One statewide daily roster using every Montana county feed currently available, plus the full archive, case tracking, and exports.</p>
    <p style="margin:0 0 24px"><a href="{safe_pricing_url}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;font-weight:700;padding:13px 20px;border-radius:10px">Choose a plan</a></p>
    <p style="font-size:12px;line-height:1.5;color:#64748b">Roster availability follows each county's official publishing schedule. A booking is an allegation, not proof of guilt.</p>
    <hr style="border:0;border-top:1px solid #e2e8f0;margin:22px 0">
    <p style="font-size:11px;line-height:1.5;color:#94a3b8">You received this because you subscribed to Montana Blotter emails. <a href="{safe_unsubscribe_url}" style="color:#64748b">Unsubscribe</a>.</p>
  </div>
</div>
</body></html>'''
