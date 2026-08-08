# Phase 2: Authentication & User Management (Week 2, Day 1)

**Objective:** Implement bcrypt password hashing, JWT API tokens, ORI verification, and invitation workflow.

**Prerequisites:** Phase 1 ✓ (all 8 tables exist and indexed)

**Tech Stack:**
- `bcrypt` — password hashing (5.0+)
- `PyJWT` — JWT token generation/validation (2.8+)
- Flask-Login — session management (existing)
- `email-validator` — email validation

**Files to Create/Modify:**
- `services/lea_auth/user_auth.py` — bcrypt hashing + password verify
- `services/lea_auth/api_tokens.py` — JWT generation + validation
- `services/lea_auth/agency_verification.py` — ORI lookup + email domain check
- `services/lea_auth/invitations.py` — invite creation, acceptance, expiry
- `blueprints/lea_auth.py` — login/logout routes (new blueprint)
- `tests/test_lea_auth.py` — full auth test suite

---

## Task 2.1: Bcrypt Password Hashing & Verification

**Objective:** Create `services/lea_auth/user_auth.py` with secure password hashing.

### Step 1: Write Failing Test

```python
# tests/test_lea_auth.py
import os, sqlite3, tempfile, unittest, bcrypt
import app as app_module
import config
import init_db
from services.lea_auth import user_auth

class TestLEAAuth(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-auth-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        
        conn = sqlite3.connect(self.db_path)
        init_db.init_database(conn)
        init_db.ensure_lea_schema(conn)
        self.conn = conn

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_hash_password(self) -> None:
        """Test bcrypt hashing produces different hashes for same password."""
        pwd = "SecurePassword123!"
        hash1 = user_auth.hash_password(pwd)
        hash2 = user_auth.hash_password(pwd)
        # Same password, different hashes (salt is random)
        self.assertNotEqual(hash1, hash2)
        # Both verify correctly
        self.assertTrue(user_auth.verify_password(pwd, hash1))
        self.assertTrue(user_auth.verify_password(pwd, hash2))

    def test_verify_password_correct(self) -> None:
        """Test password verification succeeds with correct password."""
        pwd = "SecurePassword123!"
        pwd_hash = user_auth.hash_password(pwd)
        self.assertTrue(user_auth.verify_password(pwd, pwd_hash))

    def test_verify_password_incorrect(self) -> None:
        """Test password verification fails with wrong password."""
        pwd = "SecurePassword123!"
        pwd_hash = user_auth.hash_password(pwd)
        self.assertFalse(user_auth.verify_password("WrongPassword", pwd_hash))

    def test_hash_empty_password_rejected(self) -> None:
        """Test empty password raises ValueError."""
        with self.assertRaises(ValueError):
            user_auth.hash_password("")
```

### Step 2: Run Test to Verify Failure

```bash
cd /root/montanablotter
./venv/bin/python3 -m pytest tests/test_lea_auth.py::TestLEAAuth::test_hash_password -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'services.lea_auth'`

### Step 3: Write Minimal Implementation

Create `services/lea_auth/__init__.py` (empty init file).

Create `services/lea_auth/user_auth.py`:

```python
"""LEA user authentication: password hashing and verification."""
import bcrypt

def hash_password(password: str) -> str:
    """Hash a password using bcrypt.
    
    Args:
        password: plaintext password
        
    Returns:
        bcrypt hashed password (str)
        
    Raises:
        ValueError: if password is empty
    """
    if not password or not password.strip():
        raise ValueError("Password cannot be empty")
    
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.
    
    Args:
        password: plaintext password to check
        password_hash: bcrypt hash from database
        
    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        # Invalid hash format, etc.
        return False
```

### Step 4: Run Test to Verify Pass

```bash
cd /root/montanablotter
./venv/bin/python3 -m pytest tests/test_lea_auth.py::TestLEAAuth -v
```

Expected: PASS (all 4 tests)

### Step 5: Commit

```bash
cd /root/montanablotter
git add services/lea_auth/__init__.py services/lea_auth/user_auth.py tests/test_lea_auth.py
git commit -m "feat(lea): add bcrypt password hashing and verification"
```

---

## Task 2.2: JWT API Token Generation & Expiry

**Objective:** Create `services/lea_auth/api_tokens.py` for JWT token lifecycle.

### Step 1: Write Failing Test

Add to `tests/test_lea_auth.py`:

