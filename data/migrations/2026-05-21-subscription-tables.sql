CREATE TABLE IF NOT EXISTS subscription_tiers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    monthly_cents INTEGER NOT NULL DEFAULT 0,
    annual_cents  INTEGER NOT NULL DEFAULT 0,
    stripe_monthly_price_id TEXT,
    stripe_annual_price_id  TEXT,
    features_json TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES public_users(id),
    stripe_subscription_id TEXT UNIQUE,
    stripe_customer_id     TEXT,
    tier_slug   TEXT NOT NULL REFERENCES subscription_tiers(slug),
    status      TEXT NOT NULL DEFAULT 'trialing',
    current_period_start DATETIME,
    current_period_end   DATETIME,
    cancel_at_period_end INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subscription_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    stripe_event_id TEXT,
    event_type  TEXT,
    payload_json TEXT,
    processed   INTEGER DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paywall_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_hash     TEXT,
    user_id     INTEGER,
    session_id  TEXT,
    content_type TEXT,
    content_id  TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
