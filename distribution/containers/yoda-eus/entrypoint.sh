#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${YODA_EUS_CONFIG:-/etc/yoda/eus.cfg}"

if [[ ! -r "${CONFIG_PATH}" ]]; then
  echo "FATAL: EUS configuration file '${CONFIG_PATH}' is missing or unreadable." >&2
  exit 1
fi

if [[ "${CONFIG_PATH}" != "/var/www/extuser/yoda-external-user-service.cfg" ]]; then
  ln -sf "${CONFIG_PATH}" /var/www/extuser/yoda-external-user-service.cfg
fi

exec gunicorn \
  --bind "${YODA_EUS_BIND:-0.0.0.0:8080}" \
  --workers "${YODA_EUS_WORKERS:-2}" \
  --timeout "${YODA_EUS_TIMEOUT:-60}" \
  --access-logfile "-" \
  --error-logfile "-" \
  "wsgi:application"
