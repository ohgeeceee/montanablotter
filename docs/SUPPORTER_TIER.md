# Supporter Tier — $1/month

## Status: LIVE on https://montanablotter.com

## What was built

- **Stripe product** `prod_Ugx4dJmT1A6ePR` + **price** `price_1ThZA5GL8T8btZcuvOSD4EUK` ($1.00 USD / month)
- **`POST /supporter/checkout`** — creates a Stripe Checkout session, returns `checkout_url`
- **`/supporter/success`** and **`/supporter/cancel`** — redirect targets
- **Webhook routing** — `_apply_supporter_stripe_event` in `app.py` handles:
  - `checkout.session.completed` / `checkout.session.async_payment_succeeded` → sets `subscriber_plan='supporter'`, `subscription_status='active'` for the email in the metadata
  - `customer.subscription.updated` (status=canceled/past_due/unpaid) / `customer.subscription.deleted` → flips back to `'free'`, status=`'cancelled'`
- **`subscriber_plan` column** added to `subscribers` table (defaults `'free'`)
- **Subscribe page UI** — amber "Become a Supporter" box on `/subscribe` with a one-click "Upgrade to Supporter" button that POSTs to `/supporter/checkout` and redirects to Stripe

## Stripe dashboard — what you need to do

1. Go to https://dashboard.stripe.com/webhooks
2. Find the existing webhook endpoint pointed at `https://montanablotter.com/webhooks/stripe` (it already exists for the warrant-access tier and donations)
3. **Add events to listen for** (if not already present):
   - `checkout.session.completed`
   - `checkout.session.async_payment_succeeded`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
4. Copy the signing secret into `MB_STRIPE_WEBHOOK_SECRET` in `/root/montanablotter/.env` (already set, verify it matches)
5. Test the flow:
   - Visit https://montanablotter.com/subscribe
   - Click "Upgrade to Supporter"
   - Use Stripe test card `4242 4242 4242 4242` if the dashboard is in test mode, or a real card in live mode
   - After checkout, check `subscribers` table: `SELECT email, subscriber_plan, subscription_status FROM subscribers WHERE subscriber_plan = 'supporter';`

## Notes

- The existing `/webhooks/stripe` endpoint handles ALL Stripe events and routes supporter events via the `tier=supporter` metadata. No new endpoint or secret needed.
- If you want to A/B test or track supporter conversions, the source attribution is preserved: the checkout form is on `/subscribe`, so users who upgrade are already in the subscribers table before checkout completes.
- The Supporter button currently uses the email already typed in the subscribe form. If the form is empty, the click is a no-op — consider a follow-up to grab the email in a modal/popup.