```python
def test_generate_api_token(self) -> None:
    """Test JWT token generation with expiry."""
    from services.lea_auth import api_tokens
    import time
    
    agency_id = 1
    user_id = 1
    token = api_tokens.generate_token(agency_id=agency_id, user_id=user_id)
    
    # Token should be a non-empty string
    self.assertIsInstance(token, str)
    self.assertGreater(len(token), 20)

def test_validate_api_token(self) -> None:
    """Test JWT token validation."""
    from services.lea_auth import api_tokens
    
    agency_id = 1
    user_id = 1
    token = api_tokens.generate_token(agency_id=agency_id, user_id=user_id)
    
    # Decode and verify
    payload = api_tokens.validate_token(token)
    self.assertIsNotNone(payload)
    self.assertEqual(payload['agency_id'], agency_id)
    self.assertEqual(payload['user_id'], user_id)

def test_validate_expired_token(self) -> None:
    """Test expired token validation fails."""
    from services.lea_auth import api_tokens
    from unittest.mock import patch
    import time
    
    token = api_tokens.generate_token(agency_id=1, user_id=1, expires_in_seconds=1)
    
    # Wait for expiry
    time.sleep(2)
    
    # Validation should fail
    payload = api_tokens.validate_token(token)
    self.assertIsNone(payload)

def test_validate_malformed_token(self) -> None:
    """Test malformed token returns None."""
    from services.lea_auth import api_tokens
    
    payload = api_tokens.validate_token("invalid.token.format")
    self.assertIsNone(payload)
```

### Step 2: Run Test to Verify Failure

```bash
./venv/bin/python3 -m pytest tests/test_lea_auth.py::TestLEAAuth::test_generate_api_token -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'services.lea_auth.api_tokens'`

### Step 3: Write Minimal Implementation

Create `services/lea_auth/api_tokens.py`:

```python
"""LEA API token generation and validation."""
import jwt
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

# Get JWT secret from environment; fallback to dev key
JWT_SECRET = os.getenv('LEA_JWT_SECRET', 'dev-lea-jwt-secret-change-in-prod')
JWT_ALGORITHM = 'HS256'
DEFAULT_TOKEN_EXPIRY_DAYS = 30


def generate_token(
    agency_id: int,
    user_id: int,
    expires_in_seconds: Optional[int] = None
) -> str:
    """Generate a JWT API token.
    
    Args:
        agency_id: ID of the agency
        user_id: ID of the user
        expires_in_seconds: Token lifetime in seconds (default: 30 days)
        
    Returns:
        JWT token string
    """
    if expires_in_seconds is None:
        expires_in_seconds = DEFAULT_TOKEN_EXPIRY_DAYS * 86400
    
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(seconds=expires_in_seconds)
    
    payload = {
        'agency_id': agency_id,
        'user_id': user_id,
        'iat': now,
        'exp': expiry,
    }
    
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


def validate_token(token: str) -> Optional[Dict[str, Any]]:
    """Validate and decode a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        Payload dict if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        # Token expired
        return None
    except (jwt.InvalidTokenError, jwt.DecodeError, Exception):
        # Malformed, invalid signature, etc.
        return None
```

### Step 4: Run Test to Verify Pass

```bash
./venv/bin/python3 -m pytest tests/test_lea_auth.py::TestLEAAuth -v
```

Expected: PASS (all tests including new JWT tests)

### Step 5: Commit

```bash
cd /root/montanablotter
git add services/lea_auth/api_tokens.py tests/test_lea_auth.py
git commit -m "feat(lea): add JWT API token generation and validation"
```

---

## Task 2.3: ORI Verification & Agency Email Domain Check

**Objective:** Create `services/lea_auth/agency_verification.py` to validate agency identity.

### Step 1: Write Failing Test

Add to `tests/test_lea_auth.py`:

```python
def test_verify_agency_by_ori(self) -> None:
    """Test ORI lookup against known Montana agencies."""
    from services.lea_auth import agency_verification
    
    # Great Falls Police Department ORI
    result = agency_verification.verify_agency_by_ori("MTZ001")
    # Should return agency info or confirmation
    self.assertIsNotNone(result)

def test_verify_government_email_domain(self) -> None:
    """Test government email domain validation."""
    from services.lea_auth import agency_verification
    
    # .gov domain should pass
    self.assertTrue(agency_verification.is_government_email("officer@gfpd.gov"))
    
    # County domain (.mt.us) should pass
    self.assertTrue(agency_verification.is_government_email("sheriff@cascade.mt.us"))
    
    # Non-government should fail
    self.assertFalse(agency_verification.is_government_email("officer@gmail.com"))
```

### Step 2–5: Implementation

Create `services/lea_auth/agency_verification.py`:

