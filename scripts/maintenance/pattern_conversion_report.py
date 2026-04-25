import os
import csv
import sqlite3
from datetime import datetime

import config
from db import connect_page_views

DB_PATH = config.DB_PATH
REPORT_DIR = os.path.join(os.path.dirname(__file__), 'reports')


def _row_count(conn, query, params=None):
    params = params or ()
    return conn.execute(query, params).fetchone()[0]


def _fetch_metrics(conn):
    metrics = {}
    metrics['date'] = datetime.utcnow().strftime('%Y-%m-%d')
    metrics['pattern_clicks_30d'] = _row_count(
        conn, "SELECT COUNT(*) FROM pattern_clicks WHERE created_at >= date('now', '-30 days')"
    )
    metrics['homepage_clicks'] = _row_count(
        conn,
        "SELECT COUNT(*) FROM pattern_clicks WHERE placement = 'homepage_pattern_promos' AND created_at >= date('now', '-30 days')",
    )
    metrics['post_clicks'] = _row_count(
        conn,
        "SELECT COUNT(*) FROM pattern_clicks WHERE placement = 'post_related_patterns' AND created_at >= date('now', '-30 days')",
    )
    try:
        pv_conn = connect_page_views()
        metrics['homepage_views'] = _row_count(
            pv_conn, "SELECT COUNT(*) FROM page_views WHERE path = '/' AND created_at >= date('now', '-30 days')"
        )
        metrics['post_views'] = _row_count(
            pv_conn, "SELECT COUNT(*) FROM page_views WHERE path LIKE '/post/%' AND created_at >= date('now', '-30 days')"
        )
        pv_conn.close()
    except Exception:
        metrics['homepage_views'] = 0
        metrics['post_views'] = 0
    metrics['homepage_ctr'] = (
        (metrics['homepage_clicks'] / metrics['homepage_views'] * 100) if metrics['homepage_views'] else 0.0
    )
    metrics['post_ctr'] = (
        (metrics['post_clicks'] / metrics['post_views'] * 100) if metrics['post_views'] else 0.0
    )

    placement_rows = conn.execute(
        '''
        SELECT placement, COUNT(*) AS cnt
        FROM pattern_clicks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY placement
        ORDER BY cnt DESC
        '''
    ).fetchall()
    metrics['placement_breakdown'] = [
        {'placement': row['placement'], 'clicks': row['cnt']} for row in placement_rows
    ]

    return metrics


def _write_csv(metrics):
    os.makedirs(REPORT_DIR, exist_ok=True)
    filename = f"pattern_conversion_{metrics['date']}.csv"
    path = os.path.join(REPORT_DIR, filename)
    with open(path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['metric', 'value'])
        writer.writerow(['report_date', metrics['date']])
        writer.writerow(['pattern_clicks_30d', metrics['pattern_clicks_30d']])
        writer.writerow(['homepage_clicks', metrics['homepage_clicks']])
        writer.writerow(['homepage_views', metrics['homepage_views']])
        writer.writerow(['homepage_ctr_pct', f"{metrics['homepage_ctr']:.2f}"])
        writer.writerow(['post_clicks', metrics['post_clicks']])
        writer.writerow(['post_views', metrics['post_views']])
        writer.writerow(['post_ctr_pct', f"{metrics['post_ctr']:.2f}"])
        for breakdown in metrics['placement_breakdown']:
            writer.writerow(
                [f"placement_{breakdown['placement']}_clicks", breakdown['clicks']]
            )
    return path


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    metrics = _fetch_metrics(conn)
    conn.close()

    csv_path = _write_csv(metrics)
    print(f"Pattern conversion report written to {csv_path}")


if __name__ == '__main__':
    main()
