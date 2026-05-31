ALTER TABLE public_users ADD COLUMN subscriber_plan TEXT DEFAULT 'scout';
ALTER TABLE public_users ADD COLUMN stripe_subscription_id TEXT;
ALTER TABLE public_users ADD COLUMN subscription_status TEXT DEFAULT 'active';
