from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, session, url_for

import config
from services.monetization.black_friday import campaign_context, is_new_customer

black_friday_bp = Blueprint('black_friday', __name__)


@black_friday_bp.route('/black-friday')
def landing():
    campaign = campaign_context()
    if not campaign['is_live']:
        flash('The Black Friday offer is not currently available. See our current plans below.', 'info')
        return redirect(url_for('bail_bond_ads.pricing_page'))

    public_user_id = session.get('public_user_id')
    return render_template(
        'black_friday.html',
        campaign=campaign,
        black_friday_eligible=not public_user_id or is_new_customer(public_user_id),
        is_logged_in=bool(public_user_id),
        active_nav='pricing',
        page_title='Black Friday: 50% Off Your First Year',
        meta_description='New customers save 50% on their first year of Blotter Plus or Blotter Pro during the Montana Blotter Black Friday offer.',
        canonical_url=f"{current_app.config.get('BASE_URL', config.BASE_URL)}/black-friday",
        og_title='Montana Blotter Black Friday — 50% Off Your First Year',
        og_description='Get Blotter Plus for $39.50 or Blotter Pro for $130 for your first year. New customers only, November 27–30.',
        og_image=f"{current_app.config.get('BASE_URL', config.BASE_URL)}/static/icons/black-friday-2026.png",
        current_year=datetime.now().year,
    )


def register_black_friday_blueprint(app):
    app.register_blueprint(black_friday_bp)

    @app.context_processor
    def inject_black_friday_campaign():
        return {'black_friday_banner': campaign_context()}
