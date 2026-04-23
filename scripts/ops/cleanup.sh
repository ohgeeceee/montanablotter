#!/bin/bash

# Target directory for old records
TARGET_DIR="/root/montanablotter/records"

# 1. Find and delete files older than 30 days (+30)
# 2. Specifically look for files (-type f) ending in .pdf (-name "*.pdf")
find "$TARGET_DIR" -type f -name "*.pdf" -mtime +30 -delete

# Optional: Log the cleanup action
echo "$(date): Cleaned up records older than 30 days" >> /root/montanablotter/cleanup.log
