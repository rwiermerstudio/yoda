#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${YODA_MOAI_CONFIG:-/etc/yoda/moai-settings.ini}"

if [[ ! -r "${CONFIG_PATH}" ]]; then
  echo "FATAL: MOAI configuration file '${CONFIG_PATH}' is missing or unreadable." >&2
  exit 1
fi

exec gunicorn \
  --bind "${YODA_PUBLIC_BIND:-0.0.0.0:8080}" \
  --workers "${YODA_PUBLIC_WORKERS:-2}" \
  --access-logfile "-" \
  --error-logfile "-" \
  "moai.wsgi:application"
