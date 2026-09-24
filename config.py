"""
Runtime configuration for Montana Blotter.

Secrets must be supplied via environment variables.
"""

import os
from urllib.parse import urlparse


def _load_env_file_defaults(path: str) -> None:
    """
    Populate os.environ from a KEY=VALUE file without overriding existing vars.
    This keeps cron-launched scripts aligned with systemd EnvironmentFile values.
    """
    if not os.path.exists(path):
        return

    try:
        with open(path, 'r', encoding='utf-8') as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue

                key, value = line.split('=', 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue

                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]

                os.environ[key] = value
    except OSError:
        # If the file is unreadable, continue with existing environment values.
        return


_load_env_file_defaults(os.path.join(os.path.dirname(__file__), '.env'))


def _env(name: str, default: str = '') -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name, '')
    if not value:
        return default
    return value.lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int) -> int:
    raw = _env(name, '')
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_list(name: str, default: list[int]) -> list[int]:
    raw = _env(name, '')
    if not raw:
        return list(default)

    values = []
    for part in raw.split(','):
        token = part.strip()
        if not token:
            continue
        try:
            values.append(int(token))
        except ValueError:
            continue
    return values or list(default)


def _env_csv(name: str, default: str = '') -> tuple[str, ...]:
    raw = _env(name, default)
    if not raw:
        return tuple()
    return tuple(part.strip() for part in raw.split(',') if part.strip())


ENVIRONMENT = _env('MB_ENV', _env('FLASK_ENV', 'development')).lower()
IS_PRODUCTION = ENVIRONMENT in {'prod', 'production'}

# Database
DB_PATH = _env('MB_DB_PATH', '/root/montanablotter/blotter.db')
DB_TIMEOUT_SECONDS = _env_int('MB_DB_TIMEOUT_SECONDS', 30)
DB_BUSY_TIMEOUT_MS = _env_int('MB_DB_BUSY_TIMEOUT_MS', 30000)
SCOUT_HTTP_MAX_ATTEMPTS = _env_int('MB_SCOUT_HTTP_MAX_ATTEMPTS', 5)
SCOUT_HTTP_BASE_DELAY_SECONDS = float(_env('MB_SCOUT_HTTP_BASE_DELAY_SECONDS', '0.75') or '0.75')
SCOUT_HTTP_MAX_DELAY_SECONDS = float(_env('MB_SCOUT_HTTP_MAX_DELAY_SECONDS', '12') or '12')
SCOUT_HTTP_JITTER_SECONDS = float(_env('MB_SCOUT_HTTP_JITTER_SECONDS', '0.8') or '0.8')
SCOUT_INTER_PAGE_DELAY_MIN_SECONDS = float(_env('MB_SCOUT_INTER_PAGE_DELAY_MIN_SECONDS', '0.15') or '0.15')
SCOUT_INTER_PAGE_DELAY_MAX_SECONDS = float(_env('MB_SCOUT_INTER_PAGE_DELAY_MAX_SECONDS', '1.0') or '1.0')

# Proxy for court scrapers (format: http://user:pass@host:port)
HTTP_PROXY = _env('MB_HTTP_PROXY')
HTTPS_PROXY = _env('MB_HTTPS_PROXY', HTTP_PROXY)

# Flask app
SECRET_KEY = _env('MB_SECRET_KEY', _env('SECRET_KEY'))
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError('MB_SECRET_KEY (or SECRET_KEY) must be set in production')
    SECRET_KEY = 'dev-only-change-me'

DEBUG = _env_bool('MB_DEBUG', default=not IS_PRODUCTION)
HOST = _env('MB_HOST', '0.0.0.0')
PORT = _env_int('MB_PORT', 80)
BASE_URL = _env('MB_BASE_URL', 'https://montanablotter.com')
CLAW3D_OFFICE_URL = _env('MB_CLAW3D_OFFICE_URL', '/admin/office/office')
_base_url = urlparse(BASE_URL)
BASE_ORIGIN = (
    f"{_base_url.scheme}://{_base_url.netloc}"
    if _base_url.scheme and _base_url.netloc
    else 'https://montanablotter.com'
)
_base_host = (urlparse(BASE_URL).hostname or 'montanablotter.com').strip().lower()

