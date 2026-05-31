#!/bin/bash

# Target directory for old records
TARGET_DIR="/root/montanablotter/records"

# 1. Find and delete files older than 30 days (+30)
# 2. Specifically look for files (-type f) ending in .pdf (-name "*.pdf")
find "$TARGET_DIR" -type f -name "*.pdf" -mtime +30 -delete

# Optional: Log the cleanup action
echo "$(date): Cleaned up records older than 30 days" >> /root/montanablotter/logs/cleanup.log

# Delete log files older than 14 days (keeps recent history, prevents unbounded growth)
find /root/montanablotter/logs -maxdepth 1 -type f -name "*.log" -mtime +14 -delete
echo "$(date): Rotated logs older than 14 days" >> /root/montanablotter/logs/cleanup.log

# Safety valve: truncate any log file that has grown over 50MB
find /root/montanablotter/logs -maxdepth 1 -type f -name "*.log" -size +50M -print | while read -r f; do
    truncate -s 0 "$f"
    echo "$(date): Truncated oversized log: $f" >> /root/montanablotter/logs/cleanup.log
done
