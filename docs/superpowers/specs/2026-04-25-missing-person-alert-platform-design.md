# Missing Person Alert Platform Design

Date: 2026-04-25
Status: Draft for review

## Goal

Expand the existing Montana missing-person directory into a local alert platform that:

- syncs from the official State of Montana missing-person source,
- keeps active and found cases visible in the public directory,
- alerts subscribers when a matching person is listed as missing,
- sends a second alert when that same case is marked found/located,
- matches alerts by subscriber county or city,
- sends email by default to existing active local subscribers,
- supports opt-in SMS and signed-in browser push enrollment for subscribers who explicitly enable those channels.

## Non-Goals

- Anonymous browser push subscriptions in v1.
- A new standalone subscriber system separate from the existing `subscribers` table.
- Replacing the Montana DOJ sync source.
- Adding native mobile push in the React Native app as part of this phase.

## Existing System

The codebase already includes:

- official Montana DOJ sync logic in [missing_persons.py](/root/montanablotter/missing_persons.py:1),
- public directory and detail routes in [app.py](/root/montanablotter/app.py:7864),
- public templates in [missing_persons.html](/root/montanablotter/templates/missing_persons.html:1) and [missing_person_detail.html](/root/montanablotter/templates/missing_person_detail.html:1),
- a missing-person email dispatcher in [missing_persons.py](/root/montanablotter/missing_persons.py:1308),
- an hourly watcher in [missing_person_watch.py](/root/montanablotter/missing_person_watch.py:1),
- subscriber records already used elsewhere in the app.

The current gap is not the directory itself. The gap is targeted, channel-aware alerting and explicit subscriber enrollment for SMS and browser push.

## Recommended Approach

Use the existing subscriber system as the single subscriber identity layer for all missing-person channels.

Reasoning:

- Email already maps naturally onto `subscribers`.
- County and city targeting should be defined once, not reimplemented across separate enrollment systems.
- Found alerts should reuse the same subscriber identity and location matching as initial alerts.
- Signed-in push enrollment is much safer and cleaner than anonymous push because it keeps device subscriptions attached to a real subscriber record.

## User Experience

### Public Directory

`/missing-persons` remains the statewide index and continues to show:

- active missing alerts,
- recently found / resolved cases,
- filter and search controls,
- case detail links.

New directory additions:

- a strong “Get Local Alerts” callout,
- clear language that email alerts are available now and SMS/push require opt-in enrollment,
- channel preference links to subscriber settings for signed-in users.

### Public Detail Page

`/missing-persons/<slug>` remains the canonical case page.

New detail page additions:

- local alert signup prompt tied to the person’s county/city,
- clear found-status presentation when a case resolves,
- messaging that subscribers in the matching area will receive a resolution alert when the person is located.

### Subscriber Settings

Subscribers need a settings surface that allows:

- editing county and city coverage,
- entering and verifying a phone number for SMS,
- enabling or disabling missing-person email alerts,
- enabling or disabling missing-person SMS alerts,
- enrolling the current browser for push notifications,
- disabling push per device.

Email is enabled by default for existing active local subscribers. SMS and push stay disabled until explicitly enrolled.

## Matching Rules

An alert is eligible for a subscriber when:

- the subscriber is active, and
- the subscriber has at least one saved county or city, and
- the missing-person record county matches one of the subscriber counties, or
- the missing-person record city matches one of the subscriber cities.

This is an `OR` match, not “most specific only.”

Normalization rules:

- county and city comparisons should be case-insensitive,
- trimmed values only,
- blank county/city values never match,
- matching should reuse existing subscriber county parsing where possible instead of creating a new location parser.

## Alert Lifecycle

### Initial Alert

When a case enters the system as `missing` and is newly imported from the official source:

- send an initial alert to all matching subscribers,
- email is sent automatically to matching active local subscribers,
- SMS is sent only to matching subscribers who explicitly opted in and have a verified phone,
- push is sent only to matching subscribers who explicitly opted in and have at least one active enrolled browser subscription.

### Found / Located Alert