```python
"""LEA agency verification: ORI lookup and government email domain checks."""
import re
from typing import Optional, Dict, Any

# Montana ORI registry (minimal example; expand from Montana DOJ data)
MONTANA_ORI_REGISTRY = {
    "MTZ001": {"name": "Great Falls Police Department", "county": "Cascade"},
    "MTZ002": {"name": "Missoula Police Department", "county": "Missoula"},
    # TODO: Load full 56-county registry from external source
}

GOVERNMENT_EMAIL_DOMAINS = {
    '.gov',          # Federal/state
    '.mt.us',        # Montana county
    '.state.mt.us',  # Montana state agencies
}


def verify_agency_by_ori(ori_number: str) -> Optional[Dict[str, Any]]:
    """Look up agency by ORI (Originating Agency Identifier).
    
    Args:
        ori_number: ORI code (e.g., "MTZ001")
        
    Returns:
        Agency info dict if found, None otherwise
    """
    return MONTANA_ORI_REGISTRY.get(ori_number.upper())


def is_government_email(email: str) -> bool:
    """Check if email uses a government domain.
    
    Args:
        email: email address to validate
        
    Returns:
        True if government domain, False otherwise
    """
    if '@' not in email:
        return False
    
    _, domain = email.rsplit('@', 1)
    domain = domain.lower()
    
    return any(domain.endswith(gov_domain) for gov_domain in GOVERNMENT_EMAIL_DOMAINS)


def validate_agency_registration(
    org_name: str,
    ori_number: str,
    primary_contact_email: str
) -> tuple[bool, Optional[str]]:
    """Validate an agency registration request.
    
    Args:
        org_name: Organization name
        ori_number: ORI code
        primary_contact_email: Contact email
        
    Returns:
        (is_valid: bool, error_message: Optional[str])
    """
    if not org_name or not org_name.strip():
        return False, "Organization name required"
    
    if not ori_number or not ori_number.strip():
        return False, "ORI number required"
    
    if not is_government_email(primary_contact_email):
        return False, "Email must use government domain (.gov or .mt.us)"
    
    if not verify_agency_by_ori(ori_number):
        return False, "ORI number not found in Montana registry"
    
    return True, None
```

### Step 5: Commit

```bash
git add services/lea_auth/agency_verification.py tests/test_lea_auth.py
git commit -m "feat(lea): add ORI verification and government email domain checks"
```

---

## Task 2.4: Invitation Workflow (Create & Accept)

**Objective:** Implement user invitation lifecycle in `services/lea_auth/invitations.py`.

### Step 1: Write Failing Test

Add to `tests/test_lea_auth.py`:

```python
def test_create_invitation(self) -> None:
    """Test creating a pending invitation."""
    from services.lea_auth import invitations
    
    # Insert agency first
    cursor = self.conn.cursor()
    cursor.execute(
        "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
        ("Test PD", "police", "test", "Test", "admin@testpd.gov")
    )
    agency_id = cursor.lastrowid
    self.conn.commit()
    
    # Create invitation
    invite_token = invitations.create_invitation(
        self.conn,
        agency_id=agency_id,
        invited_email="officer@testpd.gov",
        invited_by_user_id=1,
        role="pio"
    )
    
    self.assertIsNotNone(invite_token)
    self.assertGreater(len(invite_token), 20)

def test_accept_invitation(self) -> None:
    """Test accepting an invitation and creating a user."""
    from services.lea_auth import invitations
    
    # Insert agency
    cursor = self.conn.cursor()
    cursor.execute(
        "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
        ("Test PD", "police", "test", "Test", "admin@testpd.gov")
    )
    agency_id = cursor.lastrowid
    self.conn.commit()
    
    # Create invitation
    invite_token = invitations.create_invitation(
        self.conn,
        agency_id=agency_id,
        invited_email="officer@testpd.gov",
        invited_by_user_id=1,
        role="pio"
    )
    
    # Accept invitation
    success, user_id = invitations.accept_invitation(
        self.conn,
        invite_token=invite_token,
        username="officer1",
        full_name="Officer One",
        password="SecurePassword123!"
    )
    
    self.assertTrue(success)
    self.assertIsNotNone(user_id)

def test_expired_invitation_rejected(self) -> None:
    """Test that expired invitations are rejected."""
    from services.lea_auth import invitations
    import time
    
    # Create invitation with 1-second expiry
    cursor = self.conn.cursor()
    cursor.execute(
        "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
        ("Test PD", "police", "test", "Test", "admin@testpd.gov")
    )
    agency_id = cursor.lastrowid
    self.conn.commit()
    
    invite_token = invitations.create_invitation(
        self.conn,
        agency_id=agency_id,
        invited_email="officer@testpd.gov",
        invited_by_user_id=1,
        role="pio",
        expires_in_seconds=1
    )
    
    # Wait for expiry
    time.sleep(2)
    
    # Accept should fail
    success, user_id = invitations.accept_invitation(
        self.conn,
        invite_token=invite_token,
        username="officer1",
        full_name="Officer One",
        password="SecurePassword123!"
    )
    
    self.assertFalse(success)
    self.assertIsNone(user_id)
```

