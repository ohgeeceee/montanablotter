# Phase 4: REST API (`/api/v1/lea/...`) (Week 2, Day 4–5)

**Objective:** Build programmatic access for agencies to submit incidents and roster data via HTTP.

**Prerequisites:** Phase 1 ✓ (schema), Phase 2 ✓ (JWT auth)

**Tech Stack:**
- Flask blueprints (`/api/v1/lea/`)
- Bearer token authentication (JWT)
- JSON request/response
- Rate limiting (1000 req/hour per agency)
- Error responses (400, 401, 429, etc.)

**Files to Create/Modify:**
- `blueprints/api_lea.py` — REST API routes (new blueprint)
- `services/api/lea_auth.py` — Bearer token middleware
- `services/api/rate_limiter.py` — Rate limiting logic
- `tests/test_api_lea.py` — Full API test suite with mocked requests

---

## Task 4.1: `/api/v1/lea/auth/token` — Token Endpoint

**Objective:** Exchange credentials for JWT token (username/password or refresh token).

### Step 1: Write Failing Test

```python
# tests/test_api_lea.py
import os, sqlite3, tempfile, unittest, json
from flask import Flask
import app as app_module
import config, init_db
from services.lea_auth import user_auth

class TestLEARestAPI(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-api-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        
        conn = sqlite3.connect(self.db_path)
        init_db.init_database(conn)
        init_db.ensure_lea_schema(conn)
        self.conn = conn
        
        # Create Flask test client
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()
        
        # Insert test agency and user
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email, verification_status) VALUES (?, ?, ?, ?, ?, ?)",
            ("Test Sheriff", "sheriff", "test", "Test", "sheriff@testco.gov", "verified")
        )
        self.agency_id = cursor.lastrowid
        
        pwd_hash = user_auth.hash_password("ApiKey123!")
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "apiuser", "api@testco.gov", "API User", pwd_hash, "pio")
        )
        self.user_id = cursor.lastrowid
        conn.commit()
    
    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
    
    def test_post_token_with_username_password(self) -> None:
        """Test token generation with username/password (Resource Owner Password Flow)."""
        response = self.client.post('/api/v1/lea/auth/token', 
            json={
                'grant_type': 'password',
                'username': 'apiuser',
                'password': 'ApiKey123!'
            },
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('access_token', data)
        self.assertEqual(data['token_type'], 'Bearer')
        self.assertIn('expires_in', data)
    
    def test_post_token_invalid_credentials(self) -> None:
        """Test token request with wrong password."""
        response = self.client.post('/api/v1/lea/auth/token',
            json={
                'grant_type': 'password',
                'username': 'apiuser',
                'password': 'WrongPassword'
            },
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 401)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'invalid_credentials')
```

### Step 2–5: Implementation

Create `blueprints/api_lea.py`:

