"""CRM schema extensions — leads, client portals, email logs, billing rollup.

Idempotent, non-destructive, follows the repo's ensure_*_schema +
try/except ALTER TABLE ADD COLUMN pattern (AGENTS.md: migrations live in
init_db.migrate()). Draft for review; to be applied by appending
ensure_crm_schema(conn) to migrate() in init_db.py.

Stack note: production DB is SQLite (blotter.db), NOT PostgreSQL/Supabase.
The requested Postgres schema is translated 1:1 to SQLite semantics here.
"""
from __future__ import annotations

import secrets


def _add_column(conn, table, col, decl):
    try:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')
    except Exception:  # sqlite3.OperationalError: duplicate column
        pass


def ensure_crm_schema(conn) -> None:
    # ------------------------------------------------------------------
    # 1. LEADS — extend the existing advertising_prospects pipeline.
    #    Pre-existing columns already cover: id, business_name,
    #    business_key (UNIQUE dedupe), contact_name, email, phone,
    #    counties, category, stage (new/contacted/proposal_sent/active/
    #    lost/paused), next_follow_up, notes, created/updated_at.
    #    The requested `leads` table maps onto this; we add only what is
    #    missing. A parallel `leads` table would duplicate a live surface.
    # ------------------------------------------------------------------
    _add_column(conn, 'advertising_prospects', 'target_vertical',
                "TEXT NOT NULL DEFAULT 'general'")  # bail|legal|pi|process_server|recovery|general
    _add_column(conn, 'advertising_prospects', 'source',
                "TEXT NOT NULL DEFAULT 'manual'")    # manual|sales_inquiry|prospect_engine|sosmt|google_local|state_directory|import
    _add_column(conn, 'advertising_prospects', 'website', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'advertising_prospects', 'license_number', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'advertising_prospects', 'address', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'advertising_prospects', 'city', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'advertising_prospects', 'qualified_at', 'TEXT')
    _add_column(conn, 'advertising_prospects', 'converted_at', 'TEXT')
    # Engine ingest needs a stable natural key for registry rows that have
    # no reliable business_name fuzzy match.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ad_prospect_ext_key "
                 "ON advertising_prospects(category, license_number) "
                 "WHERE license_number != ''")
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_ad_prospect_vertical
                    ON advertising_prospects(target_vertical, stage)''')

    # ------------------------------------------------------------------
    # 2. CLIENT PORTALS — tokened self-service portal per converted lead.
    #    Same auth model as bail_ad_orders.onboarding_token, generalized
    #    across verticals so one portal covers banner + listing + county
    #    sponsorship for the same firm.
    # ------------------------------------------------------------------
    conn.execute('''CREATE TABLE IF NOT EXISTS client_portals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL REFERENCES advertising_prospects(id) ON DELETE CASCADE,
        access_token TEXT NOT NULL UNIQUE,
        slug TEXT UNIQUE,                          -- optional vanity URL; token always works
        tier TEXT NOT NULL DEFAULT 'banner'
            CHECK(tier IN ('banner','featured_listing','exclusive_county_sponsor')),
        portal_status TEXT NOT NULL DEFAULT 'invited'
            CHECK(portal_status IN ('invited','active','suspended','closed')),
        stripe_customer_id TEXT NOT NULL DEFAULT '',
        primary_order_table TEXT NOT NULL DEFAULT '',
        primary_order_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )''')
    _add_column(conn, 'client_portals', 'features',
                "TEXT NOT NULL DEFAULT 'listing_editor,metrics,lead_receipt,billing'")
    # Profile & Directory Listing Editor content (self-service fields).
    # These live on the portal, NOT on the provider order row, so a client
    # edit never mutates billing data; the public directory renderer joins
    # on client_portals to pick up sponsored overrides.
    _add_column(conn, 'client_portals', 'logo_url', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'client_portals', 'bio', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'client_portals', 'display_phone', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'client_portals', 'direct_line', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'client_portals', 'direct_link', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'client_portals', 'listing_updated_at', 'TEXT')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_client_portals_lead ON client_portals(lead_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_client_portals_status ON client_portals(portal_status)')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_client_portals_order
                    ON client_portals(primary_order_table, primary_order_id)''')

    # ------------------------------------------------------------------
    # 3b. METRICS SNAPSHOT — county-level exposure rollup per portal order.
    #     Raw events already live in bail_ad_events / lawyer_listing_events
    #     (impression|click|call|text|lead, county, occurred_at). The lead
    #     receipt can't be a view because routed_order_ids is a comma list
    #     (SQLite has no portable split-join); metrics_snapshot caches it.
    # ------------------------------------------------------------------
    conn.execute('''CREATE TABLE IF NOT EXISTS crm_metrics_snapshot (
        portal_id INTEGER NOT NULL REFERENCES client_portals(id) ON DELETE CASCADE,
        day TEXT NOT NULL,                 -- YYYY-MM-DD
        county TEXT NOT NULL DEFAULT '',
        impressions INTEGER NOT NULL DEFAULT 0,
        clicks INTEGER NOT NULL DEFAULT 0,
        calls INTEGER NOT NULL DEFAULT 0,
        leads INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (portal_id, day, county)
    )''')


    # ------------------------------------------------------------------
    # 3. EMAIL LOGS — generic in/out log per lead.
    #    (bail_agency_email_logs stays as-is; it is bail-agency-outreach
    #    specific and joined to bail_agency_outreach.id.)
    # ------------------------------------------------------------------
    conn.execute('''CREATE TABLE IF NOT EXISTS crm_email_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL REFERENCES advertising_prospects(id) ON DELETE CASCADE,
        direction TEXT NOT NULL DEFAULT 'outbound' CHECK(direction IN ('inbound','outbound')),
        subject TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        from_address TEXT NOT NULL DEFAULT '',
        to_address TEXT NOT NULL DEFAULT '',
        sent_at TEXT NOT NULL DEFAULT (datetime('now')),
        tracking_id TEXT UNIQUE,                   -- Message-ID / SMTP id / vendor id
        provider TEXT NOT NULL DEFAULT '',         -- smtp|stripe_email|manual
        send_status TEXT NOT NULL DEFAULT 'logged', -- logged|sent|bounced|failed
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_crm_email_logs_lead ON crm_email_logs(lead_id, sent_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_crm_email_logs_tracking ON crm_email_logs(tracking_id) WHERE tracking_id IS NOT NULL')

    # ------------------------------------------------------------------
    # 4. SUBSCRIPTIONS & INVOICES — rollup VIEW over the per-directory
    #    billing tables, not a new source of truth (Stripe is authoritative;
    #    lawyer_ad_invoices is the canonical invoice pattern; bail/lawyer
    #    orders carry provider_customer_id / provider_subscription_id).
    #    Views keep this read-only so no code can write twice to one
    #    subscription.
    # ------------------------------------------------------------------
    conn.execute('DROP VIEW IF EXISTS crm_subscriptions')
    conn.execute('''CREATE VIEW crm_subscriptions AS
        SELECT 'lawyer'  AS vertical, o.id AS order_id, p.id AS portal_id,
               o.email AS client_email, o.firm_name AS client_name,
               o.provider_customer_id AS stripe_customer_id,
               o.provider_subscription_id AS stripe_subscription_id,
               o.amount_cents AS amount_cents, o.currency AS currency,
               o.status AS status, o.billing_cycle AS billing_cycle,
               o.updated_at AS last_synced_at
          FROM lawyer_ad_orders o LEFT JOIN client_portals p
            ON p.primary_order_table='lawyer_ad_orders' AND p.primary_order_id=o.id
         WHERE o.provider_subscription_id IS NOT NULL
        UNION ALL
        SELECT 'bail', o.id, p.id, o.email, o.business_name,
               o.provider_customer_id, o.provider_subscription_id,
               o.amount_cents, o.currency, o.status, o.billing_cycle, o.updated_at
          FROM bail_ad_orders o LEFT JOIN client_portals p
            ON p.primary_order_table='bail_ad_orders' AND p.primary_order_id=o.id
         WHERE o.provider_subscription_id IS NOT NULL''')

    conn.execute('DROP VIEW IF EXISTS crm_invoices')
    conn.execute('''CREATE VIEW crm_invoices AS
        SELECT i.id AS invoice_id, 'lawyer' AS vertical, o.id AS order_id,
               o.firm_name AS client_name, i.invoice_number, i.amount_cents,
               i.currency, i.status, i.period_start, i.period_end,
               i.issued_at, i.paid_at, i.provider_invoice_id
          FROM lawyer_ad_invoices i JOIN lawyer_ad_orders o ON o.id = i.order_id''')

    conn.commit()


def new_portal_token() -> str:
    """URL-safe high-entropy token for /portal/<token>. 256 bits."""
    return secrets.token_urlsafe(32)


if __name__ == '__main__':
    import sqlite3
    import sys
    conn = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else ':memory:')
    conn.row_factory = sqlite3.Row
    ensure_crm_schema(conn)
    print('OK — crm schema applied (idempotent re-run follows)')
    ensure_crm_schema(conn)
    print('OK — second run clean')
