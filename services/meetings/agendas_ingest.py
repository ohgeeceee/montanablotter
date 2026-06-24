from __future__ import annotations

import argparse
import json

import config
from agendas_scraper.browser import launch_browser
from agendas_scraper.config import load_city_configs
from agendas_scraper.factory import create_provider
from db import connect_db
from services.meetings.public import (
    ensure_public_meeting_schema,
    record_source_scrape_error,
    sync_scraped_meetings,
)


def ingest_configs(config_path: str, *, city_slug: str = '') -> list[dict]:
    from playwright.sync_api import sync_playwright

    results: list[dict] = []
    configs = load_city_configs(config_path)
    if city_slug:
        configs = [item for item in configs if item.slug == city_slug]
    if not configs:
        raise ValueError('No city configs matched the requested slug')

    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            for city_config in configs:
                provider = create_provider(city_config)
                conn = connect_db()
                ensure_public_meeting_schema(conn)
                try:
                    meetings = provider.scrape(browser)
                    summary = sync_scraped_meetings(
                        conn,
                        city_config,
                        meetings,
                        config_path=config_path,
                    )
                    conn.commit()
                    results.append(
                        {
                            'slug': city_config.slug,
                            'provider': city_config.provider,
                            'total': summary['total'],
                            'created': summary['created'],
                            'updated': summary['updated'],
                            'status': 'ok',
                        }
                    )
                except Exception as exc:
                    record_source_scrape_error(
                        conn,
                        city_config,
                        str(exc),
                        config_path=config_path,
                    )
                    conn.commit()
                    results.append(
                        {
                            'slug': city_config.slug,
                            'provider': city_config.provider,
                            'status': 'error',
                            'error': str(exc),
                        }
                    )
                finally:
                    conn.close()
        finally:
            browser.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description='Scrape Montana public meetings and store them in SQLite.')
    parser.add_argument(
        'config_path',
        nargs='?',
        default=getattr(config, 'AGENDAS_CONFIG_PATH', 'configs/agendas/cities.example.json'),
        help='Path to the JSON config file',
    )
    parser.add_argument('--city', default='', help='Optional city slug to ingest a single source')
    parser.add_argument('--json', action='store_true', help='Print machine-readable JSON output')
    args = parser.parse_args()

    results = ingest_configs(args.config_path, city_slug=args.city)
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            if result['status'] == 'ok':
                print(
                    f"{result['slug']}: {result['total']} meetings "
                    f"({result['created']} created, {result['updated']} updated)"
                )
            else:
                print(f"{result['slug']}: error - {result['error']}")

    # A single flaky city/site should not fail the whole batch. Only treat the
    # run as failed if every source failed or we got no results at all.
    ok_count = sum(1 for item in results if item['status'] == 'ok')
    error_count = len(results) - ok_count
    if not results or ok_count == 0:
        return 1
    if error_count:
        print(f"\nWARNING: {error_count}/{len(results)} source(s) failed, but at least one succeeded. Run considered OK.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
