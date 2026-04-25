# Missing Persons Database Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current missing-persons public pages with a database-style sitewide experience that foregrounds search, filters, newest alerts, and located/resolved updates.

**Architecture:** Keep the existing SQLite-backed `missing_persons` source of truth and public detail routes, but reshape the public listing context so the website can render a database index with separate alert and resolved lanes. Update the shared public navigation so Missing Persons is a first-class sitewide destination, and refresh the homepage alert module so resolved cases remain visible as status changes occur.

**Tech Stack:** Flask, Jinja2 templates, SQLite, Tailwind utility classes, existing `missing_persons` data helpers.

---

### Task 1: Promote Missing Persons in the sitewide public navigation

**Files:**
- Modify: `app.py`
- Modify: `templates/includes/public_official_banner.html` if needed for header alignment

- [ ] **Step 1: Update the public nav item lists**

```python
public_primary_nav_items = [
    {'id': 'home', 'href': home_href, 'label': 'Home'},
    {'id': 'meetings', 'href': _public_meetings_href(), 'label': 'Meetings'},
    {'id': 'courts', 'href': '/courts', 'label': 'Courts'},
    {'id': 'arrests', 'href': '/arrests', 'label': 'Arrests'},
    {'id': 'counties', 'href': '/counties', 'label': 'Counties'},
    {'id': 'jail_rosters', 'href': '/detention', 'label': 'Detention', 'menu_label': 'Jails'},
    {'id': 'bail_bonds', 'href': '/bail-bonds', 'label': 'Bail Bonds', 'menu_label': 'Bail'},
    {'id': 'missing_persons', 'href': '/missing-persons', 'label': 'Missing Persons', 'menu_label': 'Missing'},
]
public_secondary_nav_items = [
    {'id': 'case_journeys', 'href': '/case-journeys', 'label': 'Case Journeys', 'menu_label': 'Cases'},
    {'id': 'jail_bookings', 'href': '/jail-bookings', 'label': 'New Bookings', 'menu_label': 'Bookings'},
    {'id': 'support', 'href': '/support', 'label': 'Support'},
]
```

- [ ] **Step 2: Verify the header and mobile menu still render cleanly**

Run: `./venv/bin/python -m pytest tests/test_public_detail_routes.py -q`
Expected: pass after the nav change.

- [ ] **Step 3: Commit the navigation update**

```bash
git add app.py
git commit -m "feat: promote missing persons in site navigation"
```

### Task 2: Turn the missing-persons index into a database-style explorer

**Files:**
- Modify: `missing_persons.py`
- Modify: `app.py`
- Modify: `templates/missing_persons.html`

- [ ] **Step 1: Extend the public context helper to support sort controls and focused lanes**

```python
def missing_person_public_context(
    conn: sqlite3.Connection,
    *,
    status_filter: str = 'active',
    q: str = '',
    sort: str = 'updated_desc',
) -> dict[str, Any]:
    ...
```

- [ ] **Step 2: Add database lanes for newest active alerts and recent resolved updates**

```python
return {
    'rows': [...],
    'status_filter': normalized_status,
    'sort_filter': normalized_sort,
    'q': search_term,
    'summary': {...},
    'latest_active': ...,
    'newest_alerts': [...],
    'recent_resolved': [...],
    'source_stats': source_stats,
}
```

- [ ] **Step 3: Render the new explorer layout in the public template**

```html
<form method="get" class="...">
  <!-- search, status, sort, reset -->
</form>
<div class="...">
  <!-- main rows -->
  <!-- newest alerts rail -->
  <!-- resolved updates rail -->
</div>
```

- [ ] **Step 4: Wire the Flask route to pass the new sort argument**

```python
context = missing_person_public_context(
    conn,
    status_filter=request.args.get('status'),
    q=request.args.get('q'),
    sort=request.args.get('sort'),
)
```

- [ ] **Step 5: Run the public route and missing-person tests**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py tests/test_public_detail_routes.py -q`
Expected: pass with the new list behavior.

- [ ] **Step 6: Commit the index redesign**

```bash
git add missing_persons.py app.py templates/missing_persons.html
git commit -m "feat: redesign missing persons index"
```

### Task 3: Refresh the homepage missing-person alert module

**Files:**
- Modify: `missing_persons.py`
- Modify: `templates/includes/homepage_missing_persons_alert.html`

- [ ] **Step 1: Include resolved status updates alongside active cards**

```python
def missing_person_homepage_context(conn: sqlite3.Connection, *, limit: int = 3) -> dict[str, Any]:
    ...
    return {
        'rows': [...],
        'resolved_rows': [...],
        'total_active': ...,
        'latest_update': ...,
        'latest_update_label': ...,
        'official_last_checked': ...,
    }
```

- [ ] **Step 2: Rework the homepage module to show active alerts and located updates**

```html
<section class="...">
  <!-- active alert summary -->
  <!-- active cards -->
  <!-- resolved updates -->
</section>
```

- [ ] **Step 3: Run a homepage render test**

Run: `./venv/bin/python -m pytest tests/test_homepage_layout.py -q`
Expected: pass and still include the missing-person alert module.

- [ ] **Step 4: Commit the homepage refresh**

```bash
git add missing_persons.py templates/includes/homepage_missing_persons_alert.html
git commit -m "feat: surface missing persons updates on homepage"
```

### Task 4: Add regression coverage for navigation and database-style rendering

**Files:**
- Modify: `tests/test_missing_persons.py`
- Modify: `tests/test_public_detail_routes.py`

- [ ] **Step 1: Add assertions for the new public nav link**

```python
response = client.get('/')
html = response.get_data(as_text=True)
assert '/missing-persons' in html
assert 'Missing Persons' in html
```

- [ ] **Step 2: Add assertions for the database-style index lanes**

```python
response = client.get('/missing-persons?status=all&sort=recently_resolved')
html = response.get_data(as_text=True)
assert 'Newest Alerts' in html
assert 'Found / Resolved Updates' in html
```

- [ ] **Step 3: Run the focused test suite**

Run: `./venv/bin/python -m pytest tests/test_missing_persons.py tests/test_public_detail_routes.py tests/test_homepage_layout.py -q`
Expected: pass.

- [ ] **Step 4: Commit the regression coverage**

```bash
git add tests/test_missing_persons.py tests/test_public_detail_routes.py
git commit -m "test: cover missing persons public refresh"
```

