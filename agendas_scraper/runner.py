from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .browser import launch_browser
from .config import load_city_configs
from .factory import create_provider


def run_config(config_path: str, *, city_slug: str = "") -> list[dict]:
    from playwright.sync_api import sync_playwright

    configs = load_city_configs(config_path)
    if city_slug:
        configs = [config for config in configs if config.slug == city_slug]
    if not configs:
        raise ValueError("No city configs matched the requested slug")

    results: list[dict] = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            for config in configs:
                provider = create_provider(config)
                meetings = provider.scrape(browser)
                results.append(
                    {
                        "slug": config.slug,
                        "provider": config.provider,
                        "url": config.url,
                        "meetings": [asdict(meeting) for meeting in meetings],
                    }
                )
        finally:
            browser.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run modular public-meetings scrapers from JSON config.")
    parser.add_argument("config_path", help="Path to the JSON config file")
    parser.add_argument("--city", default="", help="Optional city slug to run a single source")
    args = parser.parse_args()

    results = run_config(args.config_path, city_slug=args.city)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
