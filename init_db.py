"""
Database Schema Initialization for Montana Blotter
Creates all necessary tables with proper structure
"""

import sqlite3
import os
from datetime import datetime

from services.api.auth import ensure_api_auth_schema
from services.admin.case_journeys import ensure_case_journey_schema, seed_case_journeys
from services.court.tracker import ensure_court_tracker_schema
from services.alerts.bail_bonds import ensure_bail_bonds_alert_schema
from services.monetization.bondsman import ensure_bondsman_command_center_schema
from services.agents.mission_control import ensure_agent_mission_control_schema
from services.alerts.incidents import ensure_incident_notification_schema
from services.persons.missing import ensure_missing_person_schema
from services.meetings.public import ensure_public_meeting_schema
from services.ingestion.warrants.models import ensure_warrant_schema
from services.ops.county_inventory_persistence import ensure_county_inventory_schema

def _safe_add_column(cursor: 'sqlite3.Cursor', table: str, col: str, definition: str) -> bool:
    """ALTER TABLE … ADD COLUMN, silencing only 'duplicate column' errors.

    Returns True if the column was added, False if it already existed.
    Re-raises any other OperationalError so real failures aren't swallowed.
    """
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col} {definition}')
        return True
    except sqlite3.OperationalError as exc:
        if 'duplicate column' in str(exc).lower():
            return False
        raise


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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            ip_address TEXT,
            metadata_json TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)')


def ensure_public_engagement_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS public_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            subscription_counties TEXT DEFAULT '',
            subscribe_digest INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_login_at TEXT,
            facebook_id TEXT UNIQUE
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_public_users_email ON public_users(email)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_public_users_active ON public_users(is_active)')

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS public_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            content_id TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            moderation_note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_comments_thread '
        'ON public_comments(content_type, content_id, status, created_at)'
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_public_comments_user ON public_comments(public_user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_public_comments_status ON public_comments(status)')

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            ip_address TEXT,
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_hash '
        'ON password_reset_tokens(token_hash)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user '
        'ON password_reset_tokens(public_user_id, created_at)'
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS public_user_api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_used_at TEXT,
            revoked_at TEXT,
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_user_api_tokens_hash '
        'ON public_user_api_tokens(token_hash)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_user_api_tokens_user '
        'ON public_user_api_tokens(public_user_id, is_active)'
    )

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS public_user_saved_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(public_user_id, post_id),
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_user_saved_posts_user '
        'ON public_user_saved_posts(public_user_id, created_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_public_user_saved_posts_post '
        'ON public_user_saved_posts(post_id)'
    )

    for col, definition in [
        ('subscription_counties', "TEXT DEFAULT ''"),
        ('subscribe_digest', 'INTEGER NOT NULL DEFAULT 0'),
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_login_at', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE public_users ADD COLUMN {col} {definition}')
            print(f'✅ Added public_users.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('moderation_note', 'TEXT'),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE public_comments ADD COLUMN {col} {definition}')
            print(f'✅ Added public_comments.{col}')
        except sqlite3.OperationalError:
            pass

