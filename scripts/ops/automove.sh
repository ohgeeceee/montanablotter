#!/bin/bash

# Folder to watch (e.g., where you upload files via SFTP)
WATCH_DIR="/root/incoming_records"
# Folder where your Montana Blotter app looks for records
TARGET_DIR="/root/montanablotter/records"

# Ensure both directories exist
mkdir -p "$WATCH_DIR"
mkdir -p "$TARGET_DIR"

echo "Watching $WATCH_DIR for new PDFs..."

# Listen for 'create' or 'moved_to' events
inotifywait -m -e create -e moved_to --format "%f" "$WATCH_DIR" | while read FILENAME
do
    # Only move PDF files
    if [[ "$FILENAME" == *.pdf ]]; then
        echo "Detected $FILENAME. Moving to portal..."
        mv "$WATCH_DIR/$FILENAME" "$TARGET_DIR/"
        
        # Adjust permissions so the web app can read it
        chown root:www-data "$TARGET_DIR/$FILENAME"
        chmod 664 "$TARGET_DIR/$FILENAME"
    fi
done
