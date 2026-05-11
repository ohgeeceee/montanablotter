import os
import csv
import json
import sqlite3
import re
from datetime import datetime
import config
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), 'blotter.db')

def get_gemini_model():
    key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None
    genai.configure(api_key=key)
    return genai.GenerativeModel('gemini-flash-latest')

def generate_context(position, salary):
    model = get_gemini_model()
    if not model:
        return "Context unavailable (API key missing)."
        
    prompt = f"Given this position title '{position}' and salary '${salary}' in Montana government, write a 1-sentence context: is this above/below average for this type of role in a government setting? Keep it extremely concise and objective."
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Failed to generate context for {position}: {e}")
        return ""

def slugify(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

def ingest_csv(filepath, year=None):
    if year is None:
        year = datetime.now().year
        
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            # Expected columns: employee_name, agency, position, salary, county
            # Adapt these keys if the MT Transparency Portal CSV has different column names
            name = row.get('employee_name', row.get('Name', '')).strip()
            agency = row.get('agency', row.get('Agency', '')).strip()
            position = row.get('position', row.get('Position', '')).strip()
            raw_salary = row.get('salary', row.get('Salary', '0'))
            county = row.get('county', row.get('County', 'Unknown')).strip()
            
            if not name:
                continue
                
            # Clean salary
            salary_str = re.sub(r'[^\d.]', '', str(raw_salary))
            salary = float(salary_str) if salary_str else 0.0
            
            slug = slugify(f"{name}-{agency}-{year}")
            
            # Check if exists
            cur.execute("SELECT id FROM public_salaries WHERE slug=?", (slug,))
            if cur.fetchone():
                continue
                
            context = generate_context(position, salary)
            
            cur.execute('''
                INSERT INTO public_salaries (name, slug, agency, position, salary, county, year, context)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, slug, agency, position, salary, county, year, context))
            
            count += 1
            if count % 100 == 0:
                print(f"Ingested {count} records...")
                conn.commit()
                
    conn.commit()
    conn.close()
    print(f"Finished ingesting {count} records.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        ingest_csv(sys.argv[1])
    else:
        print("Usage: python salary_ingest.py <path_to_csv>")
