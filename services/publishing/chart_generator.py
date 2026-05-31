"""
Generate crime trend charts for daily blog posts.

Produces up to three PNGs saved to static/charts/ and returns
Markdown image lines ready to append to a blog post body.
"""

import os
import sqlite3
from collections import Counter
from datetime import date, timedelta
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless — must be set before importing pyplot
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

_STATIC_CHARTS = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../static/charts")
)

_NAVY = "#1a2744"
_GOLD = "#C9A84C"
_LIGHT = "#f8f7f4"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.facecolor": "#0f1831",
    "figure.facecolor": _NAVY,
    "text.color": "white",
    "axes.labelcolor": "white",
    "xtick.color": "white",
    "ytick.color": "white",
    "axes.edgecolor": "#334155",
    "grid.color": "#334155",
    "grid.linewidth": 0.6,
})


def _out(filename: str) -> str:
    os.makedirs(_STATIC_CHARTS, exist_ok=True)
    return os.path.join(_STATIC_CHARTS, filename)


def _chart_top_counties(county_counts: Counter, analysis_date: str) -> Optional[str]:
    top = county_counts.most_common(10)
    if not top:
        return None
    labels = [c[0] for c in reversed(top)]
    values = [c[1] for c in reversed(top)]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.barh(labels, values, color=_GOLD, height=0.6)
    ax.bar_label(bars, fmt="%d", padding=4, color="white", fontsize=9)
    ax.set_xlabel("Incident reports", fontsize=9, labelpad=6)
    ax.set_title(f"Incidents by County — {analysis_date}", fontsize=11, pad=10, color=_GOLD)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout(pad=1.5)

    path = _out(f"{analysis_date}-counties.png")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


def _chart_top_incidents(incident_counts: Counter, analysis_date: str) -> Optional[str]:
    # filter generic catch-alls so the chart is more informative
    top = [(t, c) for t, c in incident_counts.most_common(14)
           if t.upper() not in ("OTHER", "PATROL")][:10]
    if not top:
        top = incident_counts.most_common(10)
    if not top:
        return None
    labels = [t[0].title() for t in reversed(top)]
    values = [t[1] for t in reversed(top)]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.barh(labels, values, color="#3b82f6", height=0.6)
    ax.bar_label(bars, fmt="%d", padding=4, color="white", fontsize=9)
    ax.set_xlabel("Reports", fontsize=9, labelpad=6)
    ax.set_title(f"Top Incident Types — {analysis_date}", fontsize=11, pad=10, color=_GOLD)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout(pad=1.5)

    path = _out(f"{analysis_date}-incidents.png")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


def _chart_7day_trend(conn: sqlite3.Connection, analysis_date: str) -> Optional[str]:
    end = date.fromisoformat(analysis_date)
    start = end - timedelta(days=6)

    rows = conn.execute(
        """
        SELECT date, COUNT(*) AS n
        FROM records
        WHERE date >= ? AND date <= ?
        GROUP BY date
        ORDER BY date ASC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()

    if not rows or len(rows) < 2:
        return None

    day_map = {r["date"]: r["n"] for r in rows}
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    counts = [day_map.get(d, 0) for d in days]
    labels = [(start + timedelta(days=i)).strftime("%-m/%-d") for i in range(7)]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.plot(labels, counts, color=_GOLD, linewidth=2.5, marker="o",
            markersize=6, markerfacecolor=_GOLD)
    ax.fill_between(labels, counts, alpha=0.15, color=_GOLD)
    ax.set_ylabel("Reports", fontsize=9)
    ax.set_title("Montana Law Enforcement Activity — 7-Day Window",
                 fontsize=11, pad=10, color=_GOLD)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.grid(axis="y")
    fig.tight_layout(pad=1.5)

    path = _out(f"{analysis_date}-trend.png")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


def generate(
    county_counts: Counter,
    incident_counts: Counter,
    analysis_date: str,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """
    Generate up to three charts and return a Markdown snippet to embed in a blog post.
    Returns empty string if no charts could be produced.
    """
    try:
        import config
        base_url = (getattr(config, "BASE_URL", "") or "https://montanablotter.com").rstrip("/")
    except Exception:
        base_url = "https://montanablotter.com"

    lines = []

    try:
        county_path = _chart_top_counties(county_counts, analysis_date)
        if county_path:
            fname = os.path.basename(county_path)
            lines.append(f"![Incidents by county — {analysis_date}]({base_url}/static/charts/{fname})")
    except Exception:
        pass

    try:
        incident_path = _chart_top_incidents(incident_counts, analysis_date)
        if incident_path:
            fname = os.path.basename(incident_path)
            lines.append(f"![Top incident types — {analysis_date}]({base_url}/static/charts/{fname})")
    except Exception:
        pass

    if conn is not None:
        try:
            trend_path = _chart_7day_trend(conn, analysis_date)
            if trend_path:
                fname = os.path.basename(trend_path)
                lines.append(f"![7-day activity trend]({base_url}/static/charts/{fname})")
        except Exception:
            pass

    if not lines:
        return ""
    return "\n\n---\n\n## Activity Charts\n\n" + "\n\n".join(lines) + "\n"
