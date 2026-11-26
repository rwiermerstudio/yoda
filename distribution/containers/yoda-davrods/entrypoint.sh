#!/usr/bin/env bash
set -euo pipefail

VHOST_PATH="${YODA_DAVRODS_VHOST:-/etc/yoda/davrods-vhost.conf}"

if [[ ! -r "${VHOST_PATH}" ]]; then
  echo "FATAL: DavRODS Apache vhost config '${VHOST_PATH}' is missing or unreadable." >&2
  exit 1
fi

ln -sf "${VHOST_PATH}" /etc/apache2/sites-enabled/001-davrods.conf

# Fail fast if Apache config syntax is invalid
apache2ctl configtest

exec apache2ctl -D FOREGROUND
