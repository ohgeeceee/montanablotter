ALTER TABLE subscribers ADD COLUMN agency_name TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN phone_number_sms TEXT NOT NULL DEFAULT '';
ALTER TABLE subscribers ADD COLUMN counties_of_interest TEXT NOT NULL DEFAULT '[]';
ALTER TABLE subscribers ADD COLUMN charge_types_of_interest TEXT NOT NULL DEFAULT '["all"]';
ALTER TABLE subscribers ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'active';

CREATE INDEX IF NOT EXISTS idx_subscribers_bail_bonds_active
ON subscribers(subscription_status, active);

CREATE TABLE IF NOT EXISTS bail_bonds_sms_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id INTEGER NOT NULL,
    booking_id INTEGER NOT NULL,
    county_slug TEXT NOT NULL DEFAULT '',
    phone_number_sms TEXT NOT NULL DEFAULT '',
    sms_body TEXT NOT NULL DEFAULT '',
    delivery_status TEXT NOT NULL DEFAULT 'queued',
    provider_message_id TEXT,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    delivered_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bail_bonds_sms_delivery_unique
ON bail_bonds_sms_deliveries(subscriber_id, booking_id);

CREATE INDEX IF NOT EXISTS idx_bail_bonds_sms_delivery_status
ON bail_bonds_sms_deliveries(delivery_status, created_at);
