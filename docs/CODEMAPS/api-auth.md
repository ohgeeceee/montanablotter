# REST API & Authentication — Montana Blotter

Covers API routes, token systems, admin login/MFA brute-force throttling, configuration variables, and the JWT ops session used for cross-service admin access.

## Module Map

| File | Responsibility |
|------|----------------|
| `blueprints/api.py` | Primary public/website API routes (`/api/me/...`, `/api/geo/...`, `/api/posts`, `/api/scorecards`, etc.) protected by `@require_api_key`. *
| `services/api/auth.py` | API key generation, SHA-256 hashing, validation, tiered rate limiting, request logging, `ensure_api_auth_schema()`. |
| `services/api/routes.py` | Legacy/standalone JSON routes (`/api/stats`, `/api/records`, `/api/records/recent`, `/api/fwp-violations`, `/api/filters`, `/api/timeline`). Mostly separate from the main API blueprint. |
| `blueprints/auth.py` | Public user authentication: register, login, Facebook (disabled), password reset flows, public account session management. |
| `blueprints/admin/security.py` | Admin login form, brute-force throttle, user CRUD, cookies for shared cross-service ops session. |
| `app.py` | B2B data API route registration (`/api/v1/data/*`), `_check_api_token()`, `_api_v1_*` endpoints, Stripe webhook provisioning for `api_data_tokens`. |
| `config.py` | Auth/env vars: `ADMIN_LOGIN_*`, `SECRET_KEY`, session cookie flags, recaptcha, Stripe price IDs, etc. |

> *Note:* `services/api/auth_helpers.py` does **not** exist in the current tree; helpers live in `services/api/auth.py`.

## Auth Flows

### 1. Public User (Cookie) Auth

- Public users are stored in `public_users`.
- Registration/login forms in `blueprints/auth.py` use `flask.session['public_user_id']`.
- `blueprints/api.py` exposes `/_current_public_user()` to resolve the session-based user for `/api/me/*` routes.
- CSRF: `api_bp.before_request` enforces `X-CSRF-Token` against `session['_csrf_token']` for cookie-auth `POST/PUT/PATCH/DELETE` calls to `/api/me/*`. API-key auth bypasses CSRF.

### 2. Public API Key Auth (`api_clients` system)

- Used by `blueprints/api.py` routes.
- Bearer token header: `Authorization: Bearer <token>` or `?api_key=<key>` fallback.
- Token is hashed with SHA-256; only the hash is stored in `api_clients.key_hash`. Plaintext is returned once at creation.
- Tiers: `free` (default), `pro`, `enterprise`.
- Rate limits per tier: defined in `services/api/auth.py` `DEFAULT_TIERS` and overridable via `config.MB_API_TIERS`.
- Default quota: `free` = 100 req/day, `pro` = 1,000 req/day, `enterprise` = 100,000 req/day.
- Anonymous requests allowed where `allow_anonymous=True`; rate-limited per IP hash with default usage of `free` tier unless `anonymous_quota` is supplied.
- `after_api_request()` logs every `/api/*` request to `api_request_logs` and injects `X-RateLimit-*` headers.

### 3. B2B Data API Token Auth (`api_data_tokens` system)

- Used by `/api/v1/data/*` and `/api/v1/disposition/lookup` in `app.py`.
- Separate table managed in `init_db.py`: `api_data_tokens` (plus per-minute usage counter `api_data_token_hits` and delivery audit `api_token_deliveries`).
- Tokens are SHA-256 hashed; plaintext is emailed to the subscriber. `api_token_deliveries` stores the plaintext only long enough for email follow-up/audit.
- Rate limit is per minute (`rate_limit_per_minute`, default 30 for disposition tier).
- Provisioned by Stripe webhook `_apply_disposition_api_stripe_event()` with `tier='disposition'`.

### 4. Admin Auth (+ cross-service ops session)

- Admin users live in `users` table with `role` in `ADMIN_ACCESS_ROLES` and `is_active=1`.
- Admin login: `blueprints/admin/security.py` → `admin_login()`.
- On success, Flask-Login logs the user in, sets `session['_csrf_token']`, and issues two domain cookies for cross-service login:
  - `blotter_ops_session` — HS256 JWT signed with `OPS_SESSION_SECRET`, 8-hour TTL, contains `sub` (username) and `role`.
  - `studio_access` — raw token from `STUDIO_ACCESS_TOKEN` for Claw3D Office.
- Logout (`admin_logout()`) clears Flask session and those cookies.
- Brute-force protection is detailed below.

## Admin Login / MFA Brute-Force Throttle

- Failed attempts are recorded in `auth_login_attempts` (`username`, `ip_address`, `success`, `created_at`).
- Config knobs:
  - `admin_login_max_attempts` (default 5)
  - `admin_login_window_minutes` (default 15)
  - `admin_login_lockout_minutes` (default 15)
- Logic in `blueprints/admin/security.py::_login_rate_limited()` counts failures from either matching `username` or `ip_address` inside the window. When attempts ≥ max, the IP/user is locked out until `last_failure + lockout_minutes`.
- `_record_login_attempt()` writes a row on every POST regardless of outcome.
- MFA columns (`mfa_secret`, `mfa_enabled`) exist on `users`, but TOTP enforcement is not wired into `admin_login()` in the current code.

## JWT Ops Session for Cross-Service Admin Access

Implemented in `blueprints/admin/security.py`:

- Cookies set for `.montanablotter.com` so Blotter Host and Claw3D Office share authentication.
- `_sign_jwt()` / `_b64url_encode/decode()` implement HS256 signed JWTs without external libraries.
- Payload: `{ sub: username, role: role, iat, exp }`, 8 hours.
- Cookie flags: `HttpOnly`, `SameSite=Strict`, `Secure` in production.
- `_set_shared_admin_cookies()` issues cookies after login; `_clear_shared_admin_cookies()` clears them on logout.

## Key Config Variables

| Var | Purpose |
|-----|---------|
| `MB_SECRET_KEY` / `SECRET_KEY` | Flask session signing; required in production. |
| `MB_API_ADMIN_SECRET` | (Optional) admin-secret override for legacy API admin checks. |
| `MB_API_TIERS` | Override public API tier quotas. |
| `MB_ADMIN_LOGIN_MAX_ATTEMPTS` | Admin brute-force threshold (default 5). |
| `MB_ADMIN_LOGIN_WINDOW_MINUTES` | Window for counting failures (default 15). |
| `MB_ADMIN_LOGIN_LOCKOUT_MINUTES` | Lockout duration after threshold (default 15). |
| `OPS_SESSION_SECRET` | JWT signing secret for the cross-service `blotter_ops_session` cookie. |
| `STUDIO_ACCESS_TOKEN` | Token issued as `studio_access` cookie for Claw3D Office. |
| `MB_SESSION_COOKIE_SECURE`, `MB_SESSION_COOKIE_SAMESITE` | Cookie flags. |
| `MB_RECAPTCHA_SITE_KEY`, `MB_RECAPTCHA_SECRET_KEY` | Public user auth reCAPTCHA v3. |
| Stripe vars (`MB_STRIPE_*`, `STRIPE_DISPOSITION_API_PRICE_ID`, etc.) | Provision B2B data API tokens on subscription. |

## Public API Endpoints

### Modern endpoints (`blueprints/api.py`)

| Method | Route | Auth |
|--------|-------|------|
| GET/POST/PUT/DELETE | `/api/me/alert-profiles` | Public user cookie or API key |
| GET | `/api/geo/incidents` | Anonymous (generous geo quota) |
| GET | `/api/geo/heatmap` | Anonymous (generous geo quota) |
| GET | `/api/scorecards` | Anonymous |
| GET | `/api/scorecards/<area_type>/<area_slug>` | Anonymous |
| GET | `/api/posts` | Anonymous |

Geo quota: `2000 req / 24h` per IP.

### Legacy standalone endpoints (`services/api/routes.py`)

| Method | Route | Notes |
|--------|-------|-------|
| GET | `/api/stats` | Counts of records, blotters, bookings, posts, counties, incident types. |
| GET | `/api/records` | Paginated/filterable records. |
| GET | `/api/records/recent` | 50 most recent records. |
| GET | `/api/fwp-violations` | Records where `officer='MT FWP'`. |
| GET | `/api/filters` | Distinct counties and incident types. |
| GET | `/api/timeline` | Daily counts for last 30 days. |

### B2B data endpoints (`app.py`)

| Method | Route | Auth |
|--------|-------|------|
| GET | `/api/v1/data/records` | `api_data_tokens` Bearer |
| GET | `/api/v1/data/warrants` | `api_data_tokens` Bearer |
| GET | `/api/v1/data/jail-bookings` | `api_data_tokens` Bearer |
| GET | `/api/v1/data/posts` | `api_data_tokens` Bearer |
| GET | `/api/v1/data/summary` | `api_data_tokens` Bearer |
| GET/POST | `/api/v1/disposition/lookup` | `api_data_tokens` Bearer |

## Important Tables

- `public_users` — Site account records.
- `api_clients` — Public API key system: hashed tokens, tier, active/revoked state (`services/api/auth.py`).
- `api_request_logs` — All `/api/*` request logging for rate-limit enforcement and observability.
- `api_data_tokens` — B2B paid data tokens (`app.py`).
- `api_data_token_hits` — Per-minute usage counter for B2B tokens.
- `api_token_deliveries` — Plaintext token + email delivery audit (plaintext erased from API after email).
- `auth_login_attempts` — Admin login brute-force log.
- `users` — Admin users (role, MFA columns, active flag).

## Gotchas

- **Two separate auth code paths**: `blueprints/api.py` uses `services/api/auth.py`; B2B data paths in `app.py` use their own `_hash_api_token()` and `api_data_tokens`. Quotas, tables, and admin UIs are independent.
- **No `services/api/auth_helpers.py`**: the task description references it, but the file does not exist.
- **API-key auth bypasses CSRF** for `/api/me/*`; cookie-only requests need a matching `X-CSRF-Token`.
- **Anonymous quotas are IP-based** and trust reverse-proxy headers (`CF-Connecting-IP`, `X-Forwarded-For`). Ensure the edge proxy sanitizes these.
- **B2B tokens are one-way hashed**; lost tokens must be regenerated via Stripe/webhook or admin re-provisioning.
- **Admin MFA columns exist but are not enforced**: `mfa_secret`/`mfa_enabled` are created during migration, but `admin_login()` does not prompt for a TOTP code.
- **Rate-limit headers are injected late**: `inject_rate_headers()` runs in an `after_request` hook reading from `g._api_rate_headers`; any exception before the decorator runs may not return headers.
- **Legacy `/api/routes.py` not auto-registered**: `register_api_routes(app)` is defined but not invoked by the main app; verify which route tree is active in your deployment.
- **Admin lockout is by username OR IP**: a shared public IP or VPN can cause cross-user lockouts.