# Global sign-in wall: require a free public account for every page except the
# homepage and a small set of auth/payment/static exemptions.
REQUIRE_SIGNIN_WALL = _env_bool('MB_REQUIRE_SIGNIN', True)
AGENDAS_HOST = _env('MB_AGENDAS_HOST', f'agendas.{_base_host}')
AGENDAS_BASE_URL = _env('MB_AGENDAS_BASE_URL', f'https://{AGENDAS_HOST}')
AGENDAS_CONFIG_PATH = _env('MB_AGENDAS_CONFIG_PATH', '/root/montanablotter/configs/agendas/cities.example.json')

# Distinct cookie name + explicit apex domain so sibling subdomains
# (dev.*, agendas.*, blotter.host fleet, etc.) sharing the old default
# `session` name can't collide and clobber the admin session/CSRF token.
SESSION_COOKIE_NAME = _env('MB_SESSION_COOKIE_NAME', 'mb_session')
SESSION_COOKIE_DOMAIN = _env('MB_SESSION_COOKIE_DOMAIN', '')
SESSION_COOKIE_SECURE = _env_bool('MB_SESSION_COOKIE_SECURE', default=IS_PRODUCTION)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = _env('MB_SESSION_COOKIE_SAMESITE', 'Lax')

# Admin login abuse controls
ADMIN_LOGIN_MAX_ATTEMPTS = max(1, _env_int('MB_ADMIN_LOGIN_MAX_ATTEMPTS', 5))
ADMIN_LOGIN_WINDOW_MINUTES = max(1, _env_int('MB_ADMIN_LOGIN_WINDOW_MINUTES', 15))
ADMIN_LOGIN_LOCKOUT_MINUTES = max(1, _env_int('MB_ADMIN_LOGIN_LOCKOUT_MINUTES', 15))

# Security headers
CONTENT_SECURITY_POLICY = _env(
    'MB_CONTENT_SECURITY_POLICY',
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://www.google.com https://www.recaptcha.net https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com https://quge5.com; "
    "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com https://www.googletagmanager.com https://quge5.com; "
    "frame-src 'self' https://www.google.com https://www.recaptcha.net https://quge5.com; "
    "base-uri 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self' https://checkout.stripe.com"
)
REFERRER_POLICY = _env('MB_REFERRER_POLICY', 'strict-origin-when-cross-origin')
X_FRAME_OPTIONS = _env('MB_X_FRAME_OPTIONS', 'SAMEORIGIN')
STRICT_TRANSPORT_SECURITY = _env(
    'MB_STRICT_TRANSPORT_SECURITY',
    'max-age=31536000; includeSubDomains',
)
PERMISSIONS_POLICY = _env(
    'MB_PERMISSIONS_POLICY',
    'geolocation=(), microphone=(), camera=()',
)
API_CORS_ALLOW_ORIGIN = _env('MB_API_CORS_ALLOW_ORIGIN', BASE_ORIGIN)

# Server-side API cache (Flask-Caching). Use Redis in production; simple
# in-memory cache is the default for single-node deployments.
CACHE_TYPE = _env('MB_CACHE_TYPE', 'SimpleCache')
CACHE_REDIS_URL = _env('MB_CACHE_REDIS_URL', '')
CACHE_DEFAULT_TIMEOUT = _env_int('MB_CACHE_DEFAULT_TIMEOUT', 300)

# Upload controls
MAX_UPLOAD_MB = max(1, _env_int('MB_MAX_UPLOAD_MB', 20))

# Mobile in-app purchase verification (RevenueCat)
REVENUECAT_SECRET_API_KEY = _env('MB_REVENUECAT_SECRET_API_KEY', '')
REVENUECAT_PREMIUM_ENTITLEMENT_ID = _env('MB_REVENUECAT_PREMIUM_ENTITLEMENT_ID', 'premium')