def ensure_jail_booking_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS jail_booking_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county_slug TEXT UNIQUE NOT NULL,
            county_name TEXT NOT NULL,
            facility_name TEXT NOT NULL,
            roster_url TEXT,
            phone TEXT,
            source_type TEXT NOT NULL DEFAULT 'official_roster',
            coverage_tier TEXT NOT NULL DEFAULT 'standard',
            is_enabled INTEGER NOT NULL DEFAULT 1,
            is_featured INTEGER NOT NULL DEFAULT 0,
            last_checked_at TEXT,
            last_success_at TEXT,
            latest_error TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS jail_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            county_slug TEXT NOT NULL,
            county_name TEXT NOT NULL,
            facility_name TEXT NOT NULL,
            person_name TEXT NOT NULL,
            age INTEGER,
            booking_number TEXT,
            booking_at TEXT,
            release_at TEXT,
            charges_summary TEXT DEFAULT '',
            charges_json TEXT,
            arresting_agency TEXT,
            source_url TEXT,
            source_record_id TEXT,
            booking_status TEXT NOT NULL DEFAULT 'current',
            is_current INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES jail_booking_sources(id) ON DELETE SET NULL
        )
        '''
    )
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS jail_booking_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER,
            run_type TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'success',
            fetched_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            notes TEXT DEFAULT '',
            FOREIGN KEY (source_id) REFERENCES jail_booking_sources(id) ON DELETE SET NULL
        )
        '''
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_booking_sources_featured '
        'ON jail_booking_sources(is_featured, is_enabled, county_name)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_lookup '
        'ON jail_bookings(county_slug, is_current, booking_at, first_seen_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_source '
        'ON jail_bookings(source_id, last_seen_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_person '
        'ON jail_bookings(person_name, booking_number)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_booking_runs_source '
        'ON jail_booking_runs(source_id, started_at)'
    )

    for col, definition in [
        ('coverage_tier', "TEXT NOT NULL DEFAULT 'standard'"),
        ('is_enabled', 'INTEGER NOT NULL DEFAULT 1'),
        ('is_featured', 'INTEGER NOT NULL DEFAULT 0'),
        ('last_checked_at', 'TEXT'),
        ('last_success_at', 'TEXT'),
        ('latest_error', "TEXT DEFAULT ''"),
        ('notes', "TEXT DEFAULT ''"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE jail_booking_sources ADD COLUMN {col} {definition}')
            print(f'✅ Added jail_booking_sources.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('source_id', 'INTEGER'),
        ('facility_name', "TEXT NOT NULL DEFAULT ''"),
        ('booking_number', 'TEXT'),
        ('release_at', 'TEXT'),
        ('charges_json', 'TEXT'),
        ('arresting_agency', 'TEXT'),
        ('source_url', 'TEXT'),
        ('source_record_id', 'TEXT'),
        ('hash_id', 'TEXT'),
        ('raw_json', 'TEXT'),
        ('booking_status', "TEXT NOT NULL DEFAULT 'current'"),
        ('is_current', 'INTEGER NOT NULL DEFAULT 1'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('notes', "TEXT DEFAULT ''"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
        ('name_slug', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE jail_bookings ADD COLUMN {col} {definition}')
            print(f'✅ Added jail_bookings.{col}')
        except sqlite3.OperationalError:
            pass

    cursor.execute(
        """
        UPDATE jail_bookings
        SET name_slug = LOWER(
            REPLACE(REPLACE(REPLACE(REPLACE(TRIM(person_name), ' ', '-'), '.', ''), \"'\", ''), ',', '')
        )
        WHERE name_slug IS NULL AND person_name IS NOT NULL AND person_name != ''
        """
    )

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_hash_id '
        'ON jail_bookings(hash_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_name_slug '
        'ON jail_bookings(name_slug)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_source_record_id '
        'ON jail_bookings(source_record_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_jail_bookings_county_booking_at '
        'ON jail_bookings(county_slug, booking_at)'
    )

    for col, definition in [
        ('run_type', "TEXT NOT NULL DEFAULT 'manual'"),
        ('status', "TEXT NOT NULL DEFAULT 'success'"),
        ('fetched_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('new_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('updated_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('missing_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('started_at', "TEXT DEFAULT (datetime('now'))"),
        ('completed_at', 'TEXT'),
        ('notes', "TEXT DEFAULT ''"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE jail_booking_runs ADD COLUMN {col} {definition}')
            print(f'✅ Added jail_booking_runs.{col}')
        except sqlite3.OperationalError:
            pass

def init_database():
    """Initialize the database with all required tables"""
    
    # Backup existing database if it exists
    if os.path.exists(DB_PATH):
        backup_path = f'{DB_PATH}.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        print(f"⚠️  Backing up existing database to: {backup_path}")
        os.system(f'cp {DB_PATH} {backup_path}')
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    cursor = conn.cursor()
    _create_core_tables(cursor)
    # Create the subscribers table here so the ensure_* helpers below
    # (which ALTER TABLE subscribers to add opt-in columns) don't crash
    # on a fresh DB. The same CREATE TABLE exists in migrate() — the
    # duplication is intentional; both functions are documented to be
    # safe to run on a populated DB (CREATE TABLE IF NOT EXISTS, ALTER
    # is gated on PRAGMA table_info).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            counties TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            missing_person_email_opt_in INTEGER NOT NULL DEFAULT 1,
            missing_person_sms_opt_in INTEGER NOT NULL DEFAULT 0,
            missing_person_push_opt_in INTEGER NOT NULL DEFAULT 0,
            phone_verified_at TEXT DEFAULT ''
        )
    ''')
    ensure_source_material_schema(conn)
    ensure_public_meeting_schema(conn)
    ensure_public_engagement_schema(conn)
    ensure_missing_person_schema(conn)
    ensure_jail_booking_schema(conn)
    ensure_warrant_schema(conn)
    ensure_bondsman_command_center_schema(conn)
    ensure_court_tracker_schema(conn)
    ensure_agent_mission_control_schema(conn)
    ensure_api_auth_schema(conn)
    ensure_code_violation_schema(conn)
    ensure_license_sanction_schema(conn)
    seed_code_violation_sources(conn)
    ensure_civil_filing_schema(conn)
    seed_civil_filing_sources(conn)
    ensure_crash_incident_schema(conn)
    ensure_sex_offender_schema(conn)
    ensure_advertise_sales_lead_schema(conn)

    # Add lat/lon to meeting_locations for map display
    conn.commit()
    conn.close()

    print("✅ Database initialized successfully!")
    print(f"📁 Location: {DB_PATH}")
    print("\nTables created:")
    print("  - users (authentication)")
    print("  - blotters (PDF batch tracking)")
    print("  - records (individual incidents)")
    print("  - command_logs (detailed event logs)")
    print("  - code_violation_sources")
    print("  - property_addresses")
    print("  - code_violations")
    print("  - license_sanction_sources")
    print("  - license_sanctions")
    print("  - license_sanction_raw_extractions")
    print("  - civil_filing_sources")
    print("  - civil_filings")
    print("  - sex_offenders")
    print("  - sex_offender_snapshots")
    print("  - sex_offender_changes")
    print("  - sex_offender_alert_subscriptions")


def ensure_source_material_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
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
        CREATE TABLE IF NOT EXISTS source_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            display_name TEXT NOT NULL,
            base_url TEXT,
            adapter_name TEXT,
            poll_interval_seconds INTEGER NOT NULL DEFAULT 900,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS source_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_registry_id INTEGER NOT NULL,
            source_document_id INTEGER,
            artifact_kind TEXT NOT NULL DEFAULT 'raw',
            source_url TEXT,
            storage_path TEXT,
            content_sha256 TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_registry_id) REFERENCES source_registry(id) ON DELETE CASCADE,
            FOREIGN KEY (source_document_id) REFERENCES source_documents(id) ON DELETE SET NULL,
            UNIQUE(source_registry_id, artifact_kind, content_sha256)
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
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_registry_type_enabled ON source_registry(source_type, is_enabled)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_artifacts_registry ON source_artifacts(source_registry_id, fetched_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_artifacts_document ON source_artifacts(source_document_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pipeline_events_job_stage ON pipeline_events(ingestion_job_id, stage)')


def ensure_recovery_ad_schema(conn: sqlite3.Connection) -> None:
    """Create recovery_ad_orders and recovery_ad_listings tables if not present."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recovery_ad_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            center_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            package_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL DEFAULT 'monthly',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_session_id TEXT UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            activated_at TEXT,
            cancelled_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recovery_ad_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE NOT NULL REFERENCES recovery_ad_orders(id),
            tagline TEXT,
            description TEXT,
            services TEXT,
            city TEXT,
            county TEXT,
            logo_path TEXT,
            photo_path TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.execute(
        '''CREATE INDEX IF NOT EXISTS idx_recovery_ad_orders_status
           ON recovery_ad_orders(status)'''
    )
    conn.commit()


def ensure_lawyer_ad_schema(conn: sqlite3.Connection) -> None:
    """Create lawyer_ad_orders + lawyer_ad_listings + lawyer_consumer_leads tables.

    Mirror of the recovery_ad_* schema. Lawyers are a separate paid directory
    (lead-gen marketplace) from /attorneys, which stays free opt-in.

    Packages (Bronze / Silver / Gold) determine placement, branding, and lead
    routing. Stripe subscription webhooks drive status transitions.
    """
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_ad_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            bar_number TEXT,
            counties_served TEXT NOT NULL,
            practice_areas TEXT,
            package_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL DEFAULT 'monthly',
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'usd',
            provider TEXT NOT NULL DEFAULT 'stripe',
            provider_session_id TEXT UNIQUE,
            provider_subscription_id TEXT UNIQUE,
            provider_customer_id TEXT,
            status TEXT NOT NULL DEFAULT 'checkout_pending',
            onboarding_token TEXT UNIQUE,
            paid_at TEXT,
            cancelled_at TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_ad_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE NOT NULL REFERENCES lawyer_ad_orders(id),
            firm_name TEXT,
            tagline TEXT,
            description TEXT,
            practice_areas TEXT,
            counties_served TEXT,
            logo_path TEXT,
            photo_path TEXT,
            headline TEXT,
            body_copy TEXT,
            cta_text TEXT,
            target_url TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            calls INTEGER NOT NULL DEFAULT 0,
            leads INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_consumer_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            county TEXT NOT NULL,
            case_type TEXT,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'lawyers_directory',
            ip_hash TEXT,
            user_agent TEXT,
            routed_order_ids TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_consumer_lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES lawyer_consumer_leads(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            county TEXT,
            source TEXT,
            order_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_lead_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES lawyer_consumer_leads(id) ON DELETE CASCADE,
            order_id INTEGER NOT NULL REFERENCES lawyer_ad_orders(id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            destination TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            provider_message_id TEXT,
            error TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(lead_id, order_id, channel, destination)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_listing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES lawyer_ad_orders(id) ON DELETE CASCADE,
            listing_id INTEGER REFERENCES lawyer_ad_listings(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            ip_hash TEXT,
            user_agent_hash TEXT,
            county TEXT,
            session_hash TEXT,
            source TEXT,
            occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')
    # Drop the older full unique index from before this schema learned about
    # partial uniqueness. Safe to run on every migration.
    try:
        conn.execute('DROP INDEX IF EXISTS idx_lawyer_listing_event_dedupe')
    except sqlite3.OperationalError:
        pass
    # Deduped impressions: at most one per (order, IP, county, day). Partial
    # index keeps clicks/calls/leads from being blocked by the same uniqueness
    # rule — those are explicit user actions and must count every time.
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_lawyer_listing_event_dedupe
        ON lawyer_listing_events(order_id, ip_hash, county, date(occurred_at))
        WHERE event_type = 'impression'
    ''')
    for col, definition in [
        ('consent_at', 'TEXT'),
        ('consent_ip_hash', 'TEXT'),
        ('consent_text_version', "TEXT NOT NULL DEFAULT 'lawyer-lead-v1'"),
    ]:
        try:
            conn.execute(f'ALTER TABLE lawyer_consumer_leads ADD COLUMN {col} {definition}')
        except sqlite3.OperationalError as exc:
            if 'duplicate column' not in str(exc).lower():
                raise
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_lead_deliveries_lead ON lawyer_lead_deliveries(lead_id)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_lead_deliveries_status ON lawyer_lead_deliveries(status, created_at)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_lead_events_created ON lawyer_consumer_lead_events(created_at)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_lead_events_order ON lawyer_consumer_lead_events(order_id, created_at)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_ad_orders_status ON lawyer_ad_orders(status)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_ad_orders_package ON lawyer_ad_orders(package_id)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_ad_listings_active ON lawyer_ad_listings(is_active)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_leads_county ON lawyer_consumer_leads(county, created_at)'
    )
    conn.commit()


def ensure_lawyer_arrest_alert_schema(conn: sqlite3.Connection) -> None:
    """2026-07-29: Real-time arrest alerts + case claim for lawyer_ad_orders subscribers.

    Reuses the existing paid lawyer_ad_orders advertiser roster (county +
    practice_areas already captured there) rather than a parallel billing
    table. One delivery row per (order, post): tracks the alert email, the
    optional claim, and the outcome-notified badge state once court_cases
    picks up a disposition for the linked defendant.
    """
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lawyer_arrest_alert_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES lawyer_ad_orders(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            county TEXT,
            charge_category TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            sent_at TEXT,
            claim_token TEXT UNIQUE,
            claimed_at TEXT,
            court_case_id INTEGER REFERENCES court_cases(id) ON DELETE SET NULL,
            outcome_notified_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(order_id, post_id)
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_arrest_alerts_post ON lawyer_arrest_alert_deliveries(post_id)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_arrest_alerts_order ON lawyer_arrest_alert_deliveries(order_id, status)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_lawyer_arrest_alerts_claimed ON lawyer_arrest_alert_deliveries(claimed_at, court_case_id)'
    )
    try:
        conn.execute('ALTER TABLE posts ADD COLUMN lawyer_alert_dispatched_at TEXT')
        print('✅ Added posts.lawyer_alert_dispatched_at')
    except sqlite3.OperationalError:
        pass
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_posts_lawyer_alert_dispatched ON posts(lawyer_alert_dispatched_at)'
    )
    conn.commit()


def ensure_attorney_ad_schema(conn: sqlite3.Connection) -> None:
    """2026-06-06: Sponsored-listing tier on attorney_referrals.

    Free Bronze listings are the default (sponsored=0). Silver ($99/mo) and
    Gold ($199/mo) tiers rank above free listings in the same county and get
    a 'Featured' or 'Priority Placement' badge. Tier flip is done by the admin
    after manual invoicing (Stripe wire-up can replace this later).

    `attorney_sponsored_claims` captures inbound self-service form
    submissions so the admin can review and create the underlying
    attorney_referrals row + flip the sponsor tier.
    """
    _cur = conn.cursor()
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS attorney_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county TEXT NOT NULL,
            name TEXT NOT NULL,
            firm TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            practice_areas TEXT,
            blurb TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ar_county ON attorney_referrals(county, is_active, sort_order)')
    for col, definition in [
        ('sponsored', 'INTEGER NOT NULL DEFAULT 0'),
        ('sponsor_tier', "TEXT"),  # 'silver' | 'gold' | NULL for free/bronze
        ('sponsor_started_at', 'TEXT'),
        ('sponsor_expires_at', 'TEXT'),
        ('sponsor_payment_method', "TEXT DEFAULT 'invoice'"),  # 'invoice' | 'stripe' | 'comp'
        ('logo_path', 'TEXT'),
        ('photo_path', 'TEXT'),
        ('tagline', 'TEXT'),  # short callout shown above name on Gold
        # 2026-08-02: JSON column listing county names this entry serves.
        # Use ["*"] for statewide resources; specific county names otherwise.
        # Backfilled from `county` on first read by the route if NULL.
        ('counties', "TEXT DEFAULT '[]'"),
    ]:
        try:
            _cur.execute(f'ALTER TABLE attorney_referrals ADD COLUMN {col} {definition}')
        except sqlite3.OperationalError as exc:
            if 'duplicate column' not in str(exc).lower():
                raise

    conn.execute('''
        CREATE TABLE IF NOT EXISTS attorney_sponsored_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_name TEXT NOT NULL,
            contact_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            contact_phone TEXT,
            counties_served TEXT NOT NULL,
            tier_requested TEXT NOT NULL,
            website TEXT,
            practice_areas TEXT,
            blurb TEXT,
            mt_bar_number TEXT,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
            admin_notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            reviewed_at TEXT
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_asc_status ON attorney_sponsored_claims(status, created_at DESC)'
    )
    conn.commit()


def ensure_attorney_checkout_schema(conn):
    """Self-serve Stripe checkout for attorney sponsorship — orders + listings.

    attorney_checkout_orders tracks each Stripe checkout session / subscription.
    attorney_checkout_listings holds the surfaced listing rows derived from
    completed orders (one per active order, created in the webhook handler).
    Both tables are created here so the app and tests get them from the same
    source of truth rather than re-creating them in setUp.
    """
    conn.execute('''
        CREATE TABLE IF NOT EXISTS attorney_checkout_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_session_id TEXT UNIQUE,
            firm_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            counties_served TEXT,
            practice_areas TEXT,
            blurb TEXT,
            mt_bar_number TEXT,
            package_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL DEFAULT 'monthly',
            status TEXT NOT NULL DEFAULT 'pending',
            token TEXT NOT NULL,
            activated_at TEXT,
            amount_cents INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_aco_session ON attorney_checkout_orders(stripe_session_id)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_aco_status ON attorney_checkout_orders(status, created_at DESC)'
    )

    conn.execute('''
        CREATE TABLE IF NOT EXISTS attorney_checkout_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            token TEXT NOT NULL,
            firm_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            logo_path TEXT,
            photo_path TEXT,
            blurb TEXT,
            tagline TEXT,
            is_featured INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            montana_bar_verified INTEGER NOT NULL DEFAULT 0,
            montana_bar_member_at TEXT,
            is_disqualified INTEGER NOT NULL DEFAULT 0,
            disqualify_reason TEXT,
            placement_county TEXT,
            placement_tier TEXT,
            listing_position INTEGER,
            ttl_at TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            stripe_session_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_acl_order ON attorney_checkout_listings(order_id)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_acl_status ON attorney_checkout_listings(status, is_featured DESC, listing_position)'
    )
    conn.commit()


def ensure_treatment_center_schema(conn: sqlite3.Connection) -> None:
    """Free public treatment-center directory — mirrors attorney_referrals shape.

    Sister table to attorney_referrals; same opt-in ethos, same editorial
    structure. The recovery_ad_orders/_listings pair (paid product) is
    intentionally separate; this is the free, hand-curated companion page at
    /treatment-centers. Both directories are content-only — no checkout, no
    Stripe, no lead capture.

    Columns follow the attorney_referrals shape so the public template can
    render both directories with the same card component without a separate
    template branch. county is a 2-letter or full-county text label (matches
    existing patterns in attorney_referrals and emailed_agencies).
    """
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS treatment_centers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county TEXT NOT NULL,
            name TEXT NOT NULL,
            organization TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            services TEXT,
            intake_url TEXT,
            blurb TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_tc_county
        ON treatment_centers(county, is_active, sort_order)
        '''
    )
    conn.commit()


def ensure_for_the_record_drafts_schema(conn: sqlite3.Connection) -> None:
    """Weekly "For the Record" narrative drafts queue.

    Mirrors the Havre Daily News "For the Record" column: a per-agency
    weekly narrative that glues together jail bookings + incident records
    into a single publishable article.

    Workers queue rows here; /admin/for-the-record lets the operator
    review, edit, publish, or skip each one. The cron never publishes
    unattended — the Publish button is a manual action in the admin panel.

    Publishing a draft moves it to status='published', stamps published_at,
    and writes a row to for_the_record_published so the public view can
    serve the same article at /for-the-record/<state>-<date>. The
    campaign_dedupe_key prevents one worker run from creating duplicate
    rows for the same agency + week.

    body_md is the raw Markdown body (what the operator sees in the editor);
    body_html is the rendered HTML written at publish time so we don't
    re-render on every page load. source_count + booking_count are
    denormalized so the admin listing can show "X bookings, Y incidents"
    without joining jail_bookings/records every row.
    """
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS for_the_record_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_name TEXT NOT NULL,
            agency_name TEXT NOT NULL,
            county_slug TEXT NOT NULL,
            county_name TEXT NOT NULL,
            title TEXT NOT NULL,
            week_start TEXT NOT NULL,
            week_end TEXT NOT NULL,
            body_md TEXT NOT NULL,
            body_html TEXT NOT NULL DEFAULT '',
            booking_count INTEGER NOT NULL DEFAULT 0,
            source_count INTEGER NOT NULL DEFAULT 0,
            sources_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            campaign_dedupe_key TEXT UNIQUE,
            review_notes TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT,
            published_at TEXT,
            skipped_at TEXT,
            public_slug TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_ftrd_status
        ON for_the_record_drafts(status, created_at DESC)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_ftrd_county_week
        ON for_the_record_drafts(county_slug, week_start DESC)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_ftrd_published
        ON for_the_record_drafts(status, public_slug)
        '''
    )
    conn.commit()


def ensure_outreach_drafts_schema(conn: sqlite3.Connection) -> None:
    """Weekly outreach drafts queue for the source-discovery cron.

    Workers queue rows here; /admin/outreach lets the operator review, edit,
    send, or discard each one. The cron never sends unattended — the Send
    button is a manual action in the admin panel. Sending a draft moves it
    to status='sent', stamps sent_at, and writes the same agency_name /
    email_address pair into emailed_agencies so the gap-analysis SQL on the
    next run does not re-queue it.

    subject + body are kept editable in the admin so the operator can tweak
    wording before sending. worker_name records which cron generated the
    draft (currently 'weekly_county_outreach'); campaign_dedupe_key prevents
    one worker run from creating duplicate rows for the same county.
    """
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS outreach_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_name TEXT NOT NULL,
            agency_name TEXT NOT NULL,
            email_address TEXT NOT NULL,
            recipient_role TEXT,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            campaign_dedupe_key TEXT UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT,
            sent_at TEXT,
            skipped_at TEXT,
            notes TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_od_status
        ON outreach_drafts(status, created_at DESC)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_od_worker
        ON outreach_drafts(worker_name, status)
        '''
    )
    conn.commit()


def ensure_lawyer_outreach_schema(conn: sqlite3.Connection) -> None:
    """Per-firm lawyer advertising outreach workflow.

    Mirrors the existing outreach_drafts pattern (operator review queue, never
    auto-send) but adds a per-firm stage tracker on top:

      lawyer_outreach_prospects  one row per target_list.csv firm, tracks the
                                  Day 1 / Day 3 / Day 5 / Day 10 cadence and
                                  terminal won/lost state.
      lawyer_outreach_emails      queued message bodies per prospect + stage.
                                  Status moves pending -> sent / skipped via
                                  the admin blueprint, never via cron.

    The dedupe key on emails is (prospect_id, stage, attempt) — re-running the
    worker for the same week won't double-queue the Day 1 email for the same
    prospect.
    """
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS lawyer_outreach_prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firm_name TEXT NOT NULL,
            county TEXT NOT NULL,
            city TEXT,
            website TEXT,
            contact_name TEXT,
            contact_email TEXT,
            practice_areas TEXT,
            notes TEXT,
            stage TEXT NOT NULL DEFAULT 'day_1',
            last_action_at TEXT,
            next_action_at TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            source TEXT NOT NULL DEFAULT 'target_list.csv',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(firm_name, county)
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_lop_stage
        ON lawyer_outreach_prospects(stage, status)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_lop_next_action
        ON lawyer_outreach_prospects(next_action_at)
        '''
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS lawyer_outreach_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER NOT NULL REFERENCES lawyer_outreach_prospects(id),
            stage TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            to_addr TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            campaign_dedupe_key TEXT UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT,
            sent_at TEXT,
            skipped_at TEXT,
            error TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_loe_status
        ON lawyer_outreach_emails(status, created_at DESC)
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_loe_prospect
        ON lawyer_outreach_emails(prospect_id, stage)
        '''
    )
    conn.commit()


def ensure_advertise_sales_lead_schema(conn):
    """Sales-call leads from /advertise/* landing pages.

    Single table for both bail and lawyer products. The `product` column
    discriminates ('bail' or 'lawyer'). Keep this separate from the
    inventory tables (bail_ad_inquiries, lawyer_ad_orders) so a "schedule
    a call" submission doesn't accidentally look like a paid order.
    """
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS advertise_sales_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            firm_or_agency TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            county TEXT NOT NULL DEFAULT '',
            package_interest TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            ip_hash TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_asl_product_created ON advertise_sales_leads(product, created_at DESC)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_asl_status ON advertise_sales_leads(status, created_at DESC)'
    )
    conn.commit()


def migrate():
    """Safely apply schema changes to an existing DB without data loss"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _configure_sqlite(conn)
    cursor = conn.cursor()
    _create_core_tables(cursor)
    ensure_source_material_schema(conn)
    ensure_public_meeting_schema(conn)

    # Add lat/lon to meeting_locations for map display
    for col, definition in [('lat', 'REAL'), ('lon', 'REAL')]:
        try:
            cursor.execute(f'ALTER TABLE meeting_locations ADD COLUMN {col} {definition}')
            print(f'✅ Added meeting_locations.{col}')
        except sqlite3.OperationalError:
            pass

    # Add LLM cost tracking to scheduled_job_runs (created in job_runner.py).
    # Per-row model + token columns let ops see which cron workers eat the most
    # budget. Population requires instrumentation via services/llm_instrument.py
    # call sites — see that module's docstring for the drop-in pattern.
    for col, definition in [
        ('model', 'TEXT'),
        ('input_tokens', 'INTEGER'),
        ('output_tokens', 'INTEGER'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE scheduled_job_runs ADD COLUMN {col} {definition}')
            print(f'✅ Added scheduled_job_runs.{col}')
        except sqlite3.OperationalError:
            pass

    _MT_CITY_COORDS = {
        'billings':                      (45.7833, -108.5007),
        'great-falls':                   (47.5053, -111.3008),
        'missoula-county':               (46.8721, -113.9940),
        'belgrade':                      (45.7763, -111.1771),
        'whitefish':                     (48.4118, -114.3352),
        'kalispell':                     (48.1961, -114.3117),
        'columbia-falls':                (48.3719, -114.1835),
        'anaconda-deer-lodge-county':    (46.1285, -112.9471),
        'miles-city':                    (46.4083, -105.8408),
        'helena':                        (46.5958, -112.0270),
        'livingston':                    (45.6625, -110.5607),
        'havre':                         (48.5484, -109.6821),
        'big-sandy':                     (48.1840, -110.1179),
        'fort-benton':                   (47.8230, -110.6666),
        'choteau':                       (47.8127, -112.1802),
    }
    for slug, (lat, lon) in _MT_CITY_COORDS.items():
        cursor.execute(
            'UPDATE meeting_locations SET lat=?, lon=? WHERE slug=? AND (lat IS NULL OR lon IS NULL)',
            (lat, lon, slug),
        )
    conn.commit()

    ensure_public_engagement_schema(conn)
    ensure_jail_booking_schema(conn)
    ensure_warrant_schema(conn)
    ensure_bondsman_command_center_schema(conn)
    ensure_court_tracker_schema(conn)
    ensure_recovery_ad_schema(conn)
    ensure_lawyer_ad_schema(conn)
    ensure_attorney_ad_schema(conn)
    ensure_attorney_checkout_schema(conn)
    ensure_treatment_center_schema(conn)
    ensure_outreach_drafts_schema(conn)
    ensure_lawyer_outreach_schema(conn)
    ensure_for_the_record_drafts_schema(conn)
    ensure_agent_mission_control_schema(conn)
    ensure_api_auth_schema(conn)
    ensure_code_violation_schema(conn)
    ensure_license_sanction_schema(conn)
    ensure_civil_filing_schema(conn)
    ensure_crash_incident_schema(conn)
    ensure_agency_contacts_schema(conn)
    ensure_civic_records_requests_schema(conn)

    ensure_sponsored_listing_schema(conn)

    # County inventory denormalized table
    ensure_county_inventory_schema(conn)

    # LEA Panel: multi-tenant agency self-service tables
    ensure_lea_schema(conn)
    ensure_lea_security_policies(conn)

    # Add source_type column to blotters if it doesn't exist
    try:
        cursor.execute("ALTER TABLE blotters ADD COLUMN source_type TEXT DEFAULT 'pdf'")
        print("✅ Added source_type column to blotters")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # humor_score powers the public /funniest feed. NULL until scored.
    try:
        cursor.execute("ALTER TABLE records ADD COLUMN humor_score REAL")
        print("✅ Added records.humor_score")
    except sqlite3.OperationalError:
        pass  # Column already exists
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_records_humor_score ON records(humor_score)"
    )

    for col, definition in [
        ('email', 'TEXT'),
        ('created_at', 'TEXT'),
        ('role', "TEXT NOT NULL DEFAULT 'super_admin'"),
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_login_at', 'TEXT'),
        ('mfa_secret', 'TEXT'),
        ('mfa_enabled', 'INTEGER NOT NULL DEFAULT 0'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE users ADD COLUMN {col} {definition}')
            print(f'✅ Added users.{col}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active)')
    try:
        cursor.execute(
            "UPDATE users SET created_at = datetime('now') WHERE created_at IS NULL OR trim(created_at) = ''"
        )
    except sqlite3.OperationalError:
        pass

    for col, definition in [
        ('target_type', 'TEXT'),
        ('target_id', 'TEXT'),
        ('ip_address', 'TEXT'),
        ('metadata_json', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE audit_logs ADD COLUMN {col} {definition}')
            print(f'✅ Added audit_logs.{col}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)')

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
        old_columns = cursor.execute("SELECT name, type FROM pragma_table_info('posts')").fetchall()
        cursor.execute('ALTER TABLE posts RENAME TO posts_legacy_record_id_not_null')
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
        new_column_names = {
            row[1] for row in cursor.execute("SELECT * FROM pragma_table_info('posts')").fetchall()
        }
        for name, col_type in old_columns:
            if name in new_column_names:
                continue
            safe_name = '"' + name.replace('"', '""') + '"'
            safe_type = col_type or 'TEXT'
            cursor.execute(f'ALTER TABLE posts ADD COLUMN {safe_name} {safe_type}')
            new_column_names.add(name)
        old_column_names = [row[0] for row in old_columns]
        common_columns = [name for name in old_column_names if name in new_column_names]
        if common_columns:
            quoted_columns = ', '.join('"' + name.replace('"', '""') + '"' for name in common_columns)
            cursor.execute(
                f'''
                INSERT INTO posts ({quoted_columns})
                SELECT {quoted_columns}
                FROM posts_legacy_record_id_not_null
                '''
            )
        cursor.execute('DROP TABLE posts_legacy_record_id_not_null')

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

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS story_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_type TEXT NOT NULL DEFAULT 'news_story',
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            secondary_source_url TEXT,
            headline_hint TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            location_label TEXT,
            occurred_at TEXT,
            agency_name TEXT,
            source_record_ids_json TEXT,
            dedupe_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'new',
            score REAL NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_story_candidates_status ON story_candidates(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_story_candidates_score ON story_candidates(score)')

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS blog_draft_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blog_post_id INTEGER NOT NULL,
            story_candidate_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT,
            evidence_json TEXT,
            reviewed_at TEXT DEFAULT (datetime('now')),
            reviewer_agent TEXT NOT NULL,
            FOREIGN KEY (blog_post_id) REFERENCES blog_posts(id) ON DELETE CASCADE,
            FOREIGN KEY (story_candidate_id) REFERENCES story_candidates(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blog_draft_reviews_post ON blog_draft_reviews(blog_post_id)')

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS blog_post_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blog_post_id INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_title TEXT,
            source_published_at TEXT,
            notes TEXT,
            FOREIGN KEY (blog_post_id) REFERENCES blog_posts(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blog_post_sources_post ON blog_post_sources(blog_post_id)')

    # Search Console CSV imports for workflow/SEO tuning
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_console_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_filename TEXT NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'unknown',
            row_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_console_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_id INTEGER NOT NULL,
            query TEXT,
            page TEXT,
            clicks REAL DEFAULT 0,
            impressions REAL DEFAULT 0,
            ctr REAL DEFAULT 0,
            position REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (import_id) REFERENCES search_console_imports(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_console_imports_created ON search_console_imports(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_console_imports_kind ON search_console_imports(source_kind)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_console_rows_import ON search_console_rows(import_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_console_rows_page ON search_console_rows(page)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_console_rows_query ON search_console_rows(query)')

    # Subscribers table for public email digest
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            counties TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            missing_person_email_opt_in INTEGER NOT NULL DEFAULT 1,
            missing_person_sms_opt_in INTEGER NOT NULL DEFAULT 0,
            missing_person_push_opt_in INTEGER NOT NULL DEFAULT 0,
            phone_verified_at TEXT DEFAULT ''
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_subscribers_active ON subscribers(active)')
    ensure_bail_bonds_alert_schema(conn)
    ensure_incident_notification_schema(conn)
    ensure_missing_person_schema(conn)

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
        CREATE TABLE IF NOT EXISTS ingestion_source_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            alert_kind TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            summary TEXT,
            first_detected_at TEXT DEFAULT (datetime('now')),
            last_detected_at TEXT DEFAULT (datetime('now')),
            last_sent_at TEXT,
            resolved_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_ingestion_source_alerts_open '
        'ON ingestion_source_alerts(source_type, alert_kind, state)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_ingestion_source_alerts_updated '
        'ON ingestion_source_alerts(updated_at)'
    )

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
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_page_views_created_path ON page_views(created_at, path)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_page_views_created_referrer ON page_views(created_at, referrer)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_page_views_created_ip ON page_views(created_at, ip_hash)')

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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS digest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            target_date TEXT NOT NULL,
            audience TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            subject TEXT,
            preview_posts INTEGER DEFAULT 0,
            preview_subscribers INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            initiated_by TEXT,
            notes TEXT,
            created_by_user_id INTEGER,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_digest_runs_created ON digest_runs(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_digest_runs_target ON digest_runs(target_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_digest_runs_status ON digest_runs(status)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS digest_run_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            recipient_email TEXT NOT NULL,
            counties TEXT DEFAULT '',
            status TEXT NOT NULL,
            post_count INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (run_id) REFERENCES digest_runs(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_digest_run_recipients_run ON digest_run_recipients(run_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_digest_run_recipients_status ON digest_run_recipients(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_digest_run_recipients_created ON digest_run_recipients(created_at)')

    for col, definition in [
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
        ('source', 'TEXT'),
        ('notes', 'TEXT'),
        ('missing_person_email_opt_in', 'INTEGER NOT NULL DEFAULT 1'),
        ('missing_person_sms_opt_in', 'INTEGER NOT NULL DEFAULT 0'),
        ('missing_person_push_opt_in', 'INTEGER NOT NULL DEFAULT 0'),
        ('phone_verified_at', "TEXT DEFAULT ''"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE subscribers ADD COLUMN {col} {definition}')
            print(f'✅ Added subscribers.{col}')
        except sqlite3.OperationalError:
            pass

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS missing_person_push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscriber_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            p256dh_key TEXT NOT NULL,
            auth_key TEXT NOT NULL,
            user_agent TEXT DEFAULT '',
            device_label TEXT DEFAULT '',
            last_seen_county TEXT DEFAULT '',
            last_seen_city TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )

    delivery_columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info('missing_person_alert_deliveries')").fetchall()
    }
    for col, definition in [
        ('subscriber_id', 'INTEGER'),
        ('channel', "TEXT NOT NULL DEFAULT 'email'"),
        ('recipient', "TEXT DEFAULT ''"),
        ('provider_message_id', "TEXT DEFAULT ''"),
        ('updated_at', 'TEXT'),
    ]:
        if col not in delivery_columns:
            cursor.execute(f'ALTER TABLE missing_person_alert_deliveries ADD COLUMN {col} {definition}')
            print(f'✅ Added missing_person_alert_deliveries.{col}')
            delivery_columns.add(col)

    if 'recipient' in delivery_columns and 'recipient_email' in delivery_columns:
        cursor.execute(
            '''
            UPDATE missing_person_alert_deliveries
            SET recipient = recipient_email
            WHERE TRIM(COALESCE(recipient, '')) = ''
              AND TRIM(COALESCE(recipient_email, '')) != ''
            '''
        )
    if 'updated_at' in delivery_columns:
        cursor.execute(
            '''
            UPDATE missing_person_alert_deliveries
            SET updated_at = COALESCE(
                NULLIF(updated_at, ''),
                NULLIF(created_at, ''),
                datetime('now')
            )
            WHERE COALESCE(updated_at, '') = ''
            '''
        )
    cursor.execute('DROP INDEX IF EXISTS idx_missing_person_alert_delivery_unique')
    cursor.execute(
        '''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_missing_person_alert_delivery_unique
        ON missing_person_alert_deliveries(
            missing_person_id,
            notification_version,
            channel,
            COALESCE(
                NULLIF(COALESCE(recipient, ''), ''),
                CASE
                    WHEN channel = 'email' THEN NULLIF(COALESCE(recipient_email, ''), '')
                    ELSE NULL
                END,
                CASE
                    WHEN subscriber_id IS NOT NULL THEN '__subscriber__' || CAST(subscriber_id AS TEXT)
                    ELSE NULL
                END,
                '__row__' || CAST(id AS TEXT)
            )
        )
        '''
    )

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
        ('seo_title',        'TEXT'),
        ('seo_slug',         'TEXT'),
        ('audited_at',       'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE posts ADD COLUMN {col} {definition}')
            print(f'✅ Added posts.{col}')
        except sqlite3.OperationalError:
            pass  # Already exists
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_audit_status ON posts(audit_status)')

    ensure_lawyer_arrest_alert_schema(conn)

    ensure_case_journey_schema(conn)
    created_journeys = seed_case_journeys(conn)
    if created_journeys:
        print(f'✅ Seeded {created_journeys} case journeys')

    try:
        from core.agency_normalization import normalize_existing_post_agencies

        normalized_posts = normalize_existing_post_agencies(conn)
        if normalized_posts:
            print(f'✅ Normalized agency metadata for {normalized_posts} posts')
    except Exception as exc:
        print(f'⚠️ Skipped post agency normalization: {exc}')

    # Add charge_category column to records for analytics/heatmaps
    try:
        cursor.execute("ALTER TABLE records ADD COLUMN charge_category TEXT")
        print("✅ Added records.charge_category")
    except sqlite3.OperationalError:
        pass  # Already exists
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_records_charge_category ON records(charge_category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_records_county_date ON records(county, date)")

    # Alert subscriptions — county-targeted immediate alerts (separate from morning digest)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alert_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            county TEXT NOT NULL,
            alert_types TEXT NOT NULL DEFAULT '["all"]',
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            verified INTEGER NOT NULL DEFAULT 0,
            last_alerted_at TEXT,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alert_subs_email ON alert_subscriptions(email)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alert_subs_county ON alert_subscriptions(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alert_subs_active ON alert_subscriptions(active)')

    # Name watches — alert when a person's name appears in a new blotter record
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS name_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            watch_name TEXT NOT NULL,
            county TEXT,
            token TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            last_alerted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_name_watches_email ON name_watches(email)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_name_watches_active ON name_watches(active)')

    # Charge explainer pages — evergreen SEO content per incident type
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS charge_explainers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_type TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            statute_ref TEXT,
            charge_category TEXT,
            published INTEGER NOT NULL DEFAULT 1,
            generated_by TEXT DEFAULT 'claude',
            view_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_explainers_slug ON charge_explainers(slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_explainers_category ON charge_explainers(charge_category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_explainers_published ON charge_explainers(published)')

    # Premium user alert profiles (location + incident type + severity + frequency)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_alert_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT 'Default Alert',
            alert_types TEXT NOT NULL DEFAULT '["all"]',
            counties TEXT DEFAULT NULL,
            cities TEXT DEFAULT NULL,
            neighborhoods TEXT DEFAULT NULL,
            radius_miles INTEGER DEFAULT NULL,
            center_lat REAL DEFAULT NULL,
            center_lng REAL DEFAULT NULL,
            severity_threshold TEXT DEFAULT 'all',
            frequency TEXT NOT NULL DEFAULT 'immediate',
            delivery_channel TEXT NOT NULL DEFAULT 'email',
            webhook_url TEXT DEFAULT NULL,
            slack_channel TEXT DEFAULT NULL,
            teams_webhook TEXT DEFAULT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES public_users(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alert_profiles_user ON user_alert_profiles(user_id, is_active)')

    # Notification queue for deliverability tracking / bounce management
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS notification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            user_id INTEGER NOT NULL,
            record_id INTEGER,
            channel TEXT NOT NULL DEFAULT 'email',
            recipient TEXT NOT NULL,
            subject TEXT,
            body_html TEXT,
            body_text TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            sent_at TEXT,
            delivered_at TEXT,
            opened_at TEXT,
            bounce_reason TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (profile_id) REFERENCES user_alert_profiles(id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES public_users(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_notif_queue_status ON notification_queue(status, retry_count)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_notif_queue_user ON notification_queue(user_id, created_at)')

    # Geocoded incident locations for mapping
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incident_geocodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER UNIQUE NOT NULL,
            raw_location TEXT NOT NULL,
            lat REAL,
            lng REAL,
            geocode_confidence TEXT,
            county TEXT,
            city TEXT,
            neighborhood TEXT,
            geocoded_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (record_id) REFERENCES records(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_geocodes_loc ON incident_geocodes(lat, lng)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_geocodes_county ON incident_geocodes(county, city)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_geocodes_record ON incident_geocodes(record_id)')

    # Neighborhood safety scorecards cache
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS safety_scorecards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            area_type TEXT NOT NULL,
            area_slug TEXT NOT NULL,
            area_name TEXT NOT NULL,
            county TEXT,
            population INTEGER,
            score REAL,
            percentile_state REAL,
            percentile_national REAL,
            methodology_version TEXT NOT NULL DEFAULT 'v1',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            trends_json TEXT DEFAULT '{}',
            factors_json TEXT DEFAULT '{}',
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            computed_at TEXT DEFAULT (datetime('now')),
            UNIQUE(area_type, area_slug, period_start, period_end, methodology_version)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_scorecards_area ON safety_scorecards(area_type, area_slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_scorecards_period ON safety_scorecards(period_end, computed_at)')

    # 2026-05-11: code enforcement violations
    ensure_code_violation_schema(conn)
    ensure_license_sanction_schema(conn)
    ensure_sex_offender_schema(conn)

    # Admin AI pending actions — survives session expiry
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_ai_pending_actions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER,
            tool_name  TEXT NOT NULL,
            summary    TEXT,
            arguments_json TEXT,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_ai_pending_expires ON admin_ai_pending_actions(expires_at)'
    )

    # facebook_post_queue extended columns (content_type, blog support, link)
    for col, definition in [
        ('content_type', "TEXT NOT NULL DEFAULT 'blotter'"),
        ('blog_post_id', 'INTEGER'),
        ('link_url', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE facebook_post_queue ADD COLUMN {col} {definition}')
            print(f'✅ Added facebook_post_queue.{col}')
        except sqlite3.OperationalError:
            pass

    # Unsplash image cache keyed by city/county slug
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS unsplash_image_cache (
            slug TEXT PRIMARY KEY,
            image_url TEXT NOT NULL,
            photographer TEXT,
            photographer_url TEXT,
            fetched_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    # Criminal outcome fields on court_cases
    for col, definition in [
        ('defendant_name', 'TEXT'),
        ('is_criminal', 'INTEGER NOT NULL DEFAULT 0'),
        ('charges_text', 'TEXT'),
        ('charges_json', 'TEXT'),
        ('plea', 'TEXT'),
        ('disposition', 'TEXT'),
        ('sentence_text', 'TEXT'),
        ('sentence_date', 'TEXT'),
        ('sentencing_judge', 'TEXT'),
        ('outcome_scraped_at', 'TEXT'),
        ('original_court', 'TEXT'),
        ('original_case_number', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE court_cases ADD COLUMN {col} {definition}')
            print(f'✅ Added court_cases.{col}')
        except sqlite3.OperationalError:
            pass
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_court_cases_criminal '
        'ON court_cases(is_criminal, outcome_scraped_at, status)'
    )

    # Disposition API lookup keys (added 2026-06-02)
    # Pre-compute last name, first name, and slug for fast person → court_case joins.
    # court_cases.defendant_name is in "First [Middle] Last" format; we split on first space.
    for col, definition in [
        ('defendant_slug', 'TEXT'),
        ('defendant_last', 'TEXT'),
        ('defendant_first', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE court_cases ADD COLUMN {col} {definition}')
            print(f'✅ Added court_cases.{col}')
        except sqlite3.OperationalError:
            pass

    # Backfill: idempotent UPDATE gated on NULL so re-runs are safe.
    # Only rows with a non-empty defendant_name are touched.
    # SQL: extract first word as first name. (Last name is fixed in Python below
    # because the reverse-trick SQL for "last word" is unreadable and the cost of
    # 276 Python iterations is negligible.)
    cursor.execute('''
        UPDATE court_cases
        SET defendant_first = LOWER(
                CASE WHEN instr(TRIM(defendant_name), ' ') > 0
                     THEN substr(TRIM(defendant_name), 1, instr(TRIM(defendant_name), ' ') - 1)
                     ELSE ''
                END
            )
        WHERE defendant_name IS NOT NULL
          AND TRIM(defendant_name) != ''
          AND defendant_first IS NULL
    ''')
    # Python pass: extract the actual last word (true surname) so it matches
    # the normalize_name() semantic used by the lookup service. Runs every
    # migrate() call — 276 rows is negligible and the earlier SQL pass can
    # leave a stale "everything after first space" value on rows that pre-date
    # the Python correction.
    cursor.execute('''
        SELECT id, defendant_name FROM court_cases
        WHERE defendant_name IS NOT NULL AND TRIM(defendant_name) != ''
    ''')
    _backfill_rows = cursor.fetchall()
    for _bid, _bname in _backfill_rows:
        _parts = _bname.strip().split()
        if not _parts:
            continue
        _bfirst = _parts[0].lower()
        _blast = _parts[-1].lower()
        cursor.execute(
            'UPDATE court_cases SET defendant_first = ?, defendant_last = ? WHERE id = ?',
            (_bfirst, _blast, _bid),
        )
    cursor.execute('''
        UPDATE court_cases
        SET defendant_slug = LOWER(
                REPLACE(REPLACE(REPLACE(REPLACE(TRIM(defendant_name), ' ', '-'), '.', ''), "'", ''), ',', '')
            )
        WHERE defendant_name IS NOT NULL
          AND TRIM(defendant_name) != ''
          AND defendant_slug IS NULL
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_court_cases_defendant_slug '
        'ON court_cases(defendant_slug)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_court_cases_defendant_last_first '
        'ON court_cases(defendant_last, defendant_first)'
    )
    print('✅ court_cases defendant_slug/last/first backfilled + indexed')

    # Disposition watcher: links jail_bookings to court_cases (added 2026-06-02)
    # Stores the relationship + how we found it + outcome snapshot for change detection.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS booking_case_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL,
            court_case_id INTEGER NOT NULL,
            match_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            linked_at TEXT DEFAULT (datetime('now')),
            last_checked_at TEXT,
            last_outcome_snapshot TEXT,
            has_outcome INTEGER NOT NULL DEFAULT 0,
            notified_admin_at TEXT,
            UNIQUE (booking_id, court_case_id),
            FOREIGN KEY (booking_id) REFERENCES jail_bookings(id) ON DELETE CASCADE,
            FOREIGN KEY (court_case_id) REFERENCES court_cases(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bcl_booking ON booking_case_links(booking_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bcl_case ON booking_case_links(court_case_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bcl_linked_at ON booking_case_links(linked_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bcl_outcome_pending ON booking_case_links(has_outcome, last_checked_at)')
    print('✅ booking_case_links schema ensured')

    # 2026-06-02: Disposition API token-delivery log. When the Stripe webhook
    # provisions a new disposition_api subscription, it writes the plaintext
    # token here so a follow-up email can be sent (and the row stays for audit
    # even if the email fails). We never store plaintext in api_data_tokens —
    # that table only holds SHA-256 hashes.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_token_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id INTEGER NOT NULL,
            plaintext_token TEXT NOT NULL,
            public_user_id INTEGER,
            email TEXT,
            email_sent_at TEXT,
            email_error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (token_id) REFERENCES api_data_tokens(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atd_token ON api_token_deliveries(token_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atd_unsent ON api_token_deliveries(email_sent_at)')
    print('✅ api_token_deliveries schema ensured')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            county_filter TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_sent_at TEXT
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_push_subs_active ON push_subscriptions(active, county_filter)'
    )

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mobile_push_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER,
            expo_push_token TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            device_id TEXT NOT NULL DEFAULT '',
            county_filter TEXT DEFAULT '',
            alert_types TEXT DEFAULT 'all',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            last_sent_at TEXT,
            UNIQUE(public_user_id, expo_push_token)
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_mobile_push_tokens_active ON mobile_push_tokens(is_active, public_user_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_mobile_push_tokens_token ON mobile_push_tokens(expo_push_token)'
    )

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS social_posts_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blog_post_id INTEGER,
            platform TEXT NOT NULL,
            status TEXT NOT NULL,
            fb_post_id TEXT,
            ig_media_id TEXT,
            image_path TEXT,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_social_posts_log_platform '
        'ON social_posts_log(platform, created_at)'
    )

    # 2026-06-02: Public corrections log — tracks every published correction to
    # a post for transparency. Each row is one correction event.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS post_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            reason TEXT NOT NULL,
            corrected_by TEXT,
            is_public INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_post_corrections_post ON post_corrections(post_id, created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_post_corrections_created ON post_corrections(created_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_post_corrections_public ON post_corrections(is_public, created_at)')

    # 2026-06-02: Warrant cleared notification requests — public opt-in by
    # name+DOB (or warrant id) so an email can be sent when a warrant is
    # resolved. No public listing; counts are admin-visible.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS warrant_clear_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            warrant_id INTEGER,
            person_name TEXT NOT NULL,
            dob TEXT,
            county TEXT,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            notified_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            ip_address TEXT,
            notes TEXT,
            FOREIGN KEY (warrant_id) REFERENCES warrants(id) ON DELETE SET NULL
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wcr_status ON warrant_clear_requests(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wcr_warrant ON warrant_clear_requests(warrant_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_wcr_person ON warrant_clear_requests(person_name, dob)')

    # 2026-08-29: Paid name-removal / privacy-suppression requests.
    # One-time $999 payment covers a verified privacy review. On approval, the
    # person's name is REDACTED (not deleted) across public records. Requires
    # human review before suppression is applied (see /admin/name-removals).
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS name_suppression_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER,
            email TEXT NOT NULL,
            person_name TEXT NOT NULL,
            dob TEXT,
            county TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            stripe_session_id TEXT,
            stripe_payment_id TEXT,
            reviewed_by INTEGER,
            reviewed_at TEXT,
            applied_at TEXT,
            rejection_reason TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            ip_address TEXT,
            notes TEXT
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nsr_status ON name_suppression_requests(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nsr_email ON name_suppression_requests(email)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nsr_session ON name_suppression_requests(stripe_session_id)')

    # Resolved suppressions applied to public records. Render-time redaction
    # checks this table so suppressed names display as "Name withheld".
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS suppressed_names (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_name_normalized TEXT NOT NULL,
            county TEXT,
            request_id INTEGER,
            applied_by INTEGER,
            applied_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (request_id) REFERENCES name_suppression_requests(id) ON DELETE SET NULL
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_suppressed_name ON suppressed_names(person_name_normalized, county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_suppressed_request ON suppressed_names(request_id)')

    # 2026-06-02: B2B data API tokens. Each token grants access to the
    # /api/v1/data/* endpoints at a given tier with a per-minute rate cap.
    # Tokens are stored as SHA-256 hashes; the plaintext is only shown once
    # at creation time.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS api_data_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            label TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'standard',
            rate_limit_per_minute INTEGER NOT NULL DEFAULT 60,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_used_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_adt_token_hash ON api_data_tokens(token_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_adt_user ON api_data_tokens(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_adt_active ON api_data_tokens(is_active, tier)')

    # Per-token rate limit counter (in-memory cache would also work; SQLite
    # keeps it durable across gunicorn worker restarts).
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS api_data_token_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id INTEGER NOT NULL,
            hit_minute TEXT NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (token_id, hit_minute),
            FOREIGN KEY (token_id) REFERENCES api_data_tokens(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_adth_token_minute ON api_data_token_hits(token_id, hit_minute)')

    # 2026-06-02: Attorney referral directory — public-facing widget on
    # warrant/case pages that lists licensed Montana defense attorneys by
    # county. Each entry is opt-in; we do not auto-pull data from anywhere.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS attorney_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county TEXT NOT NULL,
            name TEXT NOT NULL,
            firm TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            practice_areas TEXT,
            blurb TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ar_county ON attorney_referrals(county, is_active, sort_order)')

    # 2026-06-02: Public tip submissions — anonymous or named tips from
    # site visitors about records, warrants, missing persons, or other
    # public-safety matters. Routed to admin review queue.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS public_tips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'other',
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            submitter_name TEXT,
            submitter_email TEXT,
            submitter_phone TEXT,
            is_anonymous INTEGER NOT NULL DEFAULT 0,
            related_record_id INTEGER,
            related_warrant_id INTEGER,
            related_post_id INTEGER,
            status TEXT NOT NULL DEFAULT 'new',
            assigned_to INTEGER,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            notes TEXT
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pt_status ON public_tips(status, created_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_pt_category ON public_tips(category, created_at DESC)')

    # 2026-06-02: Social share log — tracks every auto-share or manual share
    # to Facebook / Reddit / X / etc. so the editorial team can audit what
    # was posted where, when, and whether the platform accepted it.
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS social_share_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            target_url TEXT,
            post_url TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            response_code INTEGER,
            response_body TEXT,
            triggered_by TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        '''
    )
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ssl_post ON social_share_log(post_id, created_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ssl_platform ON social_share_log(platform, status)')

    # case_status_searches — analytics + per-IP rate limit for /case-status free lookup
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS case_status_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL,
            query_text TEXT NOT NULL,
            county TEXT,
            results_count INTEGER DEFAULT 0,
            user_agent TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_css_ip_time ON case_status_searches(ip_address, created_at DESC)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_css_time ON case_status_searches(created_at DESC)')

    # sponsored_digests — paid sponsorship of a county's digest emails.
    # When a county has an active sponsor, the digest email opens with a
    # "Presented by ..." block linking to sponsor_url.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sponsored_digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county TEXT NOT NULL,
            sponsor_name TEXT NOT NULL,
            sponsor_pitch TEXT,
            sponsor_url TEXT,
            contact_email TEXT,
            monthly_rate_cents INTEGER DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            starts_on TEXT,
            expires_on TEXT,
            notes TEXT,
            created_by_user_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sd_county_active ON sponsored_digests(county, is_active)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_sd_one_active_per_county ON sponsored_digests(county) WHERE is_active = 1')

    conn.commit()
    conn.close()
    print("✅ Migration complete")

    _ensure_subscription_feature_tables()
    print("✅ Subscription feature tables ensured")


def ensure_sponsored_listing_schema(conn: sqlite3.Connection) -> None:
    """Create sponsored_listings table for bail bond agencies and criminal defense
    attorneys that pay for placement on county jail booking pages."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sponsored_listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            business_type TEXT NOT NULL CHECK(business_type IN ('bail_bond', 'attorney')),
            contact_name TEXT,
            email TEXT NOT NULL,
            phone TEXT,
            website TEXT,
            county_slug TEXT NOT NULL,
            ad_text TEXT,
            logo_path TEXT,
            sort_order INTEGER NOT NULL DEFAULT 100,
            is_active INTEGER NOT NULL DEFAULT 1,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_session_id TEXT UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            activated_at TEXT,
            expires_at TEXT
        )
    ''')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_sl_county_active ON sponsored_listings(county_slug, is_active, sort_order)'
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_sl_type ON sponsored_listings(business_type)'
    )

    # Track impressions/clicks per listing
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sponsored_listing_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL REFERENCES sponsored_listings(id),
            impression_date TEXT NOT NULL DEFAULT (date('now')),
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0
        )
    ''')
    conn.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_sls_listing_date ON sponsored_listing_stats(listing_id, impression_date)'
    )
    conn.commit()


