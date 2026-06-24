"""Pytest/shared test defaults for Montana Blotter."""
import os

# The sign-in wall is on by default in production, but tests need anonymous
# access to most pages. Leave it off unless the runner explicitly enabled it.
os.environ.setdefault('MB_REQUIRE_SIGNIN', 'false')
