import json
from services.ingestion.dashboard_detector import detect_dashboards_as_dicts

html = '''<script src="https://public.tableau.com/javascripts/api/tableau-2.min.js"></script>
<iframe src="https://app.powerbi.com/view?r=eyJrIjoiabc"></iframe>
<script>var viz = new tableau.Viz(c, "https://public.tableau.com/views/Book/Sheet");</script>'''

print(json.dumps(detect_dashboards_as_dicts(html, "https://sheriff.example.com"), indent=2))