When a case changes from active missing to `located`:

- increment the notification version,
- send a second alert to the same class of matching subscribers using the current record county/city,
- use found-specific subject lines and copy,
- keep the case public in the found/resolved lane.

### No-Resend Cases

Do not resend alerts on every sync refresh. A new send should happen only when:

- the person is first imported as a missing case, or
- the person changes from missing to located, or
- a future explicitly defined high-signal state change is introduced.

Routine metadata updates like summary tweaks, photo changes, or timestamp refreshes should not retrigger alerts by themselves.

## Data Model Changes

### Subscriber Table Extensions

Extend `subscribers` with missing-person channel preferences and enrollment state:

- `missing_person_email_opt_in INTEGER NOT NULL DEFAULT 1`
- `missing_person_sms_opt_in INTEGER NOT NULL DEFAULT 0`
- `missing_person_push_opt_in INTEGER NOT NULL DEFAULT 0`
- `phone TEXT DEFAULT ''`
- `phone_verified_at TEXT DEFAULT ''`
- `missing_person_alerts_updated_at TEXT DEFAULT ''`

Notes:

- Existing active subscribers should effectively keep email enabled by default.
- SMS delivery must require both `missing_person_sms_opt_in = 1` and a verified phone.
- Push delivery must require both `missing_person_push_opt_in = 1` and at least one active browser subscription.

### Push Subscription Table

Add a new table, for example `missing_person_push_subscriptions`:

- `id INTEGER PRIMARY KEY`
- `subscriber_id INTEGER NOT NULL`
- `endpoint TEXT NOT NULL`
- `p256dh_key TEXT NOT NULL`
- `auth_key TEXT NOT NULL`
- `user_agent TEXT DEFAULT ''`
- `device_label TEXT DEFAULT ''`
- `last_seen_county TEXT DEFAULT ''`
- `last_seen_city TEXT DEFAULT ''`
- `active INTEGER NOT NULL DEFAULT 1`
- `created_at TEXT DEFAULT (datetime('now'))`
- `updated_at TEXT DEFAULT (datetime('now'))`

Unique constraint:

- one active subscription per endpoint.

This table stores browser device registrations for signed-in subscribers only.

### Delivery Log

The current `missing_person_alert_deliveries` table is email-only. Replace or extend it into a generalized per-channel delivery log.

Recommended shape:

- `missing_person_alert_deliveries`
- `id INTEGER PRIMARY KEY`
- `missing_person_id INTEGER NOT NULL`
- `notification_version INTEGER NOT NULL`
- `subscriber_id INTEGER`
- `channel TEXT NOT NULL` (`email`, `sms`, `push`)
- `recipient TEXT NOT NULL`
- `delivery_status TEXT NOT NULL DEFAULT 'queued'`
- `provider_message_id TEXT DEFAULT ''`
- `error_message TEXT DEFAULT ''`
- `created_at TEXT DEFAULT (datetime('now'))`
- `updated_at TEXT DEFAULT (datetime('now'))`

Unique constraint:

- `(missing_person_id, notification_version, channel, recipient)`

This ensures dedupe across all channels.

## Backend Architecture

### Sync Layer

Keep `sync_official_missing_persons()` as the source-of-truth sync entry point.

New sync responsibilities:

- identify newly created missing records,
- identify records whose status changed from `missing` to `located`,
- increment `notification_version` only on send-worthy transitions,
- return enough structured result data for downstream dispatch.

### Alert Targeting Layer

Add a new helper that resolves matching subscribers for a missing-person record based on county/city OR logic.

Responsibilities:

- load active subscribers,
- parse county/city coverage,
- filter by missing-person county/city,
- separate subscribers by enabled channels,
- exclude subscribers with incomplete enrollment for SMS/push.

### Delivery Layer

Split channel delivery into explicit functions:

- `dispatch_missing_person_email_alerts(...)`
- `dispatch_missing_person_sms_alerts(...)`
- `dispatch_missing_person_push_alerts(...)`

And one orchestrator:

