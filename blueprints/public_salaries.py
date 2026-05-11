from flask import Blueprint, request, render_template, redirect, url_for, abort, flash

def register_public_salaries_blueprint(app, get_db):
    salaries_bp = Blueprint('public_salaries', __name__, template_folder='templates')

    @salaries_bp.route('/public-salaries')
    def public_salaries_index():
        conn = get_db()
        cur = conn.cursor()
        
        q = request.args.get('q', '').strip()
        county = request.args.get('county', '').strip()
        page = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page
        
        # Build query
        query = "SELECT * FROM public_salaries WHERE 1=1"
        params = []
        
        if q:
            query += " AND (name LIKE ? OR agency LIKE ? OR position LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
            
        if county:
            query += " AND county = ?"
            params.append(county)
            
        # Count total
        count_query = query.replace("SELECT *", "SELECT COUNT(*)")
        cur.execute(count_query, params)
        total = cur.fetchone()[0]
        
        # Get records
        query += " ORDER BY salary DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        cur.execute(query, params)
        records = [dict(row) for row in cur.fetchall()]
        
        # Get unique counties for filter
        cur.execute("SELECT DISTINCT county FROM public_salaries WHERE county != '' AND county IS NOT NULL ORDER BY county")
        counties = [row[0] for row in cur.fetchall()]
        
        conn.close()
        
        total_pages = (total + per_page - 1) // per_page
        
        return render_template('salaries_index.html',
                               records=records,
                               total=total,
                               page=page,
                               total_pages=total_pages,
                               counties=counties,
                               county=county,
                               q=q)
                               
    @salaries_bp.route('/employee/<slug>')
    def public_salary_detail(slug):
        conn = get_db()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM public_salaries WHERE slug = ?", (slug,))
        row = cur.fetchone()
        
        if not row:
            conn.close()
            abort(404)
            
        record = dict(row)
        
        # Cross-reference with existing data (records table has no 'name' column — search details and summary)
        search_name = f"%{record['name']}%"
        cur.execute(
            "SELECT * FROM records WHERE details LIKE ? OR summary LIKE ? ORDER BY date DESC LIMIT 5",
            (search_name, search_name)
        )
        arrests = [dict(r) for r in cur.fetchall()]
        
        conn.close()
        
        return render_template('salary_detail.html', record=record, arrests=arrests)

    app.register_blueprint(salaries_bp)
