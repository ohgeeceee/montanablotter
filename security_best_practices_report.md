# Montana Blotter Security Review
Date: 2026-03-05

## Executive Summary
I reviewed the Flask web app, templates, deployment unit, and supporting ingestion/auth scripts.

Findings summary:
- Critical: 1
- High: 5
- Medium: 4

Top priorities:
1. Remove and rotate all secrets currently stored in `config.py`.
2. Fix stored/DOM XSS paths (`record_detail`, blog markdown rendering, homepage ticker).
3. Add CSRF protection to all state-changing admin routes.
4. Run the web service as a non-root account.

---

## Critical

### MB-SEC-001: Secrets stored in plaintext application config
- Rule ID: FLASK-CONFIG-001
- Severity: Critical
- Location:
  - `/root/montanablotter/config.py:13`
  - `/root/montanablotter/config.py:19-20`
  - `/root/montanablotter/config.py:27-28`
  - `/root/montanablotter/config.py:55`
  - `/root/montanablotter/app.py:30`
- Evidence: `SECRET_KEY`, email/SMTP credentials, and API key are present in source config and consumed directly as runtime secrets.
- Impact: A read of this file allows session forgery, outbound email abuse, and third-party API abuse immediately.
- Fix:
  - Move secrets to environment variables or a secrets manager.
  - Fail startup if required secrets are missing.
  - Rotate all exposed credentials and keys immediately.
- Mitigation:
  - Restrict file permissions (`600`) and ownership to the app user.
  - Add secret scanning in CI and pre-commit.
- False positive notes:
  - Even if `config.py` is gitignored, plaintext on disk is still a production risk.

---

## High

### MB-SEC-002: Stored XSS in incident narrative rendering
- Rule ID: JS-XSS-001 / Flask template output encoding
- Severity: High
- Location:
  - `/root/montanablotter/templates/record_detail.html:66`
  - `/root/montanablotter/email_worker.py:50-95`
- Evidence:
  - Narrative is rendered with `|safe` after newline replacement.
  - Ingestion accepts broad email content heuristics from external senders.
- Impact: Malicious HTML/script in ingested incident content can execute in visitors' browsers (session theft/defacement/phishing).
- Fix:
  - Do not render raw details with `|safe`.
  - Use escaped rendering (`|e`) and CSS `white-space: pre-line`.
- Mitigation:
  - Add server-side sanitization/validation at ingestion time.
- False positive notes:
  - Risk depends on whether untrusted parties can get content ingested; current heuristics are broad.

### MB-SEC-003: DOM XSS in homepage live ticker
- Rule ID: JS-XSS-001
- Severity: High
- Location:
  - `/root/montanablotter/templates/index.html:1238-1242`
  - `/root/montanablotter/templates/index.html:1255`
- Evidence: Unescaped `title/meta` are string-concatenated into HTML and assigned via `innerHTML`.
- Impact: If any post title/meta contains HTML payloads, script executes for homepage users.
- Fix:
  - Build ticker elements with `createElement` + `textContent`.
  - Or strictly escape all interpolated values before HTML insertion.
- Mitigation:
  - Add CSP as defense-in-depth.
- False positive notes:
  - Becomes exploitable whenever post text is attacker-controlled or unsafely transformed upstream.

### MB-SEC-004: Stored XSS risk in blog markdown rendering
- Rule ID: JS-XSS-001 / content sanitization
- Severity: High
- Location:
  - `/root/montanablotter/app.py:1840-1843`
  - `/root/montanablotter/templates/blog_post.html:62`
- Evidence:
  - Markdown conversion output is marked `|safe`.
  - Python-Markdown default behavior allows embedded HTML unless separately sanitized.
- Impact: Malicious HTML in blog bodies can execute in readers' browsers.
- Fix:
  - Sanitize rendered HTML with an allowlist sanitizer (e.g., `bleach.clean`) before output.
  - Restrict allowed tags/attributes/protocols.
- Mitigation:
  - Add editorial validation and sanitization tests.
- False positive notes:
  - If only fully trusted admins write posts, risk is reduced but still dangerous for account takeover scenarios.

### MB-SEC-005: Missing CSRF protection on privileged admin actions
- Rule ID: FLASK-CSRF-001
- Severity: High
- Location:
  - Admin state-changing routes:
    - `/root/montanablotter/app.py:3116-3124`
    - `/root/montanablotter/app.py:3674-3690`
    - `/root/montanablotter/app.py:3727-3739`
    - `/root/montanablotter/app.py:3749-3879`
    - `/root/montanablotter/app.py:3957-3976`
    - `/root/montanablotter/app.py:3978-4139`
  - Example forms/fetch without CSRF token:
    - `/root/montanablotter/templates/admin_upload.html:63`
    - `/root/montanablotter/templates/admin_facebook.html:72`
    - `/root/montanablotter/templates/admin_emails.html:65`
    - `/root/montanablotter/templates/admin_blotters.html:214-218`
  - No CSRF config/token usage found in app/templates/deps.
