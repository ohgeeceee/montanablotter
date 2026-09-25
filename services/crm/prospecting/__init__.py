"""CRM prospecting package — lead discovery + dedupe against the live pipeline.

Entry point: python3 -m services.crm.prospecting --vertical all [--county X]
             [--dry-run] [--limit N]

Writes ONLY to advertising_prospects / advertising_prospect_sources
(existing CRM tables) — never to orders, listings, or anything public.
"""
