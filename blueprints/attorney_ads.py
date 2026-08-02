"""Attorney sponsored-listing product — public landing page + claim form.

Path A: sponsored tier on top of the existing free /attorneys directory.
Silver ($99/mo) and Gold ($199/mo) rank above free listings in their county
and get a badge. Free Bronze remains the default opt-in listing.

2026-06-06: shipped with manual invoicing (admin flips the tier). Stripe
self-serve checkout can be wired in by mirroring `blueprints/recovery_ads.py`.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from db import get_db
from init_db import ensure_attorney_ad_schema

attorney_ads_bp = Blueprint('attorney_ads', __name__)

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

_TIERS = [
    {
        'id': 'silver',
        'name': 'Silver Featured',
        'price_monthly': 99,
        'price_label': '$99/mo',
        'price_label_annual': '$990/yr (save $198)',
        'badge': 'Featured',
        'description': 'Pinned above free listings in your county. Logo upload, 500-character blurb, larger card.',
        'features': [
            'Pinned above free listings in your county',
            'Logo on your profile card',
            '500-character blurb',
            'Larger card with "Featured" badge',
            'Mobile tap-to-call',
            'Monthly impression report',
        ],
    },
    {
        'id': 'gold',
        'name': 'Gold Priority',
        'price_monthly': 199,
        'price_label': '$199/mo',
        'price_label_annual': '$1,990/yr (save $398)',
        'badge': 'Priority Placement',
        'description': 'Top of your county\'s section. Logo + photo + custom callout. The firm Montana Blotter surfaces first.',
        'features': [
            'Everything in Silver',
            'Top-of-county placement (above Silver)',
            'Photo on your profile card',
            'Custom callout (1-line tagline)',
            '"Priority Placement" badge',
            'Featured on warrant detail pages in your county',
            'Priority placement on /arrests in your county',
        ],
    },
]

_VALID_TIER_IDS = {t['id'] for t in _TIERS}

# Logo + photo upload constraints
LOGO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'attorney_logos'
)
PHOTO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'attorney_photos'
)
_ALLOWED_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp', 'svg'}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB

os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
os.makedirs(PHOTO_UPLOAD_DIR, exist_ok=True)


def _tier_lookup() -> dict:
    return {t['id']: t for t in _TIERS}


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@attorney_ads_bp.route('/advertise/attorney-sponsorship')
def advertise_attorney_sponsorship():
    """Landing page for the sponsored-listing product."""
    conn = get_db()
    ensure_attorney_ad_schema(conn)
    conn.close()
    return render_template(
        'advertise_attorney_sponsorship.html',
        tiers=_TIERS,
        current_year=datetime.now().year,
        page_title='Advertise with Montana Blotter — Defense Attorney Sponsorship',
        meta_description=(
            'Get your Montana criminal defense, DUI, or family law firm listed '
            'in the Montana Blotter defense attorney directory. Free Bronze, '
            'Silver Featured ($99/mo), and Gold Priority ($199/mo) tiers. '
            'Reach Montana families at the moment they search for a lawyer.'
        ),
    )


@attorney_ads_bp.route('/advertise/attorney-sponsorship/claim', methods=['POST'])
def attorney_sponsorship_claim():
    """Self-service claim form — creates a pending claim for admin review."""
    form = request.form

    # Required field validation
    errors = []
    firm_name = (form.get('firm_name') or '').strip()[:200]
    contact_name = (form.get('contact_name') or '').strip()[:200]
    contact_email = (form.get('contact_email') or '').strip()[:160].lower()
    counties_served = (form.get('counties_served') or '').strip()[:400]
    tier_requested = (form.get('tier_requested') or '').strip().lower()
    mt_bar_number = (form.get('mt_bar_number') or '').strip()[:40]

    if not firm_name:
        errors.append('Firm name is required.')
    if not contact_name:
        errors.append('Your name is required.')
    if not contact_email or '@' not in contact_email:
        errors.append('A valid email is required.')
    if not counties_served:
        errors.append('At least one county is required.')
    if tier_requested not in _VALID_TIER_IDS:
        errors.append('Please pick a valid sponsorship tier.')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('.advertise_attorney_sponsorship'))

    contact_phone = (form.get('contact_phone') or '').strip()[:40]
    website = (form.get('website') or '').strip()[:200]
    practice_areas = (form.get('practice_areas') or '').strip()[:400]
    blurb = (form.get('blurb') or '').strip()[:1000]

    # Normalize website
    if website and not website.startswith(('http://', 'https://')):
        website = 'https://' + website

    conn = get_db()
    ensure_attorney_ad_schema(conn)
    conn.execute(
        '''
        INSERT INTO attorney_sponsored_claims
            (firm_name, contact_name, contact_email, contact_phone,
             counties_served, tier_requested, website, practice_areas,
             blurb, mt_bar_number, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ''',
        (firm_name, contact_name, contact_email, contact_phone,
         counties_served, tier_requested, website, practice_areas,
         blurb, mt_bar_number),
    )
    conn.commit()
    conn.close()

    return render_template(
        'advertise_attorney_sponsorship_thanks.html',
        tier_name=_tier_lookup().get(tier_requested, {}).get('name', 'Sponsored'),
        firm_name=firm_name,
        current_year=datetime.now().year,
    )
