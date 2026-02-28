"""
Database Schema Initialization for Montana Blotter
Creates all necessary tables with proper structure
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = '/root/montanablotter/blotter.db'

def init_database():
    """Initialize the database with all required tables"""
    
    # Backup existing database if it exists
    if os.path.exists(DB_PATH):
        backup_path = f'{DB_PATH}.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        print(f"⚠️  Backing up existing database to: {backup_path}")
        os.system(f'cp {DB_PATH} {backup_path}')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # --- USERS TABLE ---
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
    
    # --- BLOTTERS TABLE (Batch/File level) ---
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
    
    # --- RECORDS TABLE (Individual incidents) ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blotter_id INTEGER NOT NULL,
            cfs_number TEXT,
            date TEXT NOT NULL,
            time TEXT,
            incident_type TEXT,
            location TEXT,
            details TEXT,
            county TEXT NOT NULL,
            officer TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (blotter_id) REFERENCES blotters(id) ON DELETE CASCADE
        )
    ''')
    
    # --- COMMAND LOGS TABLE (Detailed chronological entries for each incident) ---
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
    
    # --- Create indexes for better query performance ---
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_county ON records(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_date ON records(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_blotter ON records(blotter_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blotters_county ON blotters(county)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_blotters_date ON blotters(upload_date)')
    
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
    cursor = conn.cursor()

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