```python
"""LEA REST API — token auth, incident publishing, roster sync."""
from flask import Blueprint, request, jsonify, current_app
import sqlite3, json, logging
from datetime import datetime, timezone
from services.lea_auth import api_tokens, user_auth
from services.api import rate_limiter

api_lea = Blueprint('api_lea', __name__, url_prefix='/api/v1/lea')
logger = logging.getLogger(__name__)


@api_lea.route('/auth/token', methods=['POST'])
def token():
    """Exchange credentials for JWT access token.
    
    Request (JSON):
        grant_type: "password" | "refresh_token"
        username: username (if grant_type=password)
        password: password (if grant_type=password)
        refresh_token: token (if grant_type=refresh_token)
    
    Response (200):
        {
            "access_token": "eyJ...",
            "token_type": "Bearer",
            "expires_in": 2592000,
            "refresh_token": "eyJ..."
        }
    
    Error (401):
        { "error": "invalid_credentials" | "invalid_refresh_token" }
    """
    data = request.get_json() or {}
    grant_type = data.get('grant_type', '').lower()
    
    if grant_type == 'password':
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        conn = sqlite3.connect(current_app.config['DATABASE'])
        row = conn.execute(
            'SELECT id, agency_id, password_hash FROM lea_users WHERE username = ? AND is_active = 1',
            (username,)
        ).fetchone()
        conn.close()
        
        if not row or not user_auth.verify_password(password, row[2]):
            return jsonify({'error': 'invalid_credentials'}), 401
        
        user_id, agency_id, _ = row
        
        # Generate tokens
        access_token = api_tokens.generate_token(
            agency_id=agency_id,
            user_id=user_id,
            expires_in_seconds=30 * 86400  # 30 days
        )
        
        refresh_token = api_tokens.generate_token(
            agency_id=agency_id,
            user_id=user_id,
            expires_in_seconds=90 * 86400  # 90 days
        )
        
        logger.info(f"Token issued for user {user_id} (agency {agency_id})")
        
        return jsonify({
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_in': 30 * 86400,
            'refresh_token': refresh_token
        }), 200
    
    elif grant_type == 'refresh_token':
        refresh_token = data.get('refresh_token', '')
        payload = api_tokens.validate_token(refresh_token)
        
        if not payload:
            return jsonify({'error': 'invalid_refresh_token'}), 401
        
        # Issue new access token
        access_token = api_tokens.generate_token(
            agency_id=payload['agency_id'],
            user_id=payload['user_id'],
            expires_in_seconds=30 * 86400
        )
        
        return jsonify({
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_in': 30 * 86400
        }), 200
    
    else:
        return jsonify({'error': 'unsupported_grant_type'}), 400


def bearer_token_required(f):
    """Decorator: require valid Bearer token in Authorization header."""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'missing_authorization'}), 401
        
        token = auth_header[7:]  # Strip "Bearer "
        payload = api_tokens.validate_token(token)
        
        if not payload:
            return jsonify({'error': 'invalid_token'}), 401
        
        # Check rate limit
        agency_id = payload['agency_id']
        if not rate_limiter.check_rate_limit(agency_id):
            return jsonify({'error': 'rate_limit_exceeded', 'retry_after': 60}), 429
        
        # Attach payload to request for use in handlers
        request.lea_payload = payload
        return f(*args, **kwargs)
    
    return decorated_function
```

Create `services/api/rate_limiter.py`:

```python
"""API rate limiting per agency."""
from datetime import datetime, timedelta, timezone
import sqlite3
from flask import current_app

RATE_LIMIT = 1000  # requests per hour


def check_rate_limit(agency_id: int) -> bool:
    """Check if agency is within rate limit.
    
    Args:
        agency_id: Agency ID
        
    Returns:
        True if request allowed, False if rate limit exceeded
    """
    try:
        conn = sqlite3.connect(current_app.config['DATABASE'])
        
        # Count requests in last hour
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        count = conn.execute(
            'SELECT COUNT(*) FROM lea_audit_log WHERE agency_id = ? AND action LIKE ? AND created_at > ?',
            (agency_id, 'api_%', one_hour_ago)
        ).fetchone()[0]
        
        conn.close()
        return count < RATE_LIMIT
    except Exception:
        # On error, allow request (fail open)
        return True
```

### Commit

```bash
git add blueprints/api_lea.py services/api/rate_limiter.py tests/test_api_lea.py
git commit -m "feat(lea): add /api/v1/lea/auth/token endpoint with rate limiting"
```

---

## Task 4.2: `/api/v1/lea/blotter/publish` — Single Incident Publish

**Objective:** Submit one incident via API, save as draft.

### Implementation Outline

```python
@api_lea.route('/blotter/publish', methods=['POST'])
@bearer_token_required
def publish_incident():
    """
    Submit a single incident for publication.
    
    Request (JSON):
        {
            "incident_date": "2026-08-02",
            "incident_time": "14:30",
            "cad_number": "2026-1234",
            "location": "300 BLK MAIN ST",
            "charges": ["45-5-202", "45-5-206"],
            "narrative": "Officers responded to...",
            "suspect_name": "John Doe" (optional),
            "suspect_dob": "1985-06-15" (optional)
        }
    
    Response (201):
        {
            "draft_id": 12345,
            "status": "draft",
            "created_at": "2026-08-02T14:30:00Z",
            "review_url": "/lea/submission/12345"
        }
    """
    # Validate request
    # Save to lea_blotter_drafts
    # Return response
```

**Tests:**
- Valid incident saves
- Missing required fields → 400
- Invalid MCA codes → 400
- Rate limiting works

---

## Task 4.3: `/api/v1/lea/blotter/batch` — Batch Upload

**Objective:** Submit multiple incidents in one request (CSV/JSON file or inline array).