- `dispatch_missing_person_alerts(...)`

The orchestrator should:

- determine whether the alert is an initial alert or found alert,
- build channel-appropriate message content,
- queue/log each delivery attempt,
- update delivery status and last-alerted metadata,
- remain idempotent through the delivery uniqueness constraint.

### Queueing

For v1, synchronous dispatch from the watcher is acceptable if the volume is modest, but the code should be structured so dispatch can be moved into background jobs without redesign.

Implementation bias:

- keep dispatch functions side-effect isolated,
- return structured counts by channel,
- avoid mixing sync parsing with provider calls.

## Channel Design

### Email

Email behavior:

- default on for existing active local subscribers,
- county/city filtered,
- initial and found templates use different subject/body copy,
- unsubscribe links continue to work through the current subscriber token model.

### SMS

SMS provider:

- use the app’s existing Twilio configuration.

SMS behavior:

- opt-in only,
- require verified phone number,
- concise message with person name, status, county/city, and public record URL,
- distinct copy for initial vs found alert.

Phone verification:

- add a simple verification flow before SMS delivery is enabled.
- v1 can use a code-based verification flow through Twilio Verify if available, or a local code challenge if that is already the established verification pattern elsewhere in the app.

The exact verification implementation should be chosen during planning based on what already exists in the codebase and Twilio account setup.

### Browser Push

Push behavior:

- signed-in subscribers only,
- explicit opt-in from account/settings or alert UI,
- store browser subscription endpoint + keys in the new push-subscription table,
- route alerts through standard web push,
- disable or prune invalid subscriptions when providers return expired/unregistered responses.

Service worker updates:

- extend the site service worker to support push events,
- clicking a push notification should open the missing-person detail page.

Credentials:

- add VAPID configuration to app settings.

## Public Messaging

Copy should make these rules explicit:

- Montana DOJ is the official source,
- Montana Blotter republishes and organizes statewide records,
- email alerts are local by county/city,
- SMS and push are optional enrollment channels,
- found cases remain visible and trigger a second alert when applicable.

## Admin and Operations

Admin visibility should include:

- current sync health,
- channel delivery counts by alert event,
- failed email/SMS/push attempts,
- number of active push subscriptions,
- whether a located alert was issued for a resolved case.

Operational safeguards:

- delivery dedupe per channel,
- no repeat sends on unchanged syncs,
- graceful handling when Twilio or push delivery fails,
- invalid push endpoints automatically marked inactive after provider rejection.

## Testing Strategy

Add regression coverage for:

- county match triggers alerts,
- city match triggers alerts,
- non-matching subscribers do not receive alerts,
- existing subscribers get email by default,
- SMS requires opt-in plus verified phone,
- push requires opt-in plus active device subscription,
- initial missing import sends one version,
- missing-to-located transition increments version and sends a second alert,
- unchanged syncs do not resend,
- duplicate delivery rows are suppressed by uniqueness constraints,
- public directory still shows active and found lanes.

## Risks

### Consent Risk

Email default-on is acceptable because the user explicitly requested it for existing local subscribers, but SMS and push must remain explicit opt-in channels.

### Location Quality Risk

Some official records may have sparse or inconsistent city/county fields. Matching logic should prefer exact normalized values and avoid fuzzy expansion in v1.

### Provider Risk

SMS and push both introduce provider-specific failure modes. Delivery logs and inactive-subscription cleanup are required to keep the system healthy.

## Open Decisions Resolved

- Matching is `subscriber county OR city`.
- Found status sends a second alert.
- Existing local subscribers receive email alerts by default.
- SMS and push require explicit new enrollment.
- SMS uses Twilio.
- Browser push uses signed-in site enrollment, not anonymous browser subscriptions.

## Implementation Boundary

This design covers one coherent project:

- extend subscriber data and settings,
- add channel-aware missing-person dispatch,
- add SMS and push enrollment/delivery,
- preserve and strengthen the public directory.

It does not include unrelated mobile app push work, statewide emergency broadcast logic, or non-missing-person alert expansion.