# Email settings (IONOS IMAP)
EMAIL_USER = _env('MB_EMAIL_USER')
EMAIL_PASSWORD = _env('MB_EMAIL_PASSWORD')
IMAP_SERVER = _env('MB_IMAP_SERVER', 'imap.ionos.com')
IMAP_PORT = _env_int('MB_IMAP_PORT', 993)

# Secondary IMAP inbox (Gmail) — optional. If both user and password
# are set, email_worker.py polls this mailbox in addition to IONOS.
# Leave blank to disable the second inbox.
GMAIL_IMAP_USER = _env('MB_GMAIL_IMAP_USER')
GMAIL_IMAP_PASSWORD = _env('MB_GMAIL_IMAP_PASSWORD')
GMAIL_IMAP_SERVER = _env('MB_GMAIL_IMAP_SERVER', 'imap.gmail.com')
GMAIL_IMAP_PORT = _env_int('MB_GMAIL_IMAP_PORT', 993)

# Forward-only IMAP inbox (e.g. advertising@montanablotter.com). If both user
# and password are set, email_worker.py polls this mailbox and re-sends every
# inbound message to MB_EMAIL_FORWARD_TO via SMTP. It is intentionally excluded
# from the blotter-ingest pipeline so it never feeds records into the DB.
ADVERTISING_IMAP_USER = _env('MB_ADVERTISING_IMAP_USER')
ADVERTISING_IMAP_PASSWORD = _env('MB_ADVERTISING_IMAP_PASSWORD')
ADVERTISING_IMAP_SERVER = _env('MB_ADVERTISING_IMAP_SERVER', 'imap.ionos.com')
ADVERTISING_IMAP_PORT = _env_int('MB_ADVERTISING_IMAP_PORT', 993)
EMAIL_FORWARD_TO = _env('MB_EMAIL_FORWARD_TO', 'montanablotter@gmail.com')

