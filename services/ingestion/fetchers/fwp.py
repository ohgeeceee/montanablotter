import urllib.request
import json
import sqlite3
import re
from datetime import datetime
import config
from dotenv import load_dotenv
load_dotenv()

import os
import urllib.request

DB_PATH = os.path.join(os.path.dirname(__file__), 'blotter.db')

def fetch_html():
    req = urllib.request.Request("https://fwp.mt.gov/news/allnews", headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        html = response.read().decode('utf-8')
    return html

import google.generativeai as genai

def get_gemini_model():
    key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None
    genai.configure(api_key=key)
    return genai.GenerativeModel('gemini-flash-latest')

def extract_records(html_content):
    model = get_gemini_model()
    if not model:
        print("GEMINI_API_KEY not found. Please add it to your .env file.")
        return []
        
    prompt = "Extract all enforcement actions or violations. Return JSON array of objects: [{name, county, violation, date, fine, license_status, case_number}]"
    
    try:
        response = model.generate_content(f"{prompt}\n\nReturn ONLY valid JSON.\n\n{html_content[:30000]}")
        content = response.text
        print("Raw LLM output:", content[:500])
        match = re.search(r'```(?:json)?\n?(.*?)\n?```', content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        return json.loads(content)
    except Exception as e:
        print("Failed to extract:", e)
        if 'content' in locals():
            print("Content was:", content[:1000])
        return []

def classify_with_hermes(record):
    model = get_gemini_model()
    if not model:
        return "Unclassified", "Summary not available (API key missing)."
        
    prompt = f"Classify this FWP violation by type: [poaching, license, habitat, transport, commercial]. Also write a 2-sentence public summary.\n\nRecord: {json.dumps(record)}\n\nFormat your response as:\nType: <type>\nSummary: <summary>"
    try:
        response = model.generate_content(prompt)
        text = response.text
        type_match = re.search(r'Type:\s*(.*)', text, re.IGNORECASE)
        summary_match = re.search(r'Summary:\s*(.*)', text, re.IGNORECASE | re.DOTALL)
        
        v_type = type_match.group(1).strip() if type_match else "Unclassified"
        summary = summary_match.group(1).strip() if summary_match else text
        return v_type, summary
    except Exception as e:
        print("Classification failed:", e)
        return "Unclassified", "Summary not available."

def save_to_db(record, v_type, summary):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    
    details = json.dumps({
        "name": record.get("name"),
        "fine": record.get("fine"),
        "license_status": record.get("license_status"),
        "case_number": record.get("case_number"),
        "classification": v_type
    })
    
    conn.execute('''
        INSERT INTO records (date, time, location, county, incident_type, incident, status, summary, details, officer)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        record.get("date", datetime.now().strftime("%m/%d/%Y")),
        "00:00",
        "Montana",
        record.get("county", "Unknown"),
        "FWP Violation",
        record.get("violation", "Unknown Violation"),
        "Verified",
        summary,
        details,
        "MT FWP"
    ))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    html = fetch_html()
    records = extract_records(html)
    for rec in records:
        v_type, summary = classify_with_hermes(rec)
        save_to_db(rec, v_type, summary)
        print(f"Saved: {rec.get('name')} - {v_type}")

