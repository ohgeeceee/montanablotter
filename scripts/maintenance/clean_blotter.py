import pdfplumber
import re
import json

def parse_blotter(pdf_path):
    extracted_data = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            
            # This looks for a date pattern like 02/12/26 or 2026-02-12
            # You may need to adjust this pattern based on your specific PDF look
            lines = text.split('\n')
            for line in lines:
                # Example: Looks for "MM/DD/YY" at the start of a line
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', line)
                
                if date_match:
                    date = date_match.group(1)
                    # We assume the rest of the line is the incident
                    incident = line.replace(date, "").strip()
                    
                    extracted_data.append({
                        "date": date,
                        "incident": incident,
                        "location": "Montana" # You can add logic to pull this too
                    })
    
    return extracted_data

# Test it
data = parse_blotter("your_file.pdf")
for entry in data:
    print(f"Found: {entry['date']} | {entry['incident']}")

data = parse_blotter("your_file.pdf")
with open('data.json', 'w') as f:
    json.dump(data, f)