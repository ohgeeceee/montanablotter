#!/usr/bin/env bash
set -euo pipefail

# Runs the Hermes gateway in the foreground, but with sane defaults for servers.
# Prefer the systemd unit in ops/systemd/ when available.

export HERMES_HOME="${HERMES_HOME:-/root/montanablotter/.hermes}"

LOG_DIR="/root/montanablotter/logs"
mkdir -p "$LOG_DIR"

exec /root/.local/bin/hermes gateway run --replace --accept-hooks 2>&1 | tee -a "$LOG_DIR/hermes-gateway.log"
