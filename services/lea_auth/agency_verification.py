"""LEA agency verification utilities."""

import re


# Whitelist of Montana government domains
MT_GOVERNMENT_DOMAINS = {
    'gfpd.gov', 'cascadecountymt.gov', 'msoutnews.org',
    'montanaagencyname.gov', 'montanaagency.gov',
    # Add more as discovered during onboarding
}


def verify_email_domain(email: str) -> bool:
    """
    Verify that the email is from a government domain.

    Currently supports:
    - *.gov domains (assumed government)
    - Whitelisted Montana agency-specific domains

    Returns True if domain is trusted, False otherwise.
    """
    if '@' not in email:
        return False

    domain = email.split('@')[1].lower()

    # Check whitelist first
    if domain in MT_GOVERNMENT_DOMAINS:
        return True

    # Allow any .gov domain (conservative but effective)
    if domain.endswith('.gov'):
        return True

    return False


def verify_ori_number(ori: str) -> bool:
    """
    Verify ORI number format (9 chars: 2-char state code + 7 digits).

    Format: SSXXXXX (S = state, X = digits)
    Example: MT0120100

    Note: We do NOT validate against FBI CJIS database in MVP.
    That requires registration and API credentials.
    """
    if not ori or len(ori) != 9:
        return False

    # Must match pattern: 2 letters + 7 digits
    pattern = r'^[A-Z]{2}\d{7}$'
    return bool(re.match(pattern, ori))
