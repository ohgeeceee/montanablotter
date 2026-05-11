# Donations Launch Runbook

This runbook is the production sequence for enabling Stripe donations on Montana Blotter safely.

## 1) Immediate security action
- Rotate the Stripe keys that were shared in chat.
- Create fresh keys in Stripe Dashboard and replace local environment values.

## 2) Configure environment
Set these in `.env`:

```bash
MB_DONATIONS_ENABLED=false
MB_STRIPE_PUBLISHABLE_KEY=pk_live_...
MB_STRIPE_SECRET_KEY=sk_live_...
MB_STRIPE_WEBHOOK_SECRET=whsec_...
MB_DONATION_CURRENCY=usd
MB_DONATION_MIN_CENTS=500
MB_DONATION_SUGGESTED_AMOUNTS=500,1500,2500,5000
```

Keep `MB_DONATIONS_ENABLED=false` until webhook verification passes.

## 3) Stripe webhook endpoint
In Stripe Dashboard:

1. Add endpoint: `https://montanablotter.com/webhooks/stripe`
2. Subscribe at minimum:
- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`
- `checkout.session.expired`
- `charge.refunded`
3. Copy signing secret (`whsec_...`) into `MB_STRIPE_WEBHOOK_SECRET`.

## 4) Database migration check
Run:

```bash
cd /root/montanablotter
/root/montanablotter/venv/bin/python3 init_db.py
```

## 5) Preflight validation
Run:

```bash
cd /root/montanablotter
/root/montanablotter/venv/bin/python3 donations_preflight.py
```

Pass criteria:
- schema exists
- checkout keys configured
- webhook secret configured
- no stale unprocessed webhook rows

## 6) Restart app
Run:

```bash
systemctl restart montanablotter.service
```

## 7) Live smoke test
1. Visit `/donate`.
2. Complete a small real donation.
3. Confirm:
- `donations` row moves to `succeeded`
- `payment_webhook_events` row is `processed=1`
- `/admin/donations` funnel and revenue metrics update

## 8) Enable donations
After successful webhook validation:

1. Set `MB_DONATIONS_ENABLED=true`
2. Restart service.
3. Re-check `/admin/donations` Go-Live Readiness status.

## 9) Post-launch monitoring (first 72 hours)
- Check `/admin/donations` at least 2x/day.
- Watch webhook errors (7d tile).
- Watch stale webhook count.
- Watch for unusual drop in start->success rate.

## 10) Operations recovery
- If webhook deliveries fail temporarily, run admin reconcile from `/admin/donations`.
- Use "Reconcile Unprocessed Webhooks" (default limit: `100`).
- Export accounting data from `/admin/donations/export.csv`.