### Implementation Outline

```python
@api_lea.route('/blotter/batch', methods=['POST'])
@bearer_token_required
def batch_publish():
    """
    Submit multiple incidents in one batch.
    
    Request (multipart form):
        file: CSV or JSON file
        OR inline JSON:
        {
            "incidents": [
                { "incident_date": "...", ... },
                { "incident_date": "...", ... }
            ]
        }
    
    Response (202 Accepted):
        {
            "batch_id": "batch_2026_08_02_001",
            "status": "processing",
            "records_queued": 15,
            "status_url": "/api/v1/lea/blotter/batch/batch_2026_08_02_001/status"
        }
    
    Poll status_url for results.
    """
```

**Tests:**
- CSV parsing
- JSON parsing
- Batch insert
- Status polling

---

## Task 4.4: `/api/v1/lea/roster/sync` — Jail Roster Sync

**Objective:** Submit jail roster snapshots (incremental or full).

### Implementation Outline

```python
@api_lea.route('/roster/sync', methods=['POST'])
@bearer_token_required
def sync_roster():
    """
    Submit jail roster snapshot.
    
    Request (JSON):
        {
            "sync_type": "full" | "incremental",
            "facility_name": "Test County Detention",
            "snapshot_date": "2026-08-02T14:30:00Z",
            "inmates": [
                {
                    "booking_number": "2026-12345",
                    "full_name": "John Doe",
                    "dob": "1985-06-15",
                    "booking_date": "2026-08-01",
                    "release_date": null,
                    "charges": ["45-5-202"],
                    "bond_amount": 5000.00
                }
            ]
        }
    
    Response (202 Accepted):
        {
            "sync_id": "sync_2026_08_02_001",
            "status": "processing",
            "records_received": 45,
            "status_url": "/api/v1/lea/roster/sync/sync_2026_08_02_001/status"
        }
    """
```

**Tests:**
- Full roster import
- Incremental updates
- Dedup logic (same booking number)
- Status polling

---

## Task 4.5: Rate Limiting & Token Middleware

**Objective:** Ensure all endpoints check Bearer token and enforce rate limits.

**Implementation:** Decorator pattern (already started in 4.1).

**Tests:**
- Valid token passes
- Expired token rejected
- Missing token rejected
- Rate limit enforced (>1000 req/hour → 429)

---

## Task 4.6: CORS & Security Headers

**Objective:** Configure Cross-Origin Resource Sharing and security headers.

### Implementation Outline

```python
@api_lea.before_request
def set_security_headers():
    """Add security headers to all API responses."""
    # No CORS by default (same-origin only, or whitelist agency domains)
    # Add: X-Content-Type-Options, X-Frame-Options, etc.
```

**Tests:**
- CORS headers present
- Content-Type enforced (JSON)
- XSS/clickjacking protection

---

## Task 4.7: Error Responses & Logging

**Objective:** Standardized error format, comprehensive logging.

### Implementation Outline

```python
def api_error(message: str, code: int, error_type: str = 'error'):
    """Standardized API error response."""
    return jsonify({
        'error': error_type,
        'message': message,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }), code
```

**All endpoints return:**
```json
{
    "error": "error_type",
    "message": "Human-readable message",
    "timestamp": "2026-08-02T14:30:00Z"
}
```

**Logging:**
- All requests logged (method, path, agency_id, user_id, status, latency)
- Errors logged with full context
- Rate limit hits logged

**Tests:**
- Error format consistent
- Logs contain expected fields

---

## Task 4.8: Full API Test Suite

**Objective:** Comprehensive test coverage for all endpoints.

**Test Scenarios:**
- ✅ Token auth (password, refresh)
- ✅ Single incident publish
- ✅ Batch upload (CSV, JSON)
- ✅ Roster sync
- ✅ Rate limiting
- ✅ Error handling (400, 401, 429, 500)
- ✅ Audit logging

**Target:** 95%+ code coverage for `blueprints/api_lea.py`

---

## Ready for Phase 4 Dispatch

Once Phase 2 finishes, the **API Engineer subagent** will receive:
- All 8 task specs
- Test templates (copy-paste ready)
- JSON request/response examples
- Error handling patterns
- Rate limiter logic

Each task: **TEST FAIL → IMPLEMENT → TEST PASS → COMMIT**
