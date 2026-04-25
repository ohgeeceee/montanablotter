# Admin AI Console Design

## Goal

Add an admin-only AI console to Montana Blotter that lets logged-in admins query internal data and create draft-only admin outputs through Kimi 2.6. The first version must not expose any public entry point and must not allow immediate external sends or publishes.

## Scope

This design covers:

- an admin page at `/admin/ai`
- server-side Kimi orchestration for admin use
- a narrow registry of read and draft-only action tools
- confirmation and audit requirements for write operations
- regression coverage for access control and tool execution boundaries

This design does not cover:

- public AI features
- direct model access to raw database credentials
- arbitrary SQL execution
- immediate publish/send actions
- background multi-step agent workflows

## Recommended Approach

Implement an `Admin AI Console` inside the existing admin blueprint and route all model interactions through a server-side tool registry.

The model will only see explicitly registered tools. Each tool will validate its inputs, run bounded server-side logic, and return compact JSON results. Any tool that changes application state will be treated as a draft-only action and will require an explicit confirmation step in the UI before execution.

## Route And Access Model

Add a new admin route:

- `GET /admin/ai`
- `POST /admin/ai/query`
- `POST /admin/ai/confirm`

Access requirements:

- all routes require existing admin login
- no public links or public JSON endpoints
- responses must follow existing admin auth and CSRF/session patterns already used in the app

If a non-authenticated user requests the page or the JSON endpoints, the app should follow the project’s existing admin auth behavior, typically redirecting to admin login for page requests and rejecting unauthorized POSTs.

## UI Design

Add a new template following existing admin dashboard styling.

Primary page regions:

- a short description explaining that the console can read Montana Blotter data and create drafts
- a chat-style transcript area
- a single prompt form
- a pending action review card shown only when the model proposes a draft action
- a recent activity panel showing the last few AI actions for the current admin session or recent admins

The page should make the following states explicit:

- answer returned with no action requested
- draft action proposed but waiting for confirmation
- draft action executed
- tool failure or validation failure

The UI must distinguish read-only answers from actions. Proposed actions should display:

- tool name
- human-readable summary
- validated arguments
- confirmation button
- cancel button

## Tool Architecture

Create a small admin AI service layer rather than embedding all behavior directly in the route.

Suggested module split:

- `blueprints/admin/ai_console.py` for routes
- `admin_ai.py` or similar service module for Kimi client orchestration
- optional helper module for tool registry and audit payload shaping

The registry should define:

- tool name
- tool description
- input schema
- whether the tool is read-only or write-intent
- execution function

The route layer should never execute arbitrary tool names. It should only dispatch registered tools.

## First-Version Tools

### Read Tools

- `search_records`
  Search blotter records by county and keyword with a hard result cap.
- `get_missing_persons_summary`
  Return current summary counts and official source stats.
- `get_recent_posts`
  Return recent post metadata with draft/published status.
- `get_subscriber_counts`
  Return bounded subscriber totals and recent signup stats.

### Draft-Only Action Tools

- `create_blog_draft`
  Create a new draft post from validated title, summary, and body inputs.
- `update_blog_draft`
  Update an existing draft post only if it is still in draft state.
- `create_facebook_draft`
  Create a queued or saved Facebook draft item without publishing it.
- `create_email_draft`
  Create a draft email payload without sending it.

First-version guardrails:

- no delete actions
- no status changes from draft to published
- no email send actions
- no payment, auth, or user-role mutation tools

## Confirmation Flow

Read tools may run immediately.

Write-intent tools must follow a two-step process:

1. The model proposes the action and arguments.
2. The server stores a signed pending action payload in the admin session.
3. The UI displays the action summary and asks for confirmation.
4. Only `POST /admin/ai/confirm` may execute the tool.

Confirmation requirements:

- pending action must belong to the current admin session
- the confirmed payload must match the stored signed payload
- pending actions expire after a short interval
- a confirmed action is single-use and cleared from session after execution

## Audit Logging

Every write-intent proposal and every confirmed execution must be logged.

Required audit fields:

- admin user id
- admin email or username if available in current patterns
- route source
- tool name
- normalized arguments
- result status
- timestamp

Recommended additional fields:

- model name
- whether the action was only proposed or actually executed
- record ids created or updated

Audit logging should reuse the existing admin audit mechanism if one already exists in the admin blueprint.

## Data And Security Constraints

Kimi must never receive:

- database credentials
- unrestricted SQL capability
- raw session data
- secrets from config or environment

Tool responses should be minimized to the fields needed for the admin task. For example, subscriber counts should return aggregate numbers, not whole subscriber exports.

Validation rules:

- hard result caps on read tools
- explicit allowlists for editable draft fields
- input length limits for generated content
- object existence checks before draft updates

## Failure Handling

Handle failure in these categories:

- model request failure
- tool validation failure
- tool execution failure
- expired or missing confirmation token
- authorization failure

Behavior:

- show a clear admin-facing error message
- do not partially execute a write action
- audit failed write confirmations when possible
- preserve the transcript context enough for the admin to retry

## Testing Strategy

Add focused tests before implementation for:

- admin auth required on `/admin/ai`
- unauthorized POST rejection on query/confirm endpoints
- read tool execution stays bounded
- write-intent tools do not execute during proposal phase
- confirmation executes only matching pending actions
- expired or replayed confirmations are rejected

Prefer isolating the service layer so model calls can be stubbed in tests without network access.

## Implementation Notes

Start by reusing the existing `kimi_sqlite_agent.py` tool logic where practical, but move shared tool execution into an importable service module so the admin page and CLI do not duplicate the same DB query functions.

For v1, keep the transcript server-rendered or lightly JSON-backed inside the admin page. Do not introduce websockets or live streaming unless the existing admin stack already has a clean pattern for it.

## Acceptance Criteria

- logged-in admins can open `/admin/ai`
- non-admin/public users cannot access the page or endpoints
- admins can ask read-only data questions through Kimi
- admins can request draft-only actions and must confirm them before execution
- confirmed actions create drafts but do not publish or send externally
- every proposed and executed write action is audit logged
- focused tests cover access control and confirmation safety
