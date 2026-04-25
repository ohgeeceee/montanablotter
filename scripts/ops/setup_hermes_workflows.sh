#!/usr/bin/env bash
set -euo pipefail

HERMES_BIN="${HERMES_BIN:-/root/.local/bin/hermes}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_SCRIPT_DIR="${HERMES_HOME}/scripts"

if [[ ! -x "$HERMES_BIN" ]]; then
  echo "Hermes binary not found at $HERMES_BIN" >&2
  exit 1
fi

mkdir -p "$HERMES_SCRIPT_DIR"

install_context_script() {
  local source_name="$1"
  local target_name="$2"
  cp "/root/montanablotter/scripts/ops/${source_name}" "${HERMES_SCRIPT_DIR}/${target_name}"
  chmod +x "${HERMES_SCRIPT_DIR}/${target_name}"
}

install_context_script "hermes_context_health.py" "mb_context_health.py"
install_context_script "hermes_context_ingestion.py" "mb_context_ingestion.py"
install_context_script "hermes_context_growth.py" "mb_context_growth.py"
install_context_script "hermes_context_editorial.py" "mb_context_editorial.py"
install_context_script "hermes_context_source_coverage.py" "mb_context_source_coverage.py"
install_context_script "hermes_context_privacy_quality.py" "mb_context_privacy_quality.py"
install_context_script "hermes_context_revenue.py" "mb_context_revenue.py"

echo "Creating Hermes workflows for montanablotter..."

remove_job_by_name() {
  local name="$1"
  local ids
  ids="$("$HERMES_BIN" cron list --all | awk -v n="$name" '$1 ~ /^[0-9a-f]{12}$/ {id=$1} $1=="Name:" && $2==n {print id}')"
  if [[ -n "$ids" ]]; then
    while IFS= read -r id; do
      [[ -n "$id" ]] || continue
      "$HERMES_BIN" cron remove "$id" >/dev/null
    done <<< "$ids"
  fi
}

remove_job_by_name "mb_ops_pulse_daily"
remove_job_by_name "mb_ingestion_triage_hourly"
remove_job_by_name "mb_growth_brief_weekly"
remove_job_by_name "mb_editorial_brief_daily"
remove_job_by_name "mb_source_coverage_daily"
remove_job_by_name "mb_privacy_quality_daily"
remove_job_by_name "mb_revenue_strategy_weekly"

"$HERMES_BIN" cron create "20 8 * * *" \
  "You are the MontanaBlotter Ops Assistant. Use the script output as source-of-truth context. Return: (1) current health status, (2) top 3 concrete risks, (3) exact command checklist for remediation, and (4) whether on-call intervention is needed right now. Keep it concise and actionable." \
  --name "mb_ops_pulse_daily" \
  --script "mb_context_health.py"

echo "  - mb_ops_pulse_daily"

"$HERMES_BIN" cron create "7 * * * *" \
  "You are the MontanaBlotter Ingestion Triage Assistant. Analyze watchdog and DB snapshot context. If anything is failing or stale, produce a triage report with probable root cause, impacted pipelines, and a step-by-step fix plan using existing scripts in /root/montanablotter. If healthy, output a one-paragraph all-clear plus one preventative hardening suggestion." \
  --name "mb_ingestion_triage_hourly" \
  --script "mb_context_ingestion.py"

echo "  - mb_ingestion_triage_hourly"

"$HERMES_BIN" cron create "10 13 * * 1" \
  "You are the MontanaBlotter Growth Editor. Using the weekly metrics context, produce a short weekly strategy brief: key trends, top county opportunities, content gaps, and 5 concrete story/workflow recommendations that can be implemented this week. Prioritize actions with expected impact and effort." \
  --name "mb_growth_brief_weekly" \
  --script "mb_context_growth.py"

echo "  - mb_growth_brief_weekly"

"$HERMES_BIN" cron create "35 6 * * *" \
  "You are the MontanaBlotter Morning Editor. Use the daily editorial context to produce: (1) top story opportunities, (2) county-level coverage gaps, (3) public meeting items worth watching, (4) missing-person/public-safety items that need careful treatment, and (5) a concise publish plan for today. Do not sensationalize arrests or accusations; prioritize verified public-record context and reader utility." \
  --name "mb_editorial_brief_daily" \
  --script "mb_context_editorial.py"

echo "  - mb_editorial_brief_daily"

"$HERMES_BIN" cron create "25 9 * * *" \
  "You are the MontanaBlotter Source Coverage Analyst. Review source freshness, jail roster coverage, and open source alerts. Return: stale or broken sources, priority counties to improve, likely technical causes, and exact next actions using existing scripts/configs under /root/montanablotter. Separate critical production issues from backlog improvements." \
  --name "mb_source_coverage_daily" \
  --script "mb_context_source_coverage.py"

echo "  - mb_source_coverage_daily"

"$HERMES_BIN" cron create "5 18 * * *" \
  "You are the MontanaBlotter Privacy and Quality Reviewer. Analyze audit, PII, comment, and thin-summary context. Return: posts needing review, privacy risks, quality issues, and exact remediation steps. Be conservative with public-record sensitivity and avoid suggesting publication of unverified personal details." \
  --name "mb_privacy_quality_daily" \
  --script "mb_context_privacy_quality.py"

echo "  - mb_privacy_quality_daily"

"$HERMES_BIN" cron create "30 15 * * 2" \
  "You are the MontanaBlotter Revenue Strategist. Use advertiser, bail lead, booking, post, and search context to produce a weekly monetization brief: strongest counties, best bail-bond advertiser targets, content pages likely to convert, outreach priorities, and one low-effort experiment for the week. Keep recommendations ethical and separate public-safety coverage from advertising influence." \
  --name "mb_revenue_strategy_weekly" \
  --script "mb_context_revenue.py"

echo "  - mb_revenue_strategy_weekly"

echo "Done. Current Hermes jobs:"
"$HERMES_BIN" cron list
