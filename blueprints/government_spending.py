from __future__ import annotations

import csv
import io
import json
from flask import Blueprint, make_response, render_template, request


def register_government_spending_blueprint(app, get_db):
    bp = Blueprint("government_spending", __name__)
    union_sql = """
        SELECT id, 'contract' AS record_type, agency, vendor, amount, contract_type AS subtype,
               contract_date AS record_date, description, county, analysis_summary, analysis_flags
        FROM government_contracts
        UNION ALL
        SELECT id, 'expenditure' AS record_type, agency, vendor, amount, expenditure_type AS subtype,
               expenditure_date AS record_date, description, county, NULL AS analysis_summary, NULL AS analysis_flags
        FROM government_expenditures
    """

    def _build_where():
        q_agency = (request.args.get("agency") or "").strip()
        q_vendor = (request.args.get("vendor") or "").strip()
        q_county = (request.args.get("county") or "").strip()
        date_from = (request.args.get("date_from") or "").strip()
        date_to = (request.args.get("date_to") or "").strip()
        min_amount = request.args.get("min_amount", type=float)
        max_amount = request.args.get("max_amount", type=float)
        where = ["1=1"]
        params: list = []
        if q_agency:
            where.append("agency LIKE ?")
            params.append(f"%{q_agency}%")
        if q_vendor:
            where.append("vendor LIKE ?")
            params.append(f"%{q_vendor}%")
        if q_county:
            where.append("county = ?")
            params.append(q_county)
        if min_amount is not None:
            where.append("amount >= ?")
            params.append(min_amount)
        if max_amount is not None:
            where.append("amount <= ?")
            params.append(max_amount)
        if date_from:
            where.append("record_date >= ?")
            params.append(date_from)
        if date_to:
            where.append("record_date <= ?")
            params.append(date_to)
        return " AND ".join(where), params, {
            "agency": q_agency,
            "vendor": q_vendor,
            "county": q_county,
            "date_from": date_from,
            "date_to": date_to,
            "min_amount": "" if min_amount is None else min_amount,
            "max_amount": "" if max_amount is None else max_amount,
        }

    @bp.route("/government-spending")
    def government_spending_index():
        conn = get_db()
        where_sql, params, filters = _build_where()
        page = max(1, request.args.get("page", 1, type=int))
        per_page = 50
        offset = (page - 1) * per_page

        total = conn.execute(
            f"SELECT COUNT(1) AS c FROM ({union_sql}) u WHERE {where_sql}",
            params,
        ).fetchone()["c"]

        rows = conn.execute(
            f"""
            SELECT * FROM ({union_sql}) u
            WHERE {where_sql}
            ORDER BY record_date DESC, amount DESC
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()

        leaderboard = conn.execute(
            """
            SELECT vendor, SUM(COALESCE(amount,0)) AS total_amount, COUNT(1) AS record_count
            FROM (
                SELECT vendor, amount FROM government_contracts
                UNION ALL
                SELECT vendor, amount FROM government_expenditures
            )
            GROUP BY vendor
            ORDER BY total_amount DESC
            LIMIT 15
            """
        ).fetchall()

        trend = conn.execute(
            """
            SELECT agency,
                   substr(record_date, 1, 7) AS ym,
                   SUM(COALESCE(amount, 0)) AS total_amount
            FROM (
                SELECT agency, contract_date AS record_date, amount FROM government_contracts
                UNION ALL
                SELECT agency, expenditure_date AS record_date, amount FROM government_expenditures
            )
            WHERE record_date IS NOT NULL AND record_date != ''
            GROUP BY agency, ym
            ORDER BY ym DESC, total_amount DESC
            LIMIT 240
            """
        ).fetchall()
        trend_chart = conn.execute(
            """
            SELECT ym, SUM(total_amount) AS total_amount
            FROM (
                SELECT substr(contract_date, 1, 7) AS ym, SUM(COALESCE(amount,0)) AS total_amount
                FROM government_contracts
                WHERE contract_date IS NOT NULL AND contract_date != ''
                GROUP BY ym
                UNION ALL
                SELECT substr(expenditure_date, 1, 7) AS ym, SUM(COALESCE(amount,0)) AS total_amount
                FROM government_expenditures
                WHERE expenditure_date IS NOT NULL AND expenditure_date != ''
                GROUP BY ym
            )
            GROUP BY ym
            ORDER BY ym
            """
        ).fetchall()

        counties = [
            r["county"]
            for r in conn.execute(
                """
                SELECT DISTINCT county FROM (
                    SELECT county FROM government_contracts
                    UNION
                    SELECT county FROM government_expenditures
                ) WHERE county IS NOT NULL AND county != '' ORDER BY county
                """
            ).fetchall()
        ]
        conn.close()

        return render_template(
            "government_spending.html",
            records=[dict(r) for r in rows],
            total=total,
            page=page,
            total_pages=(total + per_page - 1) // per_page,
            leaderboard=[dict(r) for r in leaderboard],
            trend=[dict(r) for r in trend],
            trend_chart=json.dumps([dict(r) for r in trend_chart]),
            counties=counties,
            filters=filters,
        )

    @bp.route("/government-spending/export.csv")
    def government_spending_export():
        conn = get_db()
        where_sql, params, _ = _build_where()
        rows = conn.execute(
            f"""
            SELECT * FROM ({union_sql}) u
            WHERE {where_sql}
            ORDER BY record_date DESC, amount DESC
            LIMIT 25000
            """,
            params,
        ).fetchall()
        conn.close()

        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            ["record_type", "agency", "vendor", "amount", "subtype", "record_date", "county", "description", "analysis_summary", "analysis_flags"]
        )
        for r in rows:
            d = dict(r)
            w.writerow(
                [
                    d.get("record_type"),
                    d.get("agency"),
                    d.get("vendor"),
                    d.get("amount"),
                    d.get("subtype"),
                    d.get("record_date"),
                    d.get("county"),
                    d.get("description"),
                    d.get("analysis_summary"),
                    d.get("analysis_flags"),
                ]
            )
        resp = make_response(out.getvalue())
        resp.headers["Content-Type"] = "text/csv"
        resp.headers["Content-Disposition"] = "attachment; filename=government_spending_export.csv"
        return resp

    app.register_blueprint(bp)
