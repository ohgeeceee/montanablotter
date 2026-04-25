import time
import requests
import random
from datetime import datetime

# URL of the local Mission Control heartbeat endpoint
HEARTBEAT_URL = "http://127.0.0.1:5000/admin/api/mission-control/heartbeat"
HEADERS = {
    "X-Internal-Mission-Control": "local",
    "Content-Type": "application/json"
}

AGENT_ID = "demo_bot_001"
DISPLAY_NAME = "Demo Bot"

def send_heartbeat(state, task, step_label="", last_tool=""):
    payload = {
        "agent_id": AGENT_ID,
        "display_name": DISPLAY_NAME,
        "runtime": "python-script",
        "state": state,
        "current_task": task,
        "step_label": step_label,
        "last_tool": last_tool,
        "source_kind": "heartbeat",
    }
    
    try:
        response = requests.post(HEARTBEAT_URL, headers=HEADERS, json=payload, timeout=2)
        if response.status_code != 202:
            print(f"[{datetime.now().isoformat()}] Failed to send heartbeat: {response.text}")
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Error connecting to Mission Control: {e}")

def main():
    print(f"Starting {DISPLAY_NAME} ({AGENT_ID})")
    print("This agent will simulate various states and report to Mission Control.")
    
    # 1. Start in Ready state
    print("State: READY")
    send_heartbeat("ready", "Waiting for assignment in queue.")
    time.sleep(4)
    
    # 2. Move to Working state
    print("State: WORKING")
    send_heartbeat("working", "Analyzing new crime report data.", step_label="data_parsing")
    
    # Simulate processing with a few working updates
    for i in range(3):
        time.sleep(3)
        send_heartbeat("working", f"Extracting entities from paragraph {i+1}/3...", step_label="extraction")
        
    # 3. Use a tool
    print("State: TOOL_RUN")
    send_heartbeat("tool_run", "Looking up address in database...", last_tool="db_query")
    time.sleep(4)
    
    # 4. Working again
    print("State: WORKING")
    send_heartbeat("working", "Formatting extracted entities for database insert.", step_label="formatting")
    time.sleep(3)
    
    # 5. Simulate being blocked/error occasionally, or waiting
    if random.choice([True, False]):
        print("State: WAITING")
        send_heartbeat("waiting", "Waiting for database lock to release.", step_label="db_insert")
        time.sleep(4)
    else:
        print("State: BLOCKED")
        send_heartbeat("blocked", "Encountered unknown location format. Retrying...", step_label="geocoding")
        time.sleep(4)
        
    # 6. Finish the job
    print("State: DONE")
    send_heartbeat("done", "Successfully inserted record into database.")
    print("Agent simulation complete.")

if __name__ == "__main__":
    main()