- Impact: Authenticated admin browsers can be coerced into unwanted state changes (deletes, status changes, outbound sends, settings changes).
- Fix:
  - Enable `CSRFProtect` for Flask.
  - Add CSRF tokens to all forms and AJAX requests.
  - Add Origin/Referer checks for sensitive POST/JSON endpoints.
- Mitigation:
  - Keep `SameSite=Lax` (or stricter) and re-auth for sensitive operations.
- False positive notes:
  - Browser cookie policies reduce but do not eliminate CSRF classes in real deployments.

### MB-SEC-006: Web service runs as root
- Rule ID: PRIV-LEAST-001
- Severity: High
- Location:
  - `/root/montanablotter/montanablotter.service:6`
- Evidence: `User=root` for Gunicorn service.
- Impact: Any RCE in app/dependency chain becomes full-root system compromise.
- Fix:
  - Create dedicated unprivileged user/group (e.g., `montanablotter`).
  - Run service under that account with least-privilege filesystem permissions.
- Mitigation:
  - Systemd hardening (`NoNewPrivileges=true`, `ProtectSystem=strict`, etc.).
- False positive notes:
  - Even with network filtering, root runtime remains high-risk.

---

## Medium

### MB-SEC-007: No brute-force/rate limiting on admin login
- Rule ID: AUTH-BRUTEFORCE-001
- Severity: Medium
- Location:
  - `/root/montanablotter/app.py:3364-3381`
- Evidence: Password checks occur with no IP/account throttling, lockout, or progressive delay.
- Impact: Increased risk of online credential stuffing/brute-force against admin login.
- Fix:
  - Add `Flask-Limiter` on `/admin/login` (per-IP and per-username dimensions).
  - Add temporary lockout/backoff after repeated failures.
- Mitigation:
  - Enforce MFA for admin accounts.
- False positive notes:
  - Edge/WAF may provide some throttling; not visible in app code.

### MB-SEC-008: Public API discloses internal file paths; uploads served publicly
- Rule ID: INFOLEAK-001
- Severity: Medium
- Location:
  - `/root/montanablotter/app.py:4406-4407`
  - `/root/montanablotter/app.py:4426-4427`
  - `/root/montanablotter/app.py:4686-4689`
- Evidence:
  - `api_blotters`/`api_blotter` include `file_path` in responses.
  - `/uploads/<path:filename>` serves files directly from upload storage.
- Impact: Reveals server filesystem structure and may expose raw source documents beyond intended audience.
- Fix:
  - Remove `file_path` from public API responses.
  - Gate raw file access behind admin auth or signed short-lived URLs.
- Mitigation:
  - Separate private storage from public web root.
- False positive notes:
  - If raw PDFs are intentionally public, still avoid leaking absolute filesystem paths.

### MB-SEC-009: Weak security baseline headers/cookie hardening not explicit
- Rule ID: FLASK-SESS-001 / HEADER-BASELINE-001
- Severity: Medium
- Location:
  - `/root/montanablotter/app.py` and `/root/montanablotter/config.py` (no explicit session cookie or security header settings found)
  - `/root/montanablotter/templates/public_page_base.html:6-7` (third-party CDN assets, no SRI)
- Evidence:
  - No explicit `SESSION_COOKIE_SECURE/HTTPONLY/SAMESITE` policy configuration.
  - No app-level security headers (`CSP`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`) found.
- Impact: Larger blast radius for XSS/clickjacking/mixed-client risks and weaker production hardening guarantees.
- Fix:
  - Set explicit cookie policy for production.
  - Add baseline response headers (or verify at nginx/CDN layer).
  - Use SRI or self-host critical third-party JS/CSS.
- Mitigation:
  - Deploy strict CSP with nonces/hashes.
- False positive notes:
  - Headers may exist in nginx/CDN config not reviewed here.

### MB-SEC-010: Insecure default admin bootstrap credentials in helper scripts
- Rule ID: AUTH-BOOTSTRAP-001
- Severity: Medium
- Location:
  - `/root/montanablotter/seed_admin.py:15`
  - `/root/montanablotter/seed_admin.py:47-50`
  - `/root/montanablotter/setup.py:98-101`
- Evidence:
  - Default admin password is hardcoded and printed by setup scripts.
- Impact: Fresh or reset deployments can be compromised quickly if defaults are used or logs are exposed.
- Fix:
  - Require interactive one-time password input or env-provided random bootstrap secret.
  - Refuse to run with known default credentials.
- Mitigation:
  - Force password rotation at first login.
- False positive notes:
  - Lower risk if operators always pass custom credentials and scripts are never run unattended.

---

## Suggested Fix Order
1. MB-SEC-001 (secret rotation + secret management)
2. MB-SEC-002/003/004 (XSS fixes)
3. MB-SEC-005 (CSRF protection)
4. MB-SEC-006 (run service as non-root)
5. MB-SEC-007/008/009/010 (hardening)
