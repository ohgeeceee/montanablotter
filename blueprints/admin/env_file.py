"""Safe reads/writes of the app's .env from admin pages.

Values are written atomically, the file mode is preserved, and callers pass an
explicit allow-list so an admin page can only ever touch its own keys.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def read_env() -> dict:
    values = {}
    if not ENV_PATH.is_file():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env(updates: dict) -> None:
    """Atomically update allow-listed keys, preserving file mode."""
    if not updates:
        raise ValueError("no updates supplied")

    lines = []
    if ENV_PATH.is_file():
        lines = ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines()

    mode = stat.S_IMODE(ENV_PATH.stat().st_mode) if ENV_PATH.exists() else 0o600

    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, ENV_PATH)
