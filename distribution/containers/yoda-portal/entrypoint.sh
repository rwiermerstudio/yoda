#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${YODA_PORTAL_CONFIG:-/etc/yoda/flask.cfg}"

if [[ ! -r "${CONFIG_PATH}" ]]; then
  echo "FATAL: Portal configuration file '${CONFIG_PATH}' is missing or unreadable." >&2
  echo "Mount a yoda-config rendered flask.cfg to '${CONFIG_PATH}' before starting." >&2
  exit 1
fi

# Validate Python syntax of flask.cfg before booting Gunicorn (fail-fast)
if ! python3 -m py_compile "${CONFIG_PATH}" 2>/tmp/flask_cfg_err.log; then
  echo "FATAL: Syntax error in '${CONFIG_PATH}':" >&2
  cat /tmp/flask_cfg_err.log >&2
  exit 1
fi

# Link externalized config to expected portal path if distinct
if [[ "${CONFIG_PATH}" != "/var/www/yoda/flask.cfg" ]]; then
  ln -sf "${CONFIG_PATH}" /var/www/yoda/flask.cfg
fi

exec gunicorn \
  --bind "${YODA_PORTAL_BIND:-0.0.0.0:8080}" \
  --workers "${YODA_PORTAL_WORKERS:-4}" \
  --threads "${YODA_PORTAL_THREADS:-4}" \
  --timeout "${YODA_PORTAL_TIMEOUT:-60}" \
  --access-logfile "-" \
  --error-logfile "-" \
  "app:app"
