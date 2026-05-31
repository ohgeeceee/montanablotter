# Facebook Page Manager Setup Guide

## Current Status

The Montana Blotter Facebook integration is **partially configured but broken**:

- `facebook_enabled` = 1 (on)
- `facebook_auto_enqueue_enabled` = 1 (on)
- `facebook_auto_publish_enabled` = 1 (on)
- **BUT** the access token is invalid → all 413 queued posts have failed with `Invalid OAuth access token`
- **AND** blog post queuing has a content-type mismatch bug (blog post IDs were being looked up in the blotter incidents table)

## Step 1: Get a Valid Facebook Access Token

### 1.1 Create a Facebook App (if you don't have one)

1. Go to https://developers.facebook.com/
2. Log in with the Facebook account that manages your page
3. Click "My Apps" → "Create App"
4. Select **"Other"** → **"Business"**
5. App name: `Montana Blotter Publisher` (or similar)
6. Note the **App ID** and **App Secret**

### 1.2 Get a Page Access Token

The token currently in the DB (`74696aa8dab...`) is not a valid Page Access Token. You need a real one.

**Option A: Graph API Explorer (fastest for testing)**

1. Go to https://developers.facebook.com/tools/explorer/
2. Select your app in the top-right dropdown
3. Click "Generate Access Token"
4. In the permission selector, add:
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `publish_to_groups` (if needed)
5. Click "Generate Token"
6. Copy the **short-lived User Access Token**
7. Exchange it for a long-lived Page Access Token:

```bash
# 1. Exchange short-lived user token for long-lived user token
curl -X GET "https://graph.facebook.com/v22.0/oauth/access_token?\
  grant_type=fb_exchange_token&\
  client_id=YOUR_APP_ID&\
  client_secret=YOUR_APP_SECRET&\
  fb_exchange_token=SHORT_LIVED_TOKEN"

# 2. Get your Page Access Token (from the response above)
curl -X GET "https://graph.facebook.com/v22.0/me/accounts?\
  access_token=LONG_LIVED_USER_TOKEN"
```

Grab the `access_token` field for your page from the second response.

**Option B: Facebook Login flow (production)**

For a persistent production token, implement the Login flow or use a System User token via Meta Business Manager.

### 1.3 Store the Token

Once you have the Page Access Token, update the setting:

```bash
cd /root/montanablotter
sqlite3 blotter.db "UPDATE app_settings SET value = 'YOUR_NEW_PAGE_ACCESS_TOKEN' WHERE key = 'facebook_page_access_token';"
```

Also update `facebook_page_id` if your page ID changed:

```bash
sqlite3 blotter.db "UPDATE app_settings SET value = 'YOUR_PAGE_ID' WHERE key = 'facebook_page_id';"
```

### 1.4 Verify the Token

```bash
curl -X GET "https://graph.facebook.com/v22.0/me?access_token=YOUR_NEW_PAGE_ACCESS_TOKEN"
```

You should see your page info, not an error.

## Step 2: Fix the Schema (Content Type Support)

Run the migration script to support blog posts, blotter posts, and custom content:

```bash
cd /root/montanablotter
./venv/bin/python3 scripts/setup/facebook_migrate_schema.py
```

This adds:
- `content_type` to `facebook_post_queue` (`blotter`, `blog`, `custom`)
- `blog_post_id` for blog post references
- `link_url` for direct-link posts (stats, Innocence Project)

## Step 3: Test Publishing

### Test a blotter post

```bash
./venv/bin/python3 -c "
from facebook_publisher import run_facebook_queue
result = run_facebook_queue(max_items=1, manual_trigger=True)
print(result)
"
```

### Test a blog post

```bash
./venv/bin/python3 facebook_page_manager.py --mode post --limit 1
./venv/bin/python3 -c "
from facebook_publisher import run_facebook_queue
result = run_facebook_queue(max_items=1, manual_trigger=True)
print(result)
"
```

### Test stats/Innocence Project content

```bash
./venv/bin/python3 facebook_stats_poster.py --mode preview
./venv/bin/python3 facebook_stats_poster.py --mode queue-stats --limit 1
./venv/bin/python3 facebook_stats_poster.py --mode queue-innocence --limit 1
./venv/bin/python3 -c "
from facebook_publisher import run_facebook_queue
result = run_facebook_queue(max_items=2, manual_trigger=True)
print(result)
"
```

## Step 4: Enable Automation

The existing cron job runs every 15 minutes:

```
5,20,35,50 * * * * .../facebook_worker.py
```

This processes the queue. To also auto-generate stats and Innocence Project posts weekly, add to crontab:

```bash
# Weekly stats post (Sundays at 10:00 AM MT)
0 10 * * 0 cd /root/montanablotter && ./venv/bin/python3 facebook_stats_poster.py --mode queue-stats --limit 1 >> /root/montanablotter/logs/facebook_stats.log 2>&1

# Innocence Project post (Wednesdays at 9:00 AM MT)
0 9 * * 3 cd /root/montanablotter && ./venv/bin/python3 facebook_stats_poster.py --mode queue-innocence --limit 1 >> /root/montanablotter/logs/facebook_stats.log 2>&1
```

## Step 5: Content Strategy

### What gets posted automatically

| Source | Frequency | Content |
|--------|-----------|---------|
| Blotter incidents | Real-time | New parsed + audited blotter posts |
| Blog posts | Every 10 min | New published blog posts (Daily Activity Reports, analysis) |
| Stats | Weekly | Top counties, trending charges, DUI counts |
| Innocence Project | Weekly | Pre-rotated awareness messages |

### Innocence Project Messages

Edit `/root/montanablotter/facebook_stats_poster.py` and update `INNOCENCE_PROJECT_MESSAGES` with your preferred content. Current defaults are Montana-specific awareness messages.

## Troubleshooting

### "Invalid OAuth access token"

Your token expired or was revoked. Page Access Tokens from the Graph API Explorer expire in ~1 hour. For production, use a long-lived token or implement token refresh.

### "(#200) Permissions error"

Your app needs Business Verification to post publicly. Until then, posts may only be visible to app admins. Go to your app dashboard → App Review → Permissions and Features → request `pages_manage_posts`.

### "post_not_found" or wrong content

This was the blog/blotter ID mismatch bug. Run the schema migration (Step 2) to fix.
