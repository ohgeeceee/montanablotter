#!/bin/bash
# Reclaim RAM from abandoned Playwright Chromium profiles.
#
# When a Playwright/Chromium run is killed (OOM, timeout, crash) its profile
# directory is left behind under snapd's private tmp. /tmp is a tmpfs, so
# every abandoned profile is RAM that never comes back. On this 3.9 GB box a
# run of crashes piled up 1362 profiles / 1.6 GB, which then caused *more*
# Chromium launches to fail -- a feedback loop that broke every browser-based
# job (agendas_ingest, court_refresh, missing persons).
#
# Only ever runs when no Chromium is alive, and only touches Playwright's own
# profile/temp directories. Safe to schedule daily.
#
# Install: root crontab, e.g.
#   30 4 * * * /opt/montanablotter/scripts/ops/cleanup_playwright_profiles.sh

set -u

TARGET_PARENTS=(
  /tmp/snap-private-tmp/snap.chromium/tmp
  /tmp
)

# Refuse to touch anything while a browser is running.
if pgrep -f 'chromium|playwright' >/dev/null 2>&1; then
  echo "$(date -Is) skipped: chromium/playwright process present"
  exit 0
fi

freed=0
removed=0
for parent in "${TARGET_PARENTS[@]}"; do
  [ -d "$parent" ] || continue
  while IFS= read -r -d '' d; do
    size=$(du -sk "$d" 2>/dev/null | cut -f1)
    if rm -rf "$d" 2>/dev/null; then
      removed=$((removed + 1))
      freed=$((freed + ${size:-0}))
    fi
  done < <(find "$parent" -maxdepth 1 \( -name 'playwright_chromiumdev_profile-*' -o -name 'org.chromium.Chromium.*' \) -print0 2>/dev/null)
done

if [ "$removed" -gt 0 ]; then
  echo "$(date -Is) removed $removed stale profile(s), reclaimed $((freed / 1024)) MB"
else
  echo "$(date -Is) nothing to clean"
fi
