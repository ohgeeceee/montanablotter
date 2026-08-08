#!/usr/bin/env bash
# Fetch each county sheriff/jail page and extract contact info + roster/detention language.
# Skips files that already exist (idempotent resume).

set -u
ROOT="/root/montanablotter/docs/research/jail-roster-coverage"
RAW="$ROOT/raw"
mkdir -p "$RAW"

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

while IFS='|' read -r slug url; do
  out="$RAW/${slug}.html"
  if [[ -s "$out" ]]; then
    echo "SKIP $slug (cached)"
    continue
  fi
  echo "FETCH $slug $url"
  code=$(curl -s -o "$out" -w "%{http_code}" --max-time 25 -L -A "$UA" -k "$url" || echo "ERR")
  size=$(stat -c %s "$out" 2>/dev/null || echo 0)
  echo "  -> HTTP $code, $size bytes"
done < "$ROOT/county_urls.txt"
