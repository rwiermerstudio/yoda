#!/bin/bash
set -e
set -o pipefail
set -u

DATA_VERSION="${1:-development}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-s3admin}"
S3_SECRET_KEY="${S3_SECRET_KEY:-s3secret}"

# Ensure tmpfs S3 cache directory and .s3auth exist on every container start
install -d -m 0700 -o irods -g irods /dev/shm/irods-s3-cache
printf '%s\n%s\n' "${S3_ACCESS_KEY}" "${S3_SECRET_KEY}" > /var/lib/irods/.s3auth
chown irods:irods /var/lib/irods/.s3auth
chmod 0600 /var/lib/irods/.s3auth

# Remove stale pidfiles left behind by a previous container run (otherwise rsyslogd aborts the init script)
rm -f /run/rsyslogd.pid

# Launch standard Yoda iCAT init in the background
/var/lib/irods/scripts/irods-icat-init.sh "${DATA_VERSION}" &
INIT_PID=$!

# Wait until iRODS provider has finished initial setup and is accepting connections
until [ -f /container_initialized ] && [ -f /var/lib/irods/.irods/.irodsA ] && sudo -u irods ils /tempZone >/dev/null 2>&1; do
    if ! kill -0 "${INIT_PID}" 2>/dev/null; then
        echo "[ERROR] irods-icat-init.sh exited before iRODS became available" >&2
        exit 1
    fi
    sleep 1
done

if [ ! -f /s3_workspace_migrated ]; then
    echo "[...] Migrating Yoda Workspace and Vault completely to S3..."
    sudo -H --preserve-env=S3_ENDPOINT,S3_BUCKET,S3_ACCESS_KEY,S3_SECRET_KEY,S3_REGION -u irods python3 /var/lib/irods/scripts/migrate_to_s3.py
    touch /s3_workspace_migrated
    echo "[ OK ] Yoda Workspace and Vault are now running 100% on S3."
fi

wait "${INIT_PID}"