def _ensure_subscription_feature_tables():
    """Create tables for saved_searches and watchlists (used by Plus/Pro tiers)."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS saved_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_user_id INTEGER NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                query_json TEXT NOT NULL,
                filters_json TEXT DEFAULT '{}',
                notify_on_match INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (public_user_id) REFERENCES public_users(id)
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_saved_searches_user
            ON saved_searches(public_user_id, active)
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_user_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                watch_type TEXT NOT NULL DEFAULT 'name',
                watch_value TEXT NOT NULL,
                county TEXT DEFAULT '',
                notify INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (public_user_id) REFERENCES public_users(id)
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_watchlists_user
            ON watchlists(public_user_id, active)
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tracked_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_user_id INTEGER NOT NULL,
                case_number TEXT NOT NULL,
                case_type TEXT DEFAULT '',
                county TEXT DEFAULT '',
                label TEXT DEFAULT '',
                notify_on_update INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (public_user_id) REFERENCES public_users(id)
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_tracked_cases_user
            ON tracked_cases(public_user_id, active)
        ''')
        conn.commit()
    finally:
        conn.close()




def ensure_sex_offender_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offenders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            date_of_birth TEXT,
            tier TEXT,
            risk_level TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            address_street TEXT,
            address_city TEXT,
            address_county TEXT,
            address_state TEXT DEFAULT 'MT',
            address_zip TEXT,
            lat REAL,
            lon REAL,
            employer_name TEXT,
            employer_address TEXT,
            school_name TEXT,
            school_address TEXT,
            offense_description TEXT,
            conviction_date TEXT,
            conviction_state TEXT,
            conviction_county TEXT,
            photo_url TEXT,
            source_url TEXT,
            raw_json TEXT,
            offender_type TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offender_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            removed_count INTEGER NOT NULL DEFAULT 0,
            changed_count INTEGER NOT NULL DEFAULT 0,
            scrape_duration_seconds INTEGER,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offender_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offender_id INTEGER NOT NULL,
            snapshot_id INTEGER NOT NULL,
            change_type TEXT NOT NULL,
            change_note TEXT,
            old_value_json TEXT,
            new_value_json TEXT,
            classified_by TEXT DEFAULT 'hermes',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (offender_id) REFERENCES sex_offenders(id) ON DELETE CASCADE,
            FOREIGN KEY (snapshot_id) REFERENCES sex_offender_snapshots(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sex_offender_alert_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            radius_miles REAL NOT NULL DEFAULT 5.0,
            counties TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            last_sent_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_registry_id ON sex_offenders(registry_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_county ON sex_offenders(address_county)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_city ON sex_offenders(address_city)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_geo ON sex_offenders(lat, lon)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offenders_status ON sex_offenders(status)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_changes_offender ON sex_offender_changes(offender_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_changes_snapshot ON sex_offender_changes(snapshot_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_changes_type ON sex_offender_changes(change_type)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_sex_offender_snapshots_date ON sex_offender_snapshots(snapshot_date)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_so_alert_subs_active ON sex_offender_alert_subscriptions(is_active)'
    )

    for col, definition in [
        ('registry_id', 'TEXT UNIQUE NOT NULL'),
        ('tier', 'TEXT'),
        ('risk_level', 'TEXT'),
        ('status', "TEXT NOT NULL DEFAULT 'active'"),
        ('address_street', 'TEXT'),
        ('address_city', 'TEXT'),
        ('address_county', 'TEXT'),
        ('address_state', "TEXT DEFAULT 'MT'"),
        ('address_zip', 'TEXT'),
        ('lat', 'REAL'),
        ('lon', 'REAL'),
        ('employer_name', 'TEXT'),
        ('employer_address', 'TEXT'),
        ('school_name', 'TEXT'),
        ('school_address', 'TEXT'),
        ('offense_description', 'TEXT'),
        ('conviction_date', 'TEXT'),
        ('conviction_state', 'TEXT'),
        ('conviction_county', 'TEXT'),
        ('photo_url', 'TEXT'),
        ('source_url', 'TEXT'),
        ('raw_json', 'TEXT'),
        ('offender_type', 'TEXT'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offenders ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offenders.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('snapshot_date', 'TEXT NOT NULL'),
        ('total_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('new_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('removed_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('changed_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('scrape_duration_seconds', 'INTEGER'),
        ('notes', "TEXT DEFAULT ''"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offender_snapshots ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offender_snapshots.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('offender_id', 'INTEGER NOT NULL'),
        ('snapshot_id', 'INTEGER NOT NULL'),
        ('change_type', 'TEXT NOT NULL'),
        ('change_note', 'TEXT'),
        ('old_value_json', 'TEXT'),
        ('new_value_json', 'TEXT'),
        ('classified_by', "TEXT DEFAULT 'hermes'"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offender_changes ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offender_changes.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('email', 'TEXT NOT NULL'),
        ('lat', 'REAL NOT NULL'),
        ('lon', 'REAL NOT NULL'),
        ('radius_miles', 'REAL NOT NULL DEFAULT 5.0'),
        ('counties', "TEXT DEFAULT ''"),
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_sent_at', 'TEXT'),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('zip_code', 'TEXT'),
        ('unsubscribe_token', 'TEXT'),
    ]:
        try:
            cursor.execute(f'ALTER TABLE sex_offender_alert_subscriptions ADD COLUMN {col} {definition}')
            print(f'✅ Added sex_offender_alert_subscriptions.{col}')
        except sqlite3.OperationalError:
            pass

    # Zip geocode cache — converts zip codes to lat/lon at signup time
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zip_geocode_cache (
            zip_code  TEXT PRIMARY KEY,
            lat       REAL,
            lon       REAL,
            cached_at TEXT DEFAULT (datetime('now'))
        )
    ''')


def ensure_crash_incident_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS crash_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL DEFAULT 'mhp_news',
            title TEXT NOT NULL,
            description TEXT,
            incident_type TEXT NOT NULL DEFAULT 'crash',
            severity TEXT NOT NULL DEFAULT 'unknown',
            status TEXT NOT NULL DEFAULT 'active',
            highway TEXT,
            mile_marker TEXT,
            nearest_city TEXT,
            county TEXT,
            lat REAL,
            lon REAL,
            injuries INTEGER,
            fatalities INTEGER,
            road_status TEXT,
            occurred_at TEXT,
            cleared_at TEXT,
            source_url TEXT,
            raw_html TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_updated_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_crash_incidents_external_id '
        'ON crash_incidents(external_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_crash_incidents_county '
        'ON crash_incidents(county, status, occurred_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_crash_incidents_highway '
        'ON crash_incidents(highway, status, occurred_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_crash_incidents_active '
        'ON crash_incidents(status, occurred_at)'
    )
    for col, definition in [
        ('external_id', 'TEXT UNIQUE NOT NULL'),
        ('source', "TEXT NOT NULL DEFAULT 'mhp_news'"),
        ('title', 'TEXT NOT NULL'),
        ('description', 'TEXT'),
        ('incident_type', "TEXT NOT NULL DEFAULT 'crash'"),
        ('severity', "TEXT NOT NULL DEFAULT 'unknown'"),
        ('status', "TEXT NOT NULL DEFAULT 'active'"),
        ('highway', 'TEXT'),
        ('mile_marker', 'TEXT'),
        ('nearest_city', 'TEXT'),
        ('county', 'TEXT'),
        ('lat', 'REAL'),
        ('lon', 'REAL'),
        ('injuries', 'INTEGER'),
        ('fatalities', 'INTEGER'),
        ('road_status', 'TEXT'),
        ('occurred_at', 'TEXT'),
        ('cleared_at', 'TEXT'),
        ('source_url', 'TEXT'),
        ('raw_html', 'TEXT'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_updated_at', "TEXT DEFAULT (datetime('now'))"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE crash_incidents ADD COLUMN {col} {definition}')
            print(f'✅ Added crash_incidents.{col}')
        except sqlite3.OperationalError:
            pass


def ensure_code_violation_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS code_violation_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            city TEXT NOT NULL,
            county TEXT,
            source_type TEXT NOT NULL DEFAULT 'portal',
            portal_url TEXT,
            request_email TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            last_checked_at TEXT,
            last_success_at TEXT,
            latest_error TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS property_addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address_slug TEXT UNIQUE NOT NULL,
            street TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'MT',
            zip TEXT,
            county TEXT,
            lat REAL,
            lon REAL,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS code_violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            property_address_id INTEGER,
            raw_address TEXT NOT NULL DEFAULT '',
            violation_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            date_issued TEXT,
            date_resolved TEXT,
            owner_name TEXT,
            description TEXT,
            fine_amount REAL,
            source_record_id TEXT,
            source_url TEXT,
            raw_json TEXT,
            hash_id TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES code_violation_sources(id) ON DELETE CASCADE,
            FOREIGN KEY (property_address_id) REFERENCES property_addresses(id) ON DELETE SET NULL
        )
    ''')

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_cv_sources_enabled '
        'ON code_violation_sources(is_enabled, city)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_property_addresses_slug '
        'ON property_addresses(address_slug)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_property_addresses_geo '
        'ON property_addresses(city, county)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_lookup '
        'ON code_violations(property_address_id, status, date_issued)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_source '
        'ON code_violations(source_id, last_seen_at)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_hash '
        'ON code_violations(hash_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_code_violations_type '
        'ON code_violations(violation_type)'
    )

    for col, definition in [
        ('county', 'TEXT'),
        ('source_type', "TEXT NOT NULL DEFAULT 'portal'"),
        ('portal_url', 'TEXT'),
        ('request_email', 'TEXT'),
        ('is_enabled', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_checked_at', 'TEXT'),
        ('last_success_at', 'TEXT'),
        ('latest_error', "TEXT DEFAULT ''"),
        ('notes', "TEXT DEFAULT ''"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE code_violation_sources ADD COLUMN {col} {definition}')
            print(f'✅ Added code_violation_sources.{col}')
        except sqlite3.OperationalError:
            pass


def ensure_civil_filing_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS civil_filing_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_key TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            adapter_type TEXT NOT NULL DEFAULT 'import_json',
            jurisdiction TEXT NOT NULL DEFAULT 'Montana',
            county TEXT,
            source_url TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            last_success_at TEXT,
            last_error TEXT DEFAULT '',
            last_run_count INTEGER NOT NULL DEFAULT 0,
            checkpoint_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS civil_filings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            property_address_id INTEGER,
            county TEXT NOT NULL,
            city TEXT,
            case_number TEXT NOT NULL,
            case_type_code TEXT,
            case_type_label TEXT,
            filing_class TEXT NOT NULL DEFAULT 'other',
            caption TEXT,
            plaintiff_name TEXT,
            defendant_name TEXT,
            raw_address TEXT DEFAULT '',
            filing_date TEXT,
            case_status TEXT,
            source_record_id TEXT,
            source_url TEXT,
            raw_json TEXT,
            hash_id TEXT UNIQUE,
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (source_id) REFERENCES civil_filing_sources(id) ON DELETE CASCADE,
            FOREIGN KEY (property_address_id) REFERENCES property_addresses(id) ON DELETE SET NULL
        )
    ''')

    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filing_sources_enabled '
        'ON civil_filing_sources(is_enabled, county)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_lookup '
        'ON civil_filings(property_address_id, filing_class, filing_date)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_case '
        'ON civil_filings(county, case_number)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_hash '
        'ON civil_filings(hash_id)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_civil_filings_source '
        'ON civil_filings(source_id, filing_date)'
    )

    for col, definition in [
        ('adapter_type', "TEXT NOT NULL DEFAULT 'import_json'"),
        ('jurisdiction', "TEXT NOT NULL DEFAULT 'Montana'"),
        ('county', 'TEXT'),
        ('source_url', 'TEXT'),
        ('is_enabled', 'INTEGER NOT NULL DEFAULT 1'),
        ('last_success_at', 'TEXT'),
        ('last_error', "TEXT DEFAULT ''"),
        ('last_run_count', 'INTEGER NOT NULL DEFAULT 0'),
        ('checkpoint_json', 'TEXT'),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE civil_filing_sources ADD COLUMN {col} {definition}')
            print(f'✅ Added civil_filing_sources.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('property_address_id', 'INTEGER'),
        ('county', "TEXT NOT NULL DEFAULT ''"),
        ('city', 'TEXT'),
        ('case_type_code', 'TEXT'),
        ('case_type_label', 'TEXT'),
        ('filing_class', "TEXT NOT NULL DEFAULT 'other'"),
        ('caption', 'TEXT'),
        ('plaintiff_name', 'TEXT'),
        ('defendant_name', 'TEXT'),
        ('raw_address', "TEXT DEFAULT ''"),
        ('filing_date', 'TEXT'),
        ('case_status', 'TEXT'),
        ('source_record_id', 'TEXT'),
        ('source_url', 'TEXT'),
        ('raw_json', 'TEXT'),
        ('hash_id', 'TEXT UNIQUE'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE civil_filings ADD COLUMN {col} {definition}')
            print(f'✅ Added civil_filings.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('county', 'TEXT'),
        ('lat', 'REAL'),
        ('lon', 'REAL'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE property_addresses ADD COLUMN {col} {definition}')
            print(f'✅ Added property_addresses.{col}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('property_address_id', 'INTEGER'),
        ('raw_address', "TEXT NOT NULL DEFAULT ''"),
        ('status', "TEXT NOT NULL DEFAULT 'open'"),
        ('date_issued', 'TEXT'),
        ('date_resolved', 'TEXT'),
        ('owner_name', 'TEXT'),
        ('description', 'TEXT'),
        ('fine_amount', 'REAL'),
        ('source_record_id', 'TEXT'),
        ('source_url', 'TEXT'),
        ('raw_json', 'TEXT'),
        ('hash_id', 'TEXT'),
        ('first_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('last_seen_at', "TEXT DEFAULT (datetime('now'))"),
        ('created_at', "TEXT DEFAULT (datetime('now'))"),
        ('updated_at', "TEXT DEFAULT (datetime('now'))"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE code_violations ADD COLUMN {col} {definition}')
            print(f'✅ Added code_violations.{col}')
        except sqlite3.OperationalError:
            pass


def ensure_license_sanction_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_sanction_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_key TEXT NOT NULL UNIQUE,
            board_name TEXT NOT NULL,
            board_url TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'html',
            last_fetched_at TEXT,
            last_status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_slug TEXT NOT NULL,
            license_number TEXT,
            board TEXT NOT NULL,
            violation_type TEXT,
            action_taken TEXT,
            effective_date TEXT,
            county TEXT,
            description TEXT,
            source_url TEXT,
            source_document_url TEXT,
            raw_extraction_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES license_sanction_sources(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_sanction_raw_extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            raw_html TEXT,
            raw_pdf_path TEXT,
            kimi_response_json TEXT,
            extraction_status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_id) REFERENCES license_sanction_sources(id) ON DELETE CASCADE
        )
    ''')

    for col, definition in [
        ('board_key', 'TEXT'),
        ('board_name', 'TEXT'),
        ('board_url', 'TEXT'),
        ('source_type', "TEXT NOT NULL DEFAULT 'html'"),
        ('last_fetched_at', 'TEXT'),
        ('last_status', 'TEXT'),
        ('created_at', "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE license_sanction_sources ADD COLUMN {col} {definition}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('source_id', 'INTEGER'),
        ('name', 'TEXT'),
        ('name_slug', 'TEXT'),
        ('license_number', 'TEXT'),
        ('board', 'TEXT'),
        ('violation_type', 'TEXT'),
        ('action_taken', 'TEXT'),
        ('effective_date', 'TEXT'),
        ('county', 'TEXT'),
        ('description', 'TEXT'),
        ('source_url', 'TEXT'),
        ('source_document_url', 'TEXT'),
        ('raw_extraction_id', 'INTEGER'),
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
        ('created_at', "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ('updated_at', "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE license_sanctions ADD COLUMN {col} {definition}')
        except sqlite3.OperationalError:
            pass

    for col, definition in [
        ('source_id', 'INTEGER'),
        ('fetched_at', 'TEXT'),
        ('raw_html', 'TEXT'),
        ('raw_pdf_path', 'TEXT'),
        ('kimi_response_json', 'TEXT'),
        ('extraction_status', "TEXT NOT NULL DEFAULT 'pending'"),
        ('error_message', 'TEXT'),
        ('created_at', "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]:
        try:
            cursor.execute(f'ALTER TABLE license_sanction_raw_extractions ADD COLUMN {col} {definition}')
        except sqlite3.OperationalError:
            pass

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_name_slug ON license_sanctions(name_slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_board ON license_sanctions(board)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_county ON license_sanctions(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_effective_date ON license_sanctions(effective_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_action ON license_sanctions(action_taken)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_active ON license_sanctions(is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_source ON license_sanctions(source_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ls_raw_extraction ON license_sanctions(raw_extraction_id)')
    conn.commit()


def seed_code_violation_sources(conn: sqlite3.Connection) -> None:
    sources = [
        ('billings', 'Billings Code Enforcement', 'Billings', 'Yellowstone'),
        ('missoula', 'Missoula Code Enforcement', 'Missoula', 'Missoula'),
        ('great_falls', 'Great Falls Code Enforcement', 'Great Falls', 'Cascade'),
        ('bozeman', 'Bozeman Code Enforcement', 'Bozeman', 'Gallatin'),
        ('helena', 'Helena Code Enforcement', 'Helena', 'Lewis and Clark'),
    ]
    for key, name, city, county in sources:
        conn.execute(
            '''
            INSERT OR IGNORE INTO code_violation_sources (source_key, display_name, city, county)
            VALUES (?, ?, ?, ?)
            ''',
            (key, name, city, county),
        )
    conn.commit()


def seed_civil_filing_sources(conn: sqlite3.Connection) -> None:
    counties = [
        'Beaverhead', 'Big Horn', 'Blaine', 'Broadwater', 'Carbon', 'Carter', 'Cascade',
        'Chouteau', 'Custer', 'Daniels', 'Dawson', 'Deer Lodge', 'Fallon', 'Fergus',
        'Flathead', 'Gallatin', 'Garfield', 'Glacier', 'Golden Valley', 'Granite', 'Hill',
        'Jefferson', 'Judith Basin', 'Lake', 'Lewis and Clark', 'Liberty', 'Lincoln',
        'Madison', 'McCone', 'Meagher', 'Mineral', 'Missoula', 'Musselshell', 'Park',
        'Petroleum', 'Phillips', 'Pondera', 'Powder River', 'Powell', 'Prairie',
        'Ravalli', 'Richland', 'Roosevelt', 'Rosebud', 'Sanders', 'Sheridan', 'Silver Bow',
        'Stillwater', 'Sweet Grass', 'Teton', 'Toole', 'Treasure', 'Valley', 'Wheatland',
        'Wibaux', 'Yellowstone',
    ]
    for county in counties:
        key = f"icourtcase-{county.lower().replace(' ', '-')}"
        name = f'iCourtCase {county} County'
        conn.execute(
            '''
            INSERT INTO civil_filing_sources (source_key, display_name, adapter_type, county, source_url)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                display_name=excluded.display_name,
                adapter_type=excluded.adapter_type,
                county=excluded.county,
                source_url=excluded.source_url,
                updated_at=datetime('now')
            ''',
            (key, name, 'icourtcase', county, 'https://dcportal.pubcourts.mt.gov/'),
        )
    conn.commit()


def ensure_agency_contacts_schema(conn):
    """Create agency_contacts table for weekly crime brief emails."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS agency_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county TEXT NOT NULL,
            agency_name TEXT,
            contact_email TEXT NOT NULL UNIQUE,
            contact_name TEXT,
            is_active INTEGER DEFAULT 1,
            weekly_brief_enabled INTEGER DEFAULT 1,
            last_sent_at TEXT,
            created_at TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_agency_contacts_county ON agency_contacts(county)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_agency_contacts_active ON agency_contacts(is_active, weekly_brief_enabled)')
    conn.commit()