### Step 2–5: Implementation

Create `services/lea_auth/invitations.py`:

```python
"""LEA user invitations: creation, validation, and acceptance."""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from . import user_auth

DEFAULT_INVITATION_TTL_DAYS = 7


def create_invitation(
    conn: sqlite3.Connection,
    agency_id: int,
    invited_email: str,
    invited_by_user_id: int,
    role: str = 'records_officer',
    expires_in_seconds: Optional[int] = None
) -> str:
    """Create a pending user invitation for an agency.
    
    Args:
        conn: Database connection
        agency_id: ID of the agency
        invited_email: Email of invitee
        invited_by_user_id: ID of user creating the invite
        role: Role for new user (admin, pio, records_officer)
        expires_in_seconds: TTL in seconds (default: 7 days)
        
    Returns:
        Invitation token (unique, one-time use)
    """
    if expires_in_seconds is None:
        expires_in_seconds = DEFAULT_INVITATION_TTL_DAYS * 86400
    
    invite_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).isoformat()
    
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO lea_invitations
        (agency_id, invited_email, invited_by_user_id, role, token_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        agency_id,
        invited_email,
        invited_by_user_id,
        role,
        user_auth.hash_password(invite_token),  # Store hashed token
        expiry,
        now
    ))
    conn.commit()
    
    return invite_token


def accept_invitation(
    conn: sqlite3.Connection,
    invite_token: str,
    username: str,
    full_name: str,
    password: str
) -> Tuple[bool, Optional[int]]:
    """Accept an invitation and create the user.
    
    Args:
        conn: Database connection
        invite_token: Invitation token from email
        username: Desired username
        full_name: Full name of user
        password: Password for account
        
    Returns:
        (success: bool, user_id: Optional[int])
    """
    cursor = conn.cursor()
    
    # Find invitation
    rows = cursor.execute('''
        SELECT id, agency_id, invited_email, role, expires_at, accepted_at
        FROM lea_invitations
        WHERE status = 'pending'
        ORDER BY created_at DESC
        LIMIT 100
    ''').fetchall()
    
    # Verify token against stored hashes
    invitation_record = None
    for row in rows:
        inv_id, agency_id, email, role, expiry, accepted_at = row
        # Get the token_hash from DB
        hash_row = cursor.execute(
            'SELECT token_hash FROM lea_invitations WHERE id = ?',
            (inv_id,)
        ).fetchone()
        
        if hash_row and user_auth.verify_password(invite_token, hash_row[0]):
            invitation_record = (inv_id, agency_id, email, role, expiry)
            break
    
    if not invitation_record:
        return False, None
    
    inv_id, agency_id, email, role, expiry = invitation_record
    
    # Check expiry
    if datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        return False, None
    
    # Create user
    now = datetime.now(timezone.utc).isoformat()
    pwd_hash = user_auth.hash_password(password)
    
    try:
        cursor.execute('''
            INSERT INTO lea_users
            (agency_id, username, email, full_name, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (agency_id, username, email, full_name, pwd_hash, role, now))
        
        user_id = cursor.lastrowid
        
        # Mark invitation as accepted
        cursor.execute(
            'UPDATE lea_invitations SET status = ?, accepted_at = ? WHERE id = ?',
            ('accepted', now, inv_id)
        )
        
        conn.commit()
        return True, user_id
    except sqlite3.IntegrityError:
        # Username already exists
        return False, None
```

### Step 5: Commit

```bash
git add services/lea_auth/invitations.py tests/test_lea_auth.py
git commit -m "feat(lea): add invitation workflow with token validation and TTL"
```

---

## Task 2.5: User Login Route & Session

**Objective:** Create `blueprints/lea_auth.py` with login/logout/profile routes.

### Tasks 2.6–2.7

- **2.6:** MFA TOTP setup (optional; can defer if time-constrained)
- **2.7:** Auth service integration tests (comprehensive test suite)

---

## Commit Sequence

```bash
# After Phase 1 is complete:
git add services/lea_auth/ tests/test_lea_auth.py
git commit -m "feat(lea): complete phase 2 authentication (bcrypt, JWT, ORI, invitations)"
git push origin main
```

---

## Ready for Phase 2 Dispatch

Once Phase 1 finishes, this plan will be passed to the **Auth Engineer subagent** with:
- All test templates (copy-paste ready)
- Implementation stubs
- Commit message templates
- Expected pass/fail outputs

Each task follows: **TEST FAIL → IMPLEMENT → TEST PASS → COMMIT**
