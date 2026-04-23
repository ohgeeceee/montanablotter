CREATE TABLE IF NOT EXISTS bondsman_subscribers (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_subscribed BOOLEAN NOT NULL DEFAULT FALSE,
    subscriber_plan TEXT NOT NULL DEFAULT 'free',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS inmate_bookings (
    id BIGSERIAL PRIMARY KEY,
    county_slug TEXT NOT NULL,
    county_name TEXT NOT NULL,
    facility_name TEXT NOT NULL,
    person_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    date_of_birth DATE,
    booking_number TEXT,
    booking_at TIMESTAMPTZ,
    release_at TIMESTAMPTZ,
    charges_summary TEXT NOT NULL DEFAULT '',
    source_url TEXT,
    source_record_id TEXT,
    booking_status TEXT NOT NULL DEFAULT 'current',
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inmate_bookings_lookup
ON inmate_bookings (county_slug, is_current, booking_at DESC);

CREATE INDEX IF NOT EXISTS idx_inmate_bookings_name
ON inmate_bookings (normalized_name, date_of_birth);

CREATE TABLE IF NOT EXISTS bondsman_watchlists (
    id BIGSERIAL PRIMARY KEY,
    subscriber_id BIGINT NOT NULL REFERENCES bondsman_subscribers(id) ON DELETE CASCADE,
    watch_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    date_of_birth DATE,
    notes TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bondsman_watchlists_lookup
ON bondsman_watchlists (subscriber_id, normalized_name, is_active);

CREATE TABLE IF NOT EXISTS bondsman_alerts (
    id BIGSERIAL PRIMARY KEY,
    subscriber_id BIGINT NOT NULL REFERENCES bondsman_subscribers(id) ON DELETE CASCADE,
    watchlist_id BIGINT REFERENCES bondsman_watchlists(id) ON DELETE SET NULL,
    booking_id BIGINT NOT NULL REFERENCES inmate_bookings(id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL DEFAULT 'watch_match',
    alert_status TEXT NOT NULL DEFAULT 'open',
    match_confidence TEXT NOT NULL DEFAULT 'name_only',
    matched_name TEXT NOT NULL,
    matched_date_of_birth DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bondsman_alerts_unique_match
ON bondsman_alerts (subscriber_id, watchlist_id, booking_id, alert_type);

CREATE TABLE IF NOT EXISTS bondsman_cases (
    id BIGSERIAL PRIMARY KEY,
    subscriber_id BIGINT NOT NULL REFERENCES bondsman_subscribers(id) ON DELETE CASCADE,
    booking_id BIGINT NOT NULL UNIQUE REFERENCES inmate_bookings(id) ON DELETE CASCADE,
    defendant_name TEXT NOT NULL,
    date_of_birth DATE,
    bond_amount_cents BIGINT,
    court_date TIMESTAMPTZ,
    payment_status TEXT NOT NULL DEFAULT 'pending',
    case_status TEXT NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    checkin_token TEXT NOT NULL UNIQUE,
    last_checkin_at TIMESTAMPTZ,
    last_checkin_latitude DOUBLE PRECISION,
    last_checkin_longitude DOUBLE PRECISION,
    last_checkin_selfie_path TEXT,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bondsman_cases_user
ON bondsman_cases (subscriber_id, case_status, court_date);

CREATE TABLE IF NOT EXISTS bondsman_client_checkins (
    id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES bondsman_cases(id) ON DELETE CASCADE,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    selfie_path TEXT,
    notes TEXT NOT NULL DEFAULT '',
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bondsman_client_checkins_case
ON bondsman_client_checkins (case_id, submitted_at DESC);
