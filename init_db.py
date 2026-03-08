"""
Database Schema Initialization for Montana Blotter
Creates all necessary tables with proper structure
"""

import sqlite3
import os
from datetime import datetime


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DB_PATH = os.getenv('MB_DB_PATH', '/root/montanablotter/blotter.db').strip() or '/root/montanablotter/blotter.db'
DB_BUSY_TIMEOUT_MS = _env_int('MB_DB_BUSY_TIMEOUT_MS', 30000)


def _configure_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f'PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}')
    # WAL prevents readers from blocking writers during ingestion.
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')


def _create_core_tables(cursor: sqlite3.Cursor) -> None:
    """Create the baseline tables required before running additive migrations."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            email TEXT,
            membership TEXT DEFAULT 'free',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blotters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            county TEXT NOT NULL,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            incident_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'processed',
            file_path TEXT,
            notes TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blotter_id INTEGER NOT NULL,
            cfs_number TEXT,
            date TEXT NOT NULL,
            time TEXT,
            incident TEXT NOT NULL DEFAULT '',
            incident_type TEXT,
            location TEXT,
            details TEXT,
            county TEXT NOT NULL,
            officer TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (blotter_id) REFERENCES blotters(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS command_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            timestamp TEXT,
            officer TEXT,
            entry TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_county ON records(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_date ON records(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_blotter ON records(blotter_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_cfs ON records(cfs_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_county_date_time ON records(county, date, time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blotters_county ON blotters(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blotters_date ON blotters(upload_date)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS auth_login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_lookup '
        'ON auth_login_attempts(username, ip_address, created_at)'
    )

def init_database():
    """Initialize the database with all required tables"""
    
    # Backup existing database if it exists
    if os.path.exists(DB_PATH):
        backup_path = f'{DB_PATH}.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        print(f"⚠️  Backing up existing database to: {backup_path}")
        os.system(f'cp {DB_PATH} {backup_path}')
    
    conn = sqlite3.connect(DB_PATH)
    _configure_sqlite(conn)
    cursor = conn.cursor()
    _create_core_tables(cursor)
    
    conn.commit()
    conn.close()
    
    print("✅ Database initialized successfully!")
    print(f"📁 Location: {DB_PATH}")
    print("\nTables created:")
    print("  - users (authentication)")
    print("  - blotters (PDF batch tracking)")
    print("  - records (individual incidents)")
    print("  - command_logs (detailed event logs)")

def migrate():
    """Safely apply schema changes to an existing DB without data loss"""
    conn = sqlite3.connect(DB_PATH)
    _configure_sqlite(conn)
    cursor = conn.cursor()
    _create_core_tables(cursor)

    # Add source_type column to blotters if it doesn't exist
    try:
        cursor.execute("ALTER TABLE blotters ADD COLUMN source_type TEXT DEFAULT 'pdf'")
        print("✅ Added source_type column to blotters")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add file_path column to blotters if missing
    try:
        cursor.execute("ALTER TABLE blotters ADD COLUMN file_path TEXT")
        print("✅ Added file_path column to blotters")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Add missing columns to records (old schema used 'incident' instead of 'incident_type')
    for col, definition in [
        ('incident',      "TEXT NOT NULL DEFAULT ''"),
        ('incident_type', 'TEXT'),
        ('cfs_number',    'TEXT'),
        ('time',          'TEXT'),
        ('officer',       'TEXT'),
    ]:
        try:
            cursor.execute(f"ALTER TABLE records ADD COLUMN {col} {definition}")
            print(f"✅ Added {col} column to records")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Recreate posts table with record_id nullable (posts are now blotter-level digests)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER,
            blotter_id INTEGER NOT NULL,
            title TEXT,
            summary TEXT,
            city TEXT,
            county TEXT,
            agency_type TEXT DEFAULT 'other',
            agency_name TEXT,
            incident_date TEXT,
            incident_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (blotter_id) REFERENCES blotters(id) ON DELETE CASCADE
        )
    ''')
    # If old posts table had NOT NULL on record_id, drop and recreate it
    record_id_col = cursor.execute(
        "SELECT [notnull] FROM pragma_table_info('posts') WHERE name='record_id'"
    ).fetchone()
    if record_id_col and record_id_col[0] == 1:
        print("Recreating posts table (removing NOT NULL on record_id)...")
        cursor.execute('DROP TABLE posts')
        cursor.execute('''
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER,
                blotter_id INTEGER NOT NULL,
                title TEXT,
                summary TEXT,
                city TEXT,
                county TEXT,
                agency_type TEXT DEFAULT 'other',
                agency_name TEXT,
                incident_date TEXT,
                incident_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (blotter_id) REFERENCES blotters(id) ON DELETE CASCADE
            )
        ''')

    # Indexes on posts
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_county ON posts(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_city ON posts(city)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_agency_type ON posts(agency_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_incident_date ON posts(incident_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_cfs ON records(cfs_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_county_date_time ON records(county, date, time)')

    # Blog posts table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blog_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            body TEXT NOT NULL,
            excerpt TEXT,
            author TEXT DEFAULT 'Montana Blotter',
            published INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blog_slug ON blog_posts(slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blog_published ON blog_posts(published)')

    # Subscribers table for public email digest
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            counties TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Emailed agencies — tracks which agencies have been contacted so duplicates are skipped
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emailed_agencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_name TEXT NOT NULL,
            email_address TEXT NOT NULL,
            subject TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_emailed_agency ON emailed_agencies(agency_name)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS source_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_message_id TEXT,
            source_sender TEXT,
            source_subject TEXT,
            source_received_at TEXT,
            filename TEXT,
            content_sha256 TEXT NOT NULL,
            storage_path TEXT,
            raw_text TEXT,
            extraction_method TEXT,
            extraction_warnings TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_type, content_sha256)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_document_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            last_error TEXT,
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT,
            FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE CASCADE,
            UNIQUE(source_document_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pipeline_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingestion_job_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (ingestion_job_id) REFERENCES ingestion_jobs(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_documents_sha ON source_documents(content_sha256)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pipeline_events_job_stage ON pipeline_events(ingestion_job_id, stage)')

    try:
        cursor.execute('ALTER TABLE blotters ADD COLUMN source_document_id INTEGER')
        print('✅ Added source_document_id column to blotters')
    except sqlite3.OperationalError:
        pass  # Column already exists

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blotters_source_document ON blotters(source_document_id)')
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_blotters_source_document_unique ON blotters(source_document_id) "
        "WHERE source_document_id IS NOT NULL"
    )

    # Page views table for visitor analytics
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS page_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_page_views_path ON page_views(path)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pattern_clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_slug TEXT NOT NULL,
            county_slug TEXT,
            target_path TEXT NOT NULL,
            placement TEXT NOT NULL,
            source_path TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pattern_clicks_created ON pattern_clicks(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pattern_clicks_placement ON pattern_clicks(placement)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pattern_clicks_target_path ON pattern_clicks(target_path)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribe_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            source TEXT,
            page_path TEXT,
            ip_hash TEXT,
            referrer TEXT,
            email_hash TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_created ON subscribe_events(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_type ON subscribe_events(event_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_source ON subscribe_events(source)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'usd',
            email_hash TEXT,
            donor_name TEXT,
            message TEXT,
            source TEXT,
            provider_session_id TEXT UNIQUE,
            provider_payment_intent_id TEXT UNIQUE,
            provider_subscription_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donations_created ON donations(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donations_status ON donations(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donations_mode ON donations(mode)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS donation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            source TEXT,
            page_path TEXT,
            ip_hash TEXT,
            referrer TEXT,
            amount_cents INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donation_events_created ON donation_events(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donation_events_type ON donation_events(event_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_donation_events_source ON donation_events(source)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            processed_at TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_webhook_events_provider_created ON payment_webhook_events(provider, created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_webhook_events_processed ON payment_webhook_events(processed)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_ad_inquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            contact_name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            website_url TEXT,
            license_number TEXT NOT NULL,
            counties_served TEXT NOT NULL,
            package_interest TEXT,
            monthly_budget_cents INTEGER,
            message TEXT,
            source TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ads_created ON bail_ad_inquiries(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ads_status ON bail_ad_inquiries(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ads_package ON bail_ad_inquiries(package_interest)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_ad_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inquiry_id INTEGER,
            business_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website_url TEXT,
            license_number TEXT,
            county_targets TEXT,
            package_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL DEFAULT 'monthly',
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'usd',
            source TEXT,
            status TEXT NOT NULL DEFAULT 'checkout_pending',
            provider TEXT NOT NULL DEFAULT 'stripe',
            provider_session_id TEXT UNIQUE,
            provider_subscription_id TEXT UNIQUE,
            provider_customer_id TEXT,
            onboarding_token TEXT UNIQUE,
            notes TEXT,
            paid_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (inquiry_id) REFERENCES bail_ad_inquiries(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_orders_created ON bail_ad_orders(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_orders_status ON bail_ad_orders(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_orders_package ON bail_ad_orders(package_id)')
    try:
        cursor.execute("ALTER TABLE bail_ad_orders ADD COLUMN add_on_ids TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_ad_creatives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL UNIQUE,
            headline TEXT NOT NULL,
            body_copy TEXT NOT NULL,
            cta_text TEXT,
            target_url TEXT NOT NULL,
            logo_path TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES bail_ad_orders(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_creatives_status ON bail_ad_creatives(status)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_ad_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            county TEXT NOT NULL,
            slot_type TEXT NOT NULL DEFAULT 'county_feature',
            status TEXT NOT NULL DEFAULT 'pending',
            starts_at TEXT,
            ends_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES bail_ad_orders(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_bail_ad_slots_order_county ON bail_ad_slots(order_id, county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_slots_county_status ON bail_ad_slots(county, status)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_ad_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            slot_id INTEGER,
            event_type TEXT NOT NULL,
            county TEXT,
            source TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES bail_ad_orders(id) ON DELETE SET NULL,
            FOREIGN KEY (slot_id) REFERENCES bail_ad_slots(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_events_created ON bail_ad_events(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_events_type ON bail_ad_events(event_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_events_order ON bail_ad_events(order_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_events_county ON bail_ad_events(county)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_consumer_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            county TEXT NOT NULL,
            jail_facility TEXT,
            callback_preference TEXT,
            notes TEXT,
            source TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            routed_order_ids TEXT,
            routed_business_names TEXT,
            routed_emails TEXT,
            routed_phones TEXT,
            ip_hash TEXT,
            referrer TEXT,
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_created ON bail_consumer_leads(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_status ON bail_consumer_leads(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_county ON bail_consumer_leads(county)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_consumer_lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            county TEXT,
            source TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES bail_consumer_leads(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_created ON bail_consumer_lead_events(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_type ON bail_consumer_lead_events(event_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_county ON bail_consumer_lead_events(county)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_agency_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            agency_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            counties TEXT,
            source TEXT,
            outreach_status TEXT NOT NULL DEFAULT 'new',
            last_contacted_at TEXT,
            next_follow_up_at TEXT,
            owner TEXT,
            email_subject_template TEXT,
            email_body_template TEXT,
            call_script_template TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_status ON bail_agency_outreach(outreach_status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_followup ON bail_agency_outreach(next_follow_up_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_name ON bail_agency_outreach(agency_name)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bail_agency_email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER,
            agency_name TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            email_kind TEXT NOT NULL,
            subject TEXT,
            body_preview TEXT,
            sent_by TEXT,
            send_status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES bail_agency_outreach(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_created ON bail_agency_email_logs(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_agency ON bail_agency_email_logs(agency_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_status ON bail_agency_email_logs(send_status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_kind ON bail_agency_email_logs(email_kind)')

    # Generic app settings key/value storage
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Facebook publishing queue (MVP social autopost pipeline)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS facebook_post_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'queued',
            custom_message TEXT,
            enqueue_source TEXT DEFAULT 'manual',
            scheduled_for TEXT DEFAULT (datetime('now')),
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            facebook_post_id TEXT,
            last_error TEXT,
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            posted_at TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fb_queue_status_time ON facebook_post_queue(status, scheduled_for)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fb_queue_post ON facebook_post_queue(post_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fb_queue_created ON facebook_post_queue(created_at)')

    # Case status for status indicator dots
    try:
        cursor.execute("ALTER TABLE posts ADD COLUMN case_status TEXT DEFAULT 'pending'")
        print('✅ Added posts.case_status')
    except sqlite3.OperationalError:
        pass
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_posts_case_status ON posts(case_status)")

    # Blotter Auditor columns on posts table
    for col, definition in [
        ('audit_status',     "TEXT DEFAULT 'pending'"),
        ('pii_flags',        'TEXT'),
        ('meta_description', 'TEXT'),
        ('audited_at',       'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE posts ADD COLUMN {col} {definition}')
            print(f'✅ Added posts.{col}')
        except sqlite3.OperationalError:
            pass  # Already exists
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_audit_status ON posts(audit_status)')

    conn.commit()
    conn.close()
    print("✅ Migration complete")


if __name__ == "__main__":
    init_database()
    migrate()
