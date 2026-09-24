from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CityScrapeConfig:
    slug: str
    name: str
    provider: str
    url: str
    timezone: str = "America/Denver"
    selectors: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _coerce_city_config(raw: dict[str, Any]) -> CityScrapeConfig:
    missing = [key for key in ("slug", "name", "provider", "url") if not str(raw.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Missing required config fields: {', '.join(missing)}")
    return CityScrapeConfig(
        slug=str(raw["slug"]).strip(),
        name=str(raw["name"]).strip(),
        provider=str(raw["provider"]).strip().lower(),
        url=str(raw["url"]).strip(),
        timezone=str(raw.get("timezone") or "America/Denver").strip(),
        selectors=dict(raw.get("selectors") or {}),
        metadata=dict(raw.get("metadata") or {}),
    )


def load_city_configs(path: str | Path) -> list[CityScrapeConfig]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("City scrape config file must be a JSON list")
    return [_coerce_city_config(item) for item in payload]