# Outbound SMTP
SMTP_SERVER = _env('MB_SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = _env_int('MB_SMTP_PORT', 587)
SMTP_USER = _env('MB_SMTP_USER')
SMTP_PASSWORD = _env('MB_SMTP_PASSWORD')
ADMIN_ALERT_EMAILS = _env_csv('MB_ADMIN_ALERT_EMAILS')
INGEST_ALERT_REPEAT_HOURS = max(1, _env_int('MB_INGEST_ALERT_REPEAT_HOURS', 24))
TWILIO_ACCOUNT_SID = _env('MB_TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = _env('MB_TWILIO_AUTH_TOKEN')
TWILIO_FROM_NUMBER = _env('MB_TWILIO_FROM_NUMBER')
BAIL_BONDS_ALERTS_ENABLED = _env_bool('MB_BAIL_BONDS_ALERTS_ENABLED', default=True)

# Bail bonds consumer lead routing
BAIL_HELP_PHONE = _env('MB_BAIL_HELP_PHONE')
BAIL_HELP_SMS = _env('MB_BAIL_HELP_SMS')
BAIL_HELP_CHAT_URL = _env('MB_BAIL_HELP_CHAT_URL')
BAIL_LEAD_NOTIFY_EMAILS = _env_csv(
    'MB_BAIL_LEAD_NOTIFY_EMAILS',
    _env('MB_BAIL_AD_LEAD_ALERT_EMAILS'),
)
BAIL_LEAD_WEBHOOK_URL = _env(
    'MB_BAIL_LEAD_WEBHOOK_URL',
    _env('MB_BAIL_AD_LEAD_ALERT_WEBHOOK'),
)

# File paths
UPLOAD_DIR = _env('MB_UPLOAD_DIR', '/root/montanablotter/uploads')
RECORDS_DIR = _env('MB_RECORDS_DIR', '/root/montanablotter/records')
LOG_FILE = _env('MB_LOG_FILE', '/root/montanablotter/logs/worker.log')

# Email processing
PROCESSED_FOLDER = _env('MB_PROCESSED_FOLDER', 'Processed')
BLOTTER_SUBJECT_KEYWORD = _env('MB_BLOTTER_SUBJECT_KEYWORD', 'Blotter')

# Optional AI keys
ANTHROPIC_API_KEY = _env('ANTHROPIC_API_KEY', _env('MB_ANTHROPIC_API_KEY'))
OPENAI_API_KEY = _env('OPENAI_API_KEY', _env('MB_OPENAI_API_KEY'))
GROQ_API_KEY = _env('GROQ_API_KEY', _env('MB_GROQ_API_KEY'))

# Master switch for paid LLM providers (Claude/OpenAI). Defaults to False so
# ingestion uses the free, local summarization/audit path and avoids API costs.
USE_PAID_LLM = _env_bool('MB_USE_PAID_LLM', False)
OLLAMA_MODEL = _env('MB_OLLAMA_MODEL', 'llama3.2')
OLLAMA_HOST = _env('MB_OLLAMA_HOST', 'http://127.0.0.1:11434')

EMBEDDING_API_KEY = _env('MB_EMBEDDING_API_KEY', OPENAI_API_KEY)
EMBEDDING_MODEL = _env('MB_EMBEDDING_MODEL', 'text-embedding-3-small')
EMBEDDING_DIMENSIONS = _env_int('MB_EMBEDDING_DIMENSIONS', 1536)

# Supabase / pgvector meeting search pipeline
SUPABASE_PGVECTOR_DSN = _env('MB_SUPABASE_PGVECTOR_DSN')
SUPABASE_PGVECTOR_SCHEMA = _env('MB_SUPABASE_PGVECTOR_SCHEMA', 'public')
TESSERACT_CMD = _env('MB_TESSERACT_CMD')

# Optional Facebook publisher settings
FACEBOOK_APP_ID = _env('MB_FACEBOOK_APP_ID')
FACEBOOK_APP_SECRET = _env('MB_FACEBOOK_APP_SECRET')
FACEBOOK_PAGE_ID = _env('MB_FACEBOOK_PAGE_ID')
FACEBOOK_PAGE_ACCESS_TOKEN = _env('MB_FACEBOOK_PAGE_ACCESS_TOKEN')
FACEBOOK_GRAPH_API_VERSION = _env('MB_FACEBOOK_GRAPH_API_VERSION', 'v22.0')

# Unsplash image search (for Facebook photo posts)
UNSPLASH_ACCESS_KEY = _env('MB_UNSPLASH_ACCESS_KEY')

# Instagram publisher (Business account linked to Facebook Page)
INSTAGRAM_BUSINESS_ACCOUNT_ID = _env('MB_INSTAGRAM_BUSINESS_ACCOUNT_ID')

# Web Push (VAPID)
VAPID_PUBLIC_KEY = _env('MB_VAPID_PUBLIC_KEY', 'BPoB3tL72XUvztcnFzdqjjWvSXSRZNmDOxWaJYJQBnNhthjZ6CrkFril5lmcuKmTgERxNs3PIPwxS6dCeQNIAiw')
VAPID_PRIVATE_KEY_PATH = _env('MB_VAPID_PRIVATE_KEY_PATH', '/root/montanablotter/vapid_private.pem')
VAPID_CLAIMS_EMAIL = _env('MB_VAPID_CLAIMS_EMAIL', 'ohjoncurrie@gmail.com')

# fal.ai image generation (optional — text graphic used if key absent)
FAL_API_KEY = _env('MB_FAL_API_KEY')

# Social posting master switch
SOCIAL_POSTING_ENABLED = _env_bool('MB_SOCIAL_POSTING_ENABLED', default=False)

# Donations (Phase 0 scaffolding)
DONATIONS_ENABLED = _env_bool('MB_DONATIONS_ENABLED', default=False)
STRIPE_SECRET_KEY = _env('MB_STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = _env('MB_STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = _env('MB_STRIPE_WEBHOOK_SECRET')
STRIPE_WARRANT_WEBHOOK_SECRET = _env('MB_STRIPE_WARRANT_WEBHOOK_SECRET')
STRIPE_MONTHLY_SUBSCRIPTION_LINK = _env('MB_STRIPE_MONTHLY_SUBSCRIPTION_LINK')

# Ad-watched warrant unlock (free-for-attention path).
# Provider priority: if WARRANT_UNLOCK_MONETAG_ZONE_ID is set, the page uses
# Monetag's rewarded video SDK (real ad revenue, $1-5 CPM for US traffic).
# Otherwise it falls back to the YouTube embed (no revenue — useful for
# sponsor/PSA content where you don't want third-party ads).
WARRANT_UNLOCK_MONETAG_ZONE_ID = _env('MB_WARRANT_UNLOCK_MONETAG_ZONE_ID')
WARRANT_UNLOCK_YOUTUBE_VIDEO_ID = _env('MB_WARRANT_UNLOCK_YOUTUBE_VIDEO_ID')
WARRANT_UNLOCK_MIN_WATCH_SECONDS = max(1, _env_int('MB_WARRANT_UNLOCK_MIN_WATCH_SECONDS', 15))
WARRANT_UNLOCK_DURATION_HOURS = max(1, _env_int('MB_WARRANT_UNLOCK_DURATION_HOURS', 24))
WARRANT_UNLOCK_RATE_LIMIT_PER_HOUR = max(1, _env_int('MB_WARRANT_UNLOCK_RATE_LIMIT_PER_HOUR', 5))
WARRANT_UNLOCK_NONCE_TTL_SECONDS = max(60, _env_int('MB_WARRANT_UNLOCK_NONCE_TTL_SECONDS', 600))
# Disposition API ($19/mo). Configure when the Stripe product is created.
STRIPE_DISPOSITION_API_PRICE_ID = _env('MB_STRIPE_DISPOSITION_API_PRICE_ID')
STRIPE_DISPOSITION_PAYMENT_LINK = _env('MB_STRIPE_DISPOSITION_PAYMENT_LINK')

# Warrant access subscription.
# Pricing model (2026-08-05):
#   - Weekly:  $3.99 / week
#   - Monthly: $12 / month
#   - Annual:  $99 / year
#   - Cancellation is self-serve in the Stripe customer portal
#
# REQUIRED STRIPE OBJECTS:
#   1. Recurring weekly Price ID at $7.00 USD (set in env when ready).
#      Create in Stripe Dashboard: Products → New → recurring, $7.00 / week.
#   2. First-invoice coupon at amount_off=600 (=$6), duration='once',
#      currency=usd, applies_to=the weekly price above. The coupon brings
#      the first invoice from $7 to $1; subsequent invoices are $7/wk.
#      Create in Stripe Dashboard: Products → Coupons → New.
#   3. The Stripe Customer Portal must be enabled so the user can cancel
#      at any time without a refund-cycle workflow on our side.
WARRANT_WEEKLY_PRICE_ID = _env('MB_WARRANT_WEEKLY_PRICE_ID')
WARRANT_MONTHLY_PRICE_ID = _env('MB_WARRANT_MONTHLY_PRICE_ID')
WARRANT_ANNUAL_PRICE_ID = _env('MB_WARRANT_ANNUAL_PRICE_ID')

# Paid name-removal / privacy-suppression: one-time payment covering a
# verified review. On approval the name is redacted (not deleted) across records.
# The ACTUAL charged amount is the Stripe Price in MB_NAME_SUPPRESS_PRICE_ID.
# NAME_SUPPRESS_AMOUNT_LABEL is only the displayed price string; keep it in sync
# with the Stripe Price (update the Stripe dashboard when you change the amount).
NAME_SUPPRESS_PRODUCT_ID = _env('MB_NAME_SUPPRESS_PRODUCT_ID')
NAME_SUPPRESS_PRICE_ID = _env('MB_NAME_SUPPRESS_PRICE_ID')
NAME_SUPPRESS_AMOUNT_LABEL = _env('MB_NAME_SUPPRESS_AMOUNT_LABEL', '$99')

# Supporter tier — $1/month subscription
STRIPE_SUPPORTER_PRICE_ID = _env(
    'MB_STRIPE_SUPPORTER_PRICE_ID',
    'price_1ThZA5GL8T8btZcuvOSD4EUK',
)
STRIPE_SUPPORTER_PRODUCT_ID = _env(
    'MB_STRIPE_SUPPORTER_PRODUCT_ID',
    'prod_Ugx4dJmT1A6ePR',
)
DONATION_MONTHLY_PRICE_ID = _env('MB_DONATION_MONTHLY_PRICE_ID')
DONATION_CURRENCY = (_env('MB_DONATION_CURRENCY', 'usd') or 'usd').lower()
DONATION_MIN_CENTS = max(100, _env_int('MB_DONATION_MIN_CENTS', 500))

# Subscription tiers (Insider / Professional)
INSIDER_MONTHLY_PRICE_ID = _env('MB_INSIDER_MONTHLY_PRICE_ID')
INSIDER_YEARLY_PRICE_ID = _env('MB_INSIDER_YEARLY_PRICE_ID')
PRO_MONTHLY_PRICE_ID = _env('MB_PRO_MONTHLY_PRICE_ID')
PRO_YEARLY_PRICE_ID = _env('MB_PRO_YEARLY_PRICE_ID')
_donation_amount_defaults = [500, 1500, 2500, 5000]
_donation_amount_candidates = _env_int_list('MB_DONATION_SUGGESTED_AMOUNTS', _donation_amount_defaults)
_donation_amounts_filtered = sorted({amt for amt in _donation_amount_candidates if amt >= DONATION_MIN_CENTS})
if not _donation_amounts_filtered:
    _donation_amounts_filtered = [amt for amt in _donation_amount_defaults if amt >= DONATION_MIN_CENTS] or [DONATION_MIN_CENTS]
DONATION_SUGGESTED_AMOUNTS_CENTS = tuple(_donation_amounts_filtered)

# Logging
LOG_LEVEL = _env('MB_LOG_LEVEL', 'INFO')
LOG_FORMAT = _env('MB_LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# reCAPTCHA v3
RECAPTCHA_SITE_KEY = _env('MB_RECAPTCHA_SITE_KEY')
RECAPTCHA_SECRET_KEY = _env('MB_RECAPTCHA_SECRET_KEY')
RECAPTCHA_SCORE_THRESHOLD = float(_env('MB_RECAPTCHA_SCORE_THRESHOLD', '0.5'))
RECAPTCHA_ENABLED = bool(RECAPTCHA_SITE_KEY and RECAPTCHA_SECRET_KEY)

# Telegram (OpenClaw BailBot)
TELEGRAM_BOT_TOKEN = _env('MB_TELEGRAM_BOT_TOKEN')
TELEGRAM_TARGET_DEFAULT = _env('MB_TELEGRAM_TARGET_DEFAULT')
TELEGRAM_TARGET_CASCADE = _env('MB_TELEGRAM_TARGET_CASCADE')
TELEGRAM_TARGET_YELLOWSTONE = _env('MB_TELEGRAM_TARGET_YELLOWSTONE')

# Montana counties
MONTANA_COUNTIES = [
    'Beaverhead', 'Big Horn', 'Blaine', 'Broadwater', 'Carbon',
    'Carter', 'Cascade', 'Chouteau', 'Custer', 'Daniels',
    'Dawson', 'Deer Lodge', 'Fallon', 'Fergus', 'Flathead',
    'Gallatin', 'Garfield', 'Glacier', 'Golden Valley', 'Granite',
    'Hill', 'Jefferson', 'Judith Basin', 'Lake', 'Lewis and Clark',
    'Liberty', 'Lincoln', 'Madison', 'McCone', 'Meagher',
    'Mineral', 'Missoula', 'Musselshell', 'Park', 'Petroleum',
    'Phillips', 'Pondera', 'Powder River', 'Powell', 'Prairie',
    'Ravalli', 'Richland', 'Roosevelt', 'Rosebud', 'Sanders',
    'Sheridan', 'Silver Bow', 'Stillwater', 'Sweet Grass', 'Teton',
    'Toole', 'Treasure', 'Valley', 'Wheatland', 'Wibaux', 'Yellowstone',
]
