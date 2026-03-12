"""
Seed Admin User
Creates or updates the admin user with proper bcrypt hashing
"""

import sqlite3
import sys
import getpass
import os
from flask_bcrypt import Bcrypt
from flask import Flask
import config

# Create dummy app for Bcrypt
app = Flask(__name__)
bcrypt = Bcrypt(app)

def _resolve_password(cli_password):
    if cli_password:
        return cli_password

    env_password = os.getenv('MB_ADMIN_BOOTSTRAP_PASSWORD', '').strip()
    if env_password:
        return env_password

    if sys.stdin.isatty():
        first = getpass.getpass('Enter admin password: ')
        second = getpass.getpass('Confirm admin password: ')
        if first != second:
            raise ValueError('Password confirmation does not match')
        if len(first) < 12:
            raise ValueError('Password must be at least 12 characters')
        return first

    raise ValueError(
        'Admin password required. Pass it as argv[2] or set MB_ADMIN_BOOTSTRAP_PASSWORD.'
    )


def seed_admin(username='admin', password=''):
    """Create or update admin user"""
    if not username:
        raise ValueError('Username is required')
    password = _resolve_password(password)

    # Generate hashed password
    hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
    
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        
        # Check if admin exists
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        
        if user:
            # Update existing admin
            cursor.execute(
                "UPDATE users SET password = ?, membership = 'pro', role = 'super_admin', is_active = 1 WHERE username = ?",
                (hashed_pw, username)
            )
            print(f"✅ Admin user '{username}' updated with new password")
        else:
            # Create new admin
            cursor.execute(
                "INSERT INTO users (username, password, membership, role, is_active) VALUES (?, ?, ?, ?, ?)",
                (username, hashed_pw, 'pro', 'super_admin', 1)
            )
            print(f"✅ Admin user '{username}' created")
        
        conn.commit()
        conn.close()
        print(f"🔐 Admin user '{username}' is ready (role: super_admin)")
        
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    username = 'admin'
    password = ''

    if len(sys.argv) >= 3:
        username = sys.argv[1]
        password = sys.argv[2]
    elif len(sys.argv) == 2:
        username = sys.argv[1]

    seed_admin(username, password)