def ensure_civic_records_requests_schema(conn):
    """End-to-end tracker for public-records requests to county agencies."""
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS civic_records_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county_slug TEXT NOT NULL,
            agency_name TEXT,
            request_type TEXT,
            subject TEXT NOT NULL,
            body TEXT,
            sent_at TEXT,
            sent_via TEXT,
            response_due_at TEXT,
            response_received_at TEXT,
            response_summary TEXT,
            response_file_path TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            follow_up_count INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_civic_records_requests_status ON civic_records_requests(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_civic_records_requests_county ON civic_records_requests(county_slug)')
    conn.commit()


def ensure_lea_schema(conn: sqlite3.Connection) -> None:
    """Create LEA panel tables for multi-tenant agency self-service."""
    cursor = conn.cursor()

    # --- lea_agencies: agency registry ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_agencies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_name TEXT NOT NULL UNIQUE,
            agency_type TEXT NOT NULL,
            county_slug TEXT NOT NULL,
            county_name TEXT NOT NULL,
            ori_number TEXT UNIQUE,
            primary_contact_name TEXT,
            primary_contact_email TEXT NOT NULL,
            primary_contact_phone TEXT,
            agency_website_url TEXT,
            verification_status TEXT DEFAULT 'pending',
            verified_by_user_id INTEGER,
            verified_at TEXT,
            timezone TEXT DEFAULT 'America/Denver',
            enable_blotter_publishing INTEGER DEFAULT 1,
            enable_roster_publishing INTEGER DEFAULT 0,
            enable_api_access INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agencies_slug ON lea_agencies(county_slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agencies_ori ON lea_agencies(ori_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agencies_status ON lea_agencies(verification_status)')

    # --- lea_users: per-agency users with RBAC ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'records_officer',
            is_active INTEGER DEFAULT 1,
            last_login_at TEXT,
            last_login_ip TEXT,
            mfa_enabled INTEGER DEFAULT 0,
            mfa_secret TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE (agency_id, email),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_users_agency ON lea_users(agency_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_users_active ON lea_users(agency_id, is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_users_email ON lea_users(email)')

    # --- lea_invitations: pending user invitations ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'records_officer',
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            accepted_at TEXT,
            invited_by_user_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
            FOREIGN KEY (invited_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_invitations_token ON lea_invitations(token)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_invitations_email ON lea_invitations(email, expires_at)')

    # --- lea_blotter_drafts: staged incident submissions ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_blotter_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            submitted_by_user_id INTEGER NOT NULL,
            incident_date TEXT NOT NULL,
            incident_time TEXT,
            cad_number TEXT,
            case_number TEXT,
            primary_offense_mca TEXT,
            charges_json TEXT,
            incident_location_block TEXT,
            incident_location_latitude REAL,
            incident_location_longitude REAL,
            public_narrative TEXT,
            arresting_agency TEXT,
            responding_officer TEXT,
            submission_status TEXT DEFAULT 'draft',
            published_at TEXT,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
            FOREIGN KEY (submitted_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_blotter_incident_date ON lea_blotter_drafts(agency_id, incident_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_blotter_status ON lea_blotter_drafts(agency_id, submission_status)')

    # --- lea_roster_snapshots: jail roster snapshots ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_roster_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            submitted_by_user_id INTEGER,
            snapshot_date TEXT NOT NULL,
            sync_type TEXT DEFAULT 'incremental',
            roster_json TEXT NOT NULL,
            total_inmates INTEGER DEFAULT 0,
            hash_checksum TEXT,
            ingestion_status TEXT DEFAULT 'staged',
            ingestion_error TEXT,
            published_at TEXT,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
            FOREIGN KEY (submitted_by_user_id) REFERENCES lea_users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_roster_agency_date ON lea_roster_snapshots(agency_id, snapshot_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_roster_hash ON lea_roster_snapshots(hash_checksum)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_roster_status ON lea_roster_snapshots(agency_id, ingestion_status)')

    # --- lea_api_tokens: hashed API tokens ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            user_id INTEGER,
            token_name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_created_from_ip TEXT,
            scopes TEXT NOT NULL,
            last_used_at TEXT,
            expires_at TEXT,
            is_revoked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES lea_users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_api_tokens_hash ON lea_api_tokens(token_hash)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_api_tokens_agency ON lea_api_tokens(agency_id, is_revoked)')

    # --- lea_audit_log: immutable audit trail (CJIS-compliant) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL,
            user_id INTEGER,
            actor_ip TEXT,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            change_summary TEXT,
            previous_state_json TEXT,
            new_state_json TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES lea_users(id) ON DELETE SET NULL
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_audit_timestamp ON lea_audit_log(agency_id, timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_audit_action ON lea_audit_log(action)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_audit_resource ON lea_audit_log(resource_type, resource_id)')

    # --- lea_agency_coverages: feature flags per agency ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_agency_coverages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER NOT NULL UNIQUE,
            blotter_coverage_tier TEXT DEFAULT 'standard',
            roster_coverage_tier TEXT DEFAULT 'off',
            supports_cad_export INTEGER DEFAULT 0,
            supports_rms_export INTEGER DEFAULT 0,
            supports_api_batch_upload INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES lea_agencies(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_agency_coverages_tier ON lea_agency_coverages(blotter_coverage_tier)')

    # --- lea_registration_interest: agency signup requests from landing page ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lea_registration_interest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_name TEXT NOT NULL,
            county_name TEXT NOT NULL,
            contact_name TEXT NOT NULL,
            contact_email TEXT NOT NULL,
            contact_phone TEXT,
            agency_type TEXT DEFAULT 'sheriff',
            message TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            contacted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_lea_registration_status ON lea_registration_interest(status)')

    conn.commit()


def ensure_lea_security_policies(conn: sqlite3.Connection) -> None:
    """Apply CJIS-compliant security policies for LEA panel tables.

    Immutable audit log, deletion guards, and RBAC enforcement triggers.
    Safe to call repeatedly — uses IF NOT EXISTS / idempotent DDL.
    """
    cursor = conn.cursor()

    # ── Immutable audit log ──────────────────────────────────────────────
    # CJIS requirement: audit records must be append-only.
    # SQLite has no native row-level BEFORE triggers for UPDATE/DELETE
    # enforcement on the table itself (AFTER still fires), so we use a
    # BEFORE trigger that raises an ABORT error.
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS lea_audit_log_prevent_update
        BEFORE UPDATE ON lea_audit_log
        BEGIN
            SELECT RAISE(ABORT, 'CJIS violation: UPDATE forbidden on lea_audit_log (immutable audit trail)');
        END;

        CREATE TRIGGER IF NOT EXISTS lea_audit_log_prevent_delete
        BEFORE DELETE ON lea_audit_log
        BEGIN
            SELECT RAISE(ABORT, 'CJIS violation: DELETE forbidden on lea_audit_log (immutable audit trail)');
        END;
    """)

    # ── API token deletion guard ─────────────────────────────────────────
    # Tokens in use should not be deleted — revoked is the canonical state.
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS lea_api_tokens_prevent_delete
        BEFORE DELETE ON lea_api_tokens
        BEGIN
            SELECT RAISE(ABORT, 'Security violation: DELETE forbidden on lea_api_tokens (use is_revoked=1 instead)');
        END;
    """)

    # ── Blotter draft submission status workflow ─────────────────────────
    # Enforce valid status transitions: draft → submitted → approved → published
    # Rejects illegal skips (e.g. draft → published).
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS lea_blotter_drafts_status_workflow
        BEFORE UPDATE OF submission_status ON lea_blotter_drafts
        WHEN NEW.submission_status != OLD.submission_status
        BEGIN
            SELECT
                CASE
                    WHEN OLD.submission_status = 'draft' AND NEW.submission_status NOT IN ('submitted', 'rejected')
                        THEN RAISE(ABORT, 'Workflow violation: draft can only transition to submitted or rejected')
                    WHEN OLD.submission_status = 'submitted' AND NEW.submission_status NOT IN ('approved', 'rejected')
                        THEN RAISE(ABORT, 'Workflow violation: submitted can only transition to approved or rejected')
                    WHEN OLD.submission_status = 'approved' AND NEW.submission_status NOT IN ('published', 'rejected')
                        THEN RAISE(ABORT, 'Workflow violation: approved can only transition to published or rejected')
                    WHEN OLD.submission_status = 'published' AND NEW.submission_status != 'published'
                        THEN RAISE(ABORT, 'Workflow violation: published is a terminal state')
                    WHEN OLD.submission_status = 'rejected' AND NEW.submission_status != 'rejected'
                        THEN RAISE(ABORT, 'Workflow violation: rejected is a terminal state')
                END;
        END;
    """)

    # ── Roster ingestion status workflow ─────────────────────────────────
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS lea_roster_status_workflow
        BEFORE UPDATE OF ingestion_status ON lea_roster_snapshots
        WHEN NEW.ingestion_status != OLD.ingestion_status
        BEGIN
            SELECT
                CASE
                    WHEN OLD.ingestion_status = 'staged' AND NEW.ingestion_status NOT IN ('processing', 'rejected')
                        THEN RAISE(ABORT, 'Workflow violation: staged can only transition to processing or rejected')
                    WHEN OLD.ingestion_status = 'processing' AND NEW.ingestion_status NOT IN ('published', 'rejected')
                        THEN RAISE(ABORT, 'Workflow violation: processing can only transition to published or rejected')
                    WHEN OLD.ingestion_status IN ('published', 'rejected') AND NEW.ingestion_status != OLD.ingestion_status
                        THEN RAISE(ABORT, 'Workflow violation: published/rejected are terminal states')
                END;
        END;
    """)

    # ── RBAC role validation ─────────────────────────────────────────────
    # Enforce valid role values at the DB level.
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS lea_users_role_validation
        BEFORE INSERT ON lea_users
        WHEN NEW.role NOT IN ('admin', 'pio', 'records_officer')
        BEGIN
            SELECT RAISE(ABORT, 'RBAC violation: role must be admin, pio, or records_officer');
        END;

        CREATE TRIGGER IF NOT EXISTS lea_users_role_update_validation
        BEFORE UPDATE OF role ON lea_users
        WHEN NEW.role NOT IN ('admin', 'pio', 'records_officer')
        BEGIN
            SELECT RAISE(ABORT, 'RBAC violation: role must be admin, pio, or records_officer');
        END;
    """)

    conn.commit()


if __name__ == "__main__":
    init_database()
    migrate()
