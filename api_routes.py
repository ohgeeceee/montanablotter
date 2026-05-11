
import sqlite3
import json
from datetime import datetime, timedelta
import os

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), 'blotter.db')

def get_db():
    """Get a read-only database connection with optimized settings for large DB."""
    conn = sqlite3.connect(DB_PATH, uri=True)
    conn.row_factory = sqlite3.Row
    # Speed up read queries on large DB
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn

def register_api_routes(app):
    """Register JSON API routes on the existing Flask app."""

    @app.route('/api/stats')
    def api_stats():
        conn = get_db()
        cur = conn.cursor()

        # Total records count
        cur.execute("SELECT COUNT(*) as total FROM records")
        total_records = cur.fetchone()['total']

        # Records in last 24 hours
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%m/%d/%y')
        cur.execute("SELECT COUNT(*) as recent FROM records WHERE date >= ?", (yesterday,))
        recent_records = cur.fetchone()['recent']

        # Active blotters
        cur.execute("SELECT COUNT(*) as active FROM blotters")
        active_blotters = cur.fetchone()['active']

        # Jail bookings
        cur.execute("SELECT COUNT(*) as bookings FROM jail_bookings")
        jail_bookings = cur.fetchone()['bookings']

        # Posts
        cur.execute("SELECT COUNT(*) as posts FROM posts")
        posts_count = cur.fetchone()['posts']

        # County breakdown
        cur.execute("SELECT county, COUNT(*) as count FROM records WHERE county IS NOT NULL AND county != 'Unknown' GROUP BY county ORDER BY count DESC LIMIT 10")
        counties = [dict(r) for r in cur.fetchall()]

        # Incident type breakdown (top 10)
        cur.execute("SELECT incident_type, COUNT(*) as count FROM records WHERE incident_type IS NOT NULL GROUP BY incident_type ORDER BY count DESC LIMIT 10")
        incident_types = [dict(r) for r in cur.fetchall()]

        conn.close()

        return {
            'total_records': total_records,
            'recent_24h': recent_records,
            'active_blotters': active_blotters,
            'jail_bookings': jail_bookings,
            'posts_count': posts_count,
            'counties': counties,
            'incident_types': incident_types,
            'updated_at': datetime.now().isoformat()
        }

    @app.route('/api/records')
    def api_records():
        from flask import request

        conn = get_db()
        cur = conn.cursor()

        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        offset = (page - 1) * per_page

        # Filters
        county = request.args.get('county')
        incident_type = request.args.get('type')
        status = request.args.get('status')
        search = request.args.get('q')

        where_clauses = []
        params = []

        if county:
            where_clauses.append("county = ?")
            params.append(county)
        if incident_type:
            where_clauses.append("incident_type = ?")
            params.append(incident_type)
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if search:
            where_clauses.append("(location LIKE ? OR incident LIKE ? OR summary LIKE ?)")
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

        # Get total count for pagination
        count_sql = f"SELECT COUNT(*) as total FROM records {where_sql}"
        cur.execute(count_sql, params)
        total = cur.fetchone()['total']

        # Get records
        sql = f"SELECT id, date, time, location, county, incident_type, incident, status, summary, details, officer, charge_category FROM records {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
        cur.execute(sql, params + [per_page, offset])
        records = [dict(r) for r in cur.fetchall()]

        conn.close()

        return {
            'records': records,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        }

    @app.route('/api/records/recent')
    def api_records_recent():
        conn = get_db()
        cur = conn.cursor()

        # Get the 50 most recent records
        cur.execute("SELECT id, date, time, location, county, incident_type, incident, status, summary, details, officer, charge_category FROM records ORDER BY id DESC LIMIT 50")
        records = [dict(r) for r in cur.fetchall()]
        conn.close()

        return {'records': records}

    @app.route('/api/fwp-violations')
    def api_fwp_violations():
        from flask import request, jsonify
        conn = get_db()
        cur = conn.cursor()
        
        county = request.args.get('county', '')
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        
        query = "SELECT * FROM records WHERE officer = 'MT FWP'"
        params = []
        
        if county:
            query += " AND county = ?"
            params.append(county)
            
        query += " ORDER BY date DESC, time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        cur.execute(query, params)
        records = [dict(row) for row in cur.fetchall()]
        
        cur.execute("SELECT COUNT(*) FROM records WHERE officer = 'MT FWP'" + (" AND county = ?" if county else ""), [county] if county else [])
        total = cur.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'total': total,
            'limit': limit,
            'offset': offset,
            'records': records
        })

    @app.route('/api/filters')
    def api_filters():
        conn = get_db()
        cur = conn.cursor()

        # Get distinct counties
        cur.execute("SELECT DISTINCT county FROM records WHERE county IS NOT NULL AND county != 'Unknown' ORDER BY county")
        counties = [r['county'] for r in cur.fetchall()]

        # Get distinct incident types
        cur.execute("SELECT DISTINCT incident_type FROM records WHERE incident_type IS NOT NULL ORDER BY incident_type")
        types = [r['incident_type'] for r in cur.fetchall()]

        conn.close()

        return {'counties': counties, 'incident_types': types}

    @app.route('/api/timeline')
    def api_timeline():
        conn = get_db()
        cur = conn.cursor()

        # Daily counts for last 30 days
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%m/%d/%y')
        cur.execute("SELECT date, COUNT(*) as count FROM records WHERE date >= ? GROUP BY date ORDER BY date", (thirty_days_ago,))
        timeline = [dict(r) for r in cur.fetchall()]
        conn.close()

        return {'timeline': timeline}
