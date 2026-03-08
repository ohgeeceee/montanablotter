"""
PDF Parser for Montana Sheriff's Office Blotters
Handles GCSO format and adaptable for other counties
"""

import pdfplumber
import re
from datetime import datetime
from typing import List, Dict, Optional

class BlotterParser:
    """Parse police blotter PDFs into structured data"""

    def __init__(self, pdf_path: str):
        self.pdf_path = pdf_path
        self.county = None
        self.incidents = []

    def _extract_text(self) -> str:
        """Extract raw text from the PDF, falling back to OCR for image-based PDFs."""
        full_text = ""
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

        if not full_text.strip():
            # No embedded text — try OCR
            try:
                from pdf2image import convert_from_path
                import pytesseract
                pages = convert_from_path(self.pdf_path, dpi=200)
                for page in pages:
                    full_text += pytesseract.image_to_string(page, config='--psm 6') + "\n"
            except Exception as e:
                import logging
                logging.warning(f"OCR failed for {self.pdf_path}: {e}")

        return full_text

    def _parse_text(self, full_text: str) -> Dict:
        """Shared parsing logic given raw text. Returns structured data dict."""
        self.county = self._detect_county(full_text)

        if "GCSO" in full_text or "Gallatin County" in full_text:
            self.incidents = self._parse_gcso_format(full_text)
        elif re.search(r'Helena Police|HPD Officers responded|helenamt\.gov', full_text, re.IGNORECASE):
            self.incidents = self._parse_helena_format(full_text)
        elif re.search(r'Daily Incidents', full_text, re.IGNORECASE) and (
            re.search(r'\bWF\b', full_text) or re.search(r'Whitefish', full_text, re.IGNORECASE)
        ):
            self.incidents = self._parse_whitefish_format(full_text)
        elif re.search(r'HAVRE POLICE|For Jurisdiction:\s*HAVRE', full_text, re.IGNORECASE):
            self.incidents = self._parse_havre_format(full_text)
        else:
            self.incidents = self._parse_generic_format(full_text)

        return {
            'county': self.county,
            'incidents': self.incidents,
            'total_count': len(self.incidents)
        }

    def parse(self) -> Dict:
        """Main parsing method - returns structured data"""
        full_text = self._extract_text()
        return self._parse_text(full_text)
    
    def _detect_county(self, text: str) -> str:
        """Extract county name from PDF header"""
        # Helena Police Department is in Lewis and Clark County
        if re.search(r'Helena Police|helenamt\.gov|Helena Police Department', text, re.IGNORECASE):
            return "Lewis and Clark"

        # Whitefish Police Department is in Flathead County
        if re.search(r'Daily Incidents', text, re.IGNORECASE) and (
            re.search(r'\bWF\b', text) or re.search(r'Whitefish', text, re.IGNORECASE)
        ):
            return "Flathead"

        # Havre Police Department is in Hill County
        if re.search(r'HAVRE POLICE|For Jurisdiction:\s*HAVRE', text, re.IGNORECASE):
            return "Hill"

        county_patterns = [
            r"(\w+)\s+County\s+Sheriff",
            r"GCSO",  # Gallatin County Sheriff's Office
            r"(\w+)\s+County",
        ]

        for pattern in county_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if pattern == r"GCSO":
                    return "Gallatin"
                return match.group(1)

        return "Unknown"
    
    def _parse_gcso_format(self, text: str) -> List[Dict]:
        """Parse GCSO-specific format with CFS numbers and command logs"""
        incidents = []
        
        # Pattern to find incident blocks
        # GCSO format: MM/DD/YY HH:MM:SS CFS26-XXXXXX LOCATION CODE
        incident_pattern = r'(\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+(CFS\d{2}-\d+)\s+(.+?)\s+(\w+(?:\s+\w+)?)\s*$'
        
        lines = text.split('\n')
        current_incident = None
        command_logs = []
        
        for i, line in enumerate(lines):
            # Skip header lines
            if 'CFS Date/Time' in line or 'Command Log' in line or 'Page' in line:
                continue
            
            # Check if this is a new incident header
            match = re.match(incident_pattern, line.strip())
            if match:
                # Save previous incident if exists
                if current_incident:
                    current_incident['command_logs'] = command_logs
                    current_incident['details'] = self._extract_narrative(command_logs)
                    incidents.append(current_incident)
                
                # Start new incident
                date_time, cfs_num, location, code = match.groups()
                date_parts = date_time.split()
                
                current_incident = {
                    'cfs_number': cfs_num.strip(),
                    'date': date_parts[0],
                    'time': date_parts[1],
                    'location': location.strip(),
                    'incident_type': code.strip(),
                    'officer': None
                }
                command_logs = []
            
            # Check if this is a command log entry
            elif current_incident and re.match(r'\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+-\s+', line):
                # Parse command log: "02/11/26 01:34:33 - Alexander, Logan - Details..."
                log_match = re.match(r'(\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+-\s+([\w,\s]+)\s+-\s+(.+)', line)
                if log_match:
                    timestamp, officer, entry = log_match.groups()
                    command_logs.append({
                        'timestamp': timestamp.strip(),
                        'officer': officer.strip(),
                        'entry': entry.strip()
                    })
                    # Set primary officer if not set
                    if not current_incident['officer']:
                        current_incident['officer'] = officer.strip()
        
        # Don't forget the last incident
        if current_incident:
            current_incident['command_logs'] = command_logs
            current_incident['details'] = self._extract_narrative(command_logs)
            incidents.append(current_incident)
        
        return incidents
    
    def _extract_narrative(self, command_logs: List[Dict]) -> str:
        """Extract the main narrative from command logs"""
        if not command_logs:
            return ""
        
        # The narrative is usually the longest entry or entries with actual incident details
        narratives = []
        for log in command_logs:
            entry = log['entry']
            # Skip technical dispatch entries
            if len(entry) > 50 and not any(skip in entry.upper() for skip in ['CB1', 'CB2', 'NO ANSWER', 'VM', 'ADV']):
                narratives.append(entry)
        
        return " ".join(narratives) if narratives else (command_logs[-1]['entry'] if command_logs else "")
    
    def _parse_helena_format(self, text: str) -> List[Dict]:
        """Parse Helena Police Department press release format.

        Handles two variants:
          Format 1: '8:20 AM – A theft was reported near the 3100 block of...'
          Format 2: '1008 hours, an Officer responded to the 1800 block of...'
        """
        incidents = []

        # Extract date from email body
        date_str = None
        month_match = re.search(
            r'(January|February|March|April|May|June|July|August|September|October|November|December)'
            r'\s+(\d{1,2}),?\s+(\d{4})', text, re.IGNORECASE)
        if month_match:
            try:
                dt = datetime.strptime(
                    f"{month_match.group(1)} {month_match.group(2)} {month_match.group(3)}", "%B %d %Y")
                date_str = dt.strftime('%m/%d/%y')
            except ValueError:
                pass
        if not date_str:
            slash_match = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\b', text)
            if slash_match:
                try:
                    dt = datetime.strptime(slash_match.group(1), '%m/%d/%Y')
                    date_str = dt.strftime('%m/%d/%y')
                except ValueError:
                    pass
        if not date_str:
            date_str = datetime.now().strftime('%m/%d/%y')

        # Format 1: "8:20 AM – Description"
        # The dash separator may be en-dash, em-dash, or a replacement char
        fmt1 = re.compile(
            r'^(\d{1,2}:\d{2}\s+[AP]M)\s+\S\s+(.+)$',
            re.IGNORECASE | re.MULTILINE)
        for m in fmt1.finditer(text):
            time_val = m.group(1).strip()
            description = m.group(2).strip()
            incidents.append({
                'cfs_number': None,
                'date': date_str,
                'time': time_val,
                'location': self._extract_hpd_location(description),
                'incident_type': self._classify_hpd_incident(description),
                'details': description,
                'officer': None,
                'command_logs': [],
            })

        # Format 2: "1008 hours, an Officer responded to..."  (military time bullets)
        if not incidents:
            fmt2 = re.compile(r'^(\d{4})\s+hours?,\s+(.+?)(?=^\d{4}\s+hours?|$)',
                              re.IGNORECASE | re.MULTILINE | re.DOTALL)
            for m in fmt2.finditer(text):
                raw_time = m.group(1)
                description = re.sub(r'\s+', ' ', m.group(2)).strip()
                try:
                    dt = datetime.strptime(raw_time, '%H%M')
                    time_val = dt.strftime('%-I:%M %p')
                except ValueError:
                    time_val = raw_time
                incidents.append({
                    'cfs_number': None,
                    'date': date_str,
                    'time': time_val,
                    'location': self._extract_hpd_location(description),
                    'incident_type': self._classify_hpd_incident(description),
                    'details': description,
                    'officer': None,
                    'command_logs': [],
                })

        return incidents

    @staticmethod
    def _extract_hpd_location(description: str) -> str:
        """Pull 'XXXX block of Street' from HPD incident description."""
        m = re.search(
            r'(?:near|to|at|around)\s+(?:the\s+)?(\d+\s+block\s+of\s+[\w\s]+?'
            r'(?:St|Ave|Blvd|Dr|Rd|Ln|Way|Circle|Gulch|Ct|Pl|Hwy|Highway)\.?)',
            description, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return "Helena, MT"

    @staticmethod
    def _classify_hpd_incident(description: str) -> str:
        """Derive a short incident type label from free-text description."""
        d = description.lower()
        if any(w in d for w in ['theft', 'shoplift', 'stolen']):
            return 'Theft'
        if 'assault' in d:
            return 'Assault'
        if 'domestic' in d:
            return 'Domestic Disturbance'
        if 'warrant' in d:
            return 'Warrant Arrest'
        if any(w in d for w in ['accident', 'crash', 'collision']):
            return 'Accident'
        if 'trespass' in d:
            return 'Trespassing'
        if any(w in d for w in ['drug', 'marijuana', 'mip', 'narcotic']):
            return 'Drug/Narcotic'
        if any(w in d for w in ['disturbance', 'disorderly']):
            return 'Disturbance'
        if any(w in d for w in ['protection order', 'protective order']):
            return 'Protection Order'
        if any(w in d for w in ['welfare check', 'welfare']):
            return 'Welfare Check'
        if any(w in d for w in ['suspicious', 'suspicious person']):
            return 'Suspicious Activity'
        if 'fraud' in d:
            return 'Fraud'
        if 'vehicle' in d:
            return 'Vehicle'
        return 'Police Incident'

    @staticmethod
    def _clean_ocr_artifacts(text: str) -> str:
        """Remove common OCR artifacts from table-border characters."""
        # Remove isolated pipe/bang/colon/bracket chars that are table borders
        cleaned = re.sub(r'(?<!\w)[|!{}](?!\w)', ' ', text)
        cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
        return cleaned

    def _parse_havre_format(self, text: str) -> List[Dict]:
        """Parse Havre Police Department dispatch log format.

        Format per line:
          26-2080 0737 COMPLAINT C- NTA ISSUED WITH REPORT
          Location/Address: [HAV 433] SOME PLACE - 4TH ST
          Narrative:
          brief description
        """
        incidents = []

        # Extract date from header
        date_str = None
        date_match = re.search(r'For Date:\s*(\d{2}/\d{2}/\d{4})', text)
        if date_match:
            try:
                dt = datetime.strptime(date_match.group(1), '%m/%d/%Y')
                date_str = dt.strftime('%m/%d/%y')
            except ValueError:
                pass
        if not date_str:
            date_str = datetime.now().strftime('%m/%d/%y')

        # Split into per-incident blocks at each call number
        blocks = re.split(r'\n(?=\d{2}-\d{4}\s)', text)

        for block in blocks:
            if not block.strip():
                continue

            lines = [l.strip() for l in block.splitlines() if l.strip()]
            if not lines:
                continue

            # First line: "26-2080 O737 COMPLAINT C- NTA ISSUED WITH REPORT"
            m = re.match(
                r'^(\d{2}-\d{4})\s+([0O]?\d{3,4})\s*(.*)',
                lines[0])
            if not m:
                continue

            call_num = m.group(1)
            time_raw = m.group(2).replace('O', '0').replace('o', '0')
            rest = m.group(3).strip()

            # Convert military time → 12-hour
            try:
                if len(time_raw) == 3:
                    time_raw = '0' + time_raw
                dt = datetime.strptime(time_raw, '%H%M')
                time_val = dt.strftime('%-I:%M %p')
            except ValueError:
                time_val = time_raw

            # Split rest into incident type and action code
            # Action codes look like "C- ...", "J- ...", "L- ...", etc.
            action = ''
            incident_type = rest
            action_m = re.search(r'\s+([A-Z]-\s+.+)$', rest)
            if action_m:
                action = action_m.group(1).strip()
                incident_type = rest[:action_m.start()].strip()

            # If no incident type found on first line, check second non-meta line
            if not incident_type:
                for line in lines[1:4]:
                    if not re.match(
                        r'^(Location|Narrative|Calling|Involved|Refer|Arrest|'
                        r'Summons|Address|Age|Charges|Page)[\s:/]',
                            line, re.IGNORECASE):
                        incident_type = line
                        break

            # Extract location
            location = 'Havre, MT'
            for line in lines:
                loc_m = re.match(r'Location(?:/Address)?:\s*(.+)', line, re.IGNORECASE)
                if loc_m:
                    loc = loc_m.group(1).strip()
                    loc = re.sub(r'[\[{]HAV[^\]}\s]*[\]}]?\s*', '', loc)  # remove [HAV xxx] codes
                    loc = self._clean_ocr_artifacts(loc).strip(' -|~')
                    if loc:
                        location = loc
                    break

            # Extract narrative (lines after "Narrative:" up to next meta field)
            narr_lines = []
            in_narr = False
            for line in lines:
                if re.match(r'^Narrative:', line, re.IGNORECASE):
                    in_narr = True
                    after = re.sub(r'^Narrative:\s*', '', line, flags=re.IGNORECASE).strip()
                    if after:
                        narr_lines.append(after)
                    continue
                if in_narr:
                    if re.match(
                        r'^(Refer To|Arrest:|Summons|Charges:|Age:|Address:|'
                        r'Calling Party:|Involved Party:|For Date:)',
                            line, re.IGNORECASE):
                        break
                    narr_lines.append(line)
            narrative = ' '.join(narr_lines).strip()

            details = narrative if narrative else incident_type
            if action:
                details = f"{details} ({action})" if details else action
            # Strip page headers that bleed into narrative via OCR
            details = re.sub(
                r'HAVRE POLICE DEPT\w*\s+Page:.*?Printed:\s*\d{2}/\d{2}/\d{4}',
                '', details, flags=re.IGNORECASE | re.DOTALL)
            details = self._clean_ocr_artifacts(details)

            incidents.append({
                'cfs_number': call_num,
                'date': date_str,
                'time': time_val,
                'location': location,
                'incident_type': incident_type.title() if incident_type else 'Police Incident',
                'details': details,
                'officer': None,
                'command_logs': [],
            })

        return incidents

    def _parse_whitefish_format(self, text: str) -> List[Dict]:
        """Parse Whitefish PD Daily Incidents PDF format."""
        incidents = []
        clean_text = text.replace('\r', '')
        starts = [m.start() for m in re.finditer(r'(?m)^\d{4}-\s+\d{2}/\d{2}/\d{4}\b', clean_text)]
        if not starts:
            return incidents

        starts.append(len(clean_text))
        for idx in range(len(starts) - 1):
            block = clean_text[starts[idx]:starts[idx + 1]].strip()
            if not block:
                continue

            date_match = re.search(r'\b(\d{2}/\d{2}/\d{4})\b', block)
            cfs_match = re.search(r'\b(\d{8})\s+(\d{2}:\d{2}:\d{2})\b', block)
            if not date_match or not cfs_match:
                continue

            raw_date = date_match.group(1)
            try:
                date_value = datetime.strptime(raw_date, '%m/%d/%Y').strftime('%m/%d/%y')
            except ValueError:
                date_value = raw_date

            incident_type = self._extract_whitefish_incident_type(block)
            location = self._extract_whitefish_location(block, incident_type)
            details = self._clean_whitefish_details(block, cfs_match.group(0))

            incidents.append({
                'cfs_number': cfs_match.group(1),
                'date': date_value,
                'time': cfs_match.group(2),
                'location': location,
                'incident_type': incident_type,
                'details': details,
                'officer': None,
                'command_logs': [],
            })

        return incidents

    @staticmethod
    def _extract_whitefish_incident_type(block: str) -> str:
        via_number_line = re.search(
            r'\b\d{8}\s+\d{2}:\d{2}:\d{2}\s+([A-Za-z][A-Za-z /&-]{1,40}?);',
            block,
        )
        if via_number_line:
            incident_type = via_number_line.group(1).strip()
            return incident_type.title() if incident_type.isupper() else incident_type

        first_line = block.splitlines()[0] if block.splitlines() else block
        via_header = re.search(r'\d{2}/\d{2}/\d{4}\s+([A-Za-z][A-Za-z /&-]{1,30})', first_line)
        if via_header:
            incident_type = via_header.group(1).strip()
            return incident_type.title() if incident_type.isupper() else incident_type
        return 'Police Incident'

    @staticmethod
    def _extract_whitefish_location(block: str, incident_type: str) -> str:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for line in lines:
            normalized = ' '.join(line.split())
            if re.search(r'^\d{8}\s+\d{2}:\d{2}:\d{2}\b', normalized):
                continue
            if re.search(r'^\d{4}-\s+\d{2}/\d{2}/\d{4}\b', normalized):
                normalized = re.sub(r'^\d{4}-\s+\d{2}/\d{2}/\d{4}\s*', '', normalized)
                type_prefix = re.escape(incident_type or '')
                if type_prefix:
                    normalized = re.sub(rf'^{type_prefix}\b', '', normalized, flags=re.IGNORECASE).strip()
            normalized = re.sub(r'\bW\d{2}\b;?', '', normalized).strip(' -;')
            if ' - ' in normalized:
                normalized = normalized.split(' - ', 1)[0].strip()
            if ';' in normalized and len(normalized.split(';', 1)[0]) < 90:
                normalized = normalized.split(';', 1)[0].strip()
            if re.search(
                r'\b(AVE|ST|RD|DR|LN|PKWY|HWY|HIGHWAY|WAY|BLVD|TRAIL|TR|CT|PL|BEACH|DISTRICT|POST|PUMP)\b',
                normalized,
                re.IGNORECASE,
            ):
                return normalized[:160]
        return 'Whitefish, MT'

    @staticmethod
    def _clean_whitefish_details(block: str, cfs_time_marker: str) -> str:
        details = ' '.join(ln.strip() for ln in block.splitlines() if ln.strip())
        details = re.sub(r'^\d{4}-\s+\d{2}/\d{2}/\d{4}\s*', '', details)
        details = details.replace(cfs_time_marker, ' ')
        details = re.sub(r'\bW\d{2}\b;?', ' ', details)
        details = re.sub(r'\s{2,}', ' ', details).strip(' ;')
        return details

    def _parse_generic_format(self, text: str) -> List[Dict]:
        """Fallback parser for non-GCSO formats"""
        incidents = []
        
        # Generic date-based parsing
        # Look for lines starting with dates
        lines = text.split('\n')
        
        for line in lines:
            # Match MM/DD/YY or YYYY-MM-DD at start of line
            date_match = re.match(r'^(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})', line)
            if date_match:
                date = date_match.group(1)
                rest = line[len(date):].strip()
                
                # Try to extract incident type and details
                parts = rest.split('-', 1)
                if len(parts) >= 2:
                    incident_type = parts[0].strip()
                    details = parts[1].strip()
                else:
                    incident_type = "Unknown"
                    details = rest
                
                incidents.append({
                    'cfs_number': None,
                    'date': date,
                    'time': None,
                    'location': "Unknown",
                    'incident_type': incident_type,
                    'details': details,
                    'officer': None,
                    'command_logs': []
                })
        
        return incidents


def parse_text_blotter(text: str) -> dict:
    """Parse a blotter from raw text (email body) without pdfplumber."""
    parser = BlotterParser.__new__(BlotterParser)
    parser.pdf_path = None
    parser.county = None
    parser.incidents = []
    return parser._parse_text(text)


def test_parser(pdf_path: str):
    """Test the parser with a PDF file"""
    parser = BlotterParser(pdf_path)
    result = parser.parse()
    
    print(f"\n{'='*60}")
    print(f"County: {result['county']}")
    print(f"Total Incidents: {result['total_count']}")
    print(f"{'='*60}\n")
    
    for i, incident in enumerate(result['incidents'][:5], 1):  # Show first 5
        print(f"Incident #{i}")
        print(f"  CFS: {incident.get('cfs_number', 'N/A')}")
        print(f"  Date/Time: {incident['date']} {incident.get('time', '')}")
        print(f"  Type: {incident['incident_type']}")
        print(f"  Location: {incident.get('location', 'N/A')}")
        print(f"  Officer: {incident.get('officer', 'N/A')}")
        print(f"  Details: {incident['details'][:100]}...")
        print(f"  Command Logs: {len(incident.get('command_logs', []))} entries")
        print()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_parser(sys.argv[1])
    else:
        print("Usage: python pdf_parser.py <path_to_pdf>")
