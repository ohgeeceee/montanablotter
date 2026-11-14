#!/usr/bin/env python3
import json, sys, os
payload_path = sys.argv[1]
p = json.load(open(payload_path))
# validate required fields
required = ["pdf_path", "original_filename", "county", "message_id"]
for r in required:
    if not p.get(r):
        print(json.dumps({"success": False, "error": f"missing {r}"}))
        sys.exit(0)
if not os.path.exists(p["pdf_path"]):
    print(json.dumps({"success": False, "error": "pdf not found"}))
    sys.exit(0)
# simulate success
print(json.dumps({"success": True, "published_id": "mock-123", "county": p["county"], "filename": p["original_filename"]}))
