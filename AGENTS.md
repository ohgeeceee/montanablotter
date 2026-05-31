# Repository Guidelines

## Project Structure & Module Organization

- `app.py` — primary Flask app entrypoint (WSGI: `app:app`).
- `blueprints/`, `templates/`, `static/` — web routes, Jinja templates, and assets.
- `core/`, `services/`, `ingestion/`, `scrapers/`, `utils/` — domain logic, background jobs, and source adapters.
- `tests/` — pytest suite (fixtures in `tests/fixtures/`).
- `scripts/maintenance/`, `scripts/ops/` — one-off maintenance and operations helpers (many root symlinks point here).
- `mobile/` — Expo/React Native client.

## Build, Test, and Development Commands

- `python3 -m venv venv && source venv/bin/activate` — create/enter virtualenv.
- `pip install -r requirements.txt` — install server dependencies.
- `cp .env.example .env` — configure local environment.
- `python3 init_db.py` — initialize local SQLite schema.
- `python3 app.py` — run the dev server.
- `./venv/bin/python3 -m pytest` — run the test suite.
- `gitleaks git --config .gitleaks.toml --redact` — run secret scan locally (matches CI).
- `./venv/bin/python3 script_watchdog.py` — basic ops/cron health checks.
- Mobile: `cd mobile && npm install && npm run start` (or `npm run android|ios|web`).

## Coding Style & Naming Conventions

- Python: 4-space indentation, PEP 8-ish formatting, `snake_case` for functions/variables, `PascalCase` for classes.
- Tests: `tests/test_*.py` with `test_*` functions; prefer small, isolated tests with fixtures under `tests/fixtures/`.
- Mobile (TypeScript): keep React components in `PascalCase` and prefer `npm run typecheck` before pushing.

## Testing Guidelines

- Primary framework is `pytest`. Add regression coverage for bug fixes and include at least one failing/edge-case assertion.
- When iterating, run targeted tests first (example: `./venv/bin/python3 -m pytest tests/test_public_api.py`).

## Commit & Pull Request Guidelines

- Commit messages generally follow Conventional Commits: `feat: …`, `fix(scope): …`, `docs: …`, `chore: …`, `refactor: …`.
- PRs should explain the change, note any operational impact (cron/systemd/Redis/RQ), and include screenshots for template/UI changes.

## Security & Configuration Tips

- Never commit secrets: keep credentials in `.env`/`.secrets/` and verify with `gitleaks` before opening a PR.
- Avoid accidental large binary/DB diffs (for example `blotter.db`) unless the change is intentional and reviewed.
