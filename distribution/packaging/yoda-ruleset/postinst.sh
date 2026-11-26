#!/usr/bin/env bash
set -euo pipefail

# Ensure irods service account owns staged ruleset & isolated venv
if id -u irods >/dev/null 2>&1; then
  chown -R irods:irods /etc/irods/yoda-ruleset /etc/irods/yoda-extensions.d /opt/yoda/ruleset-venv
fi

# Clean stale bytecode/rule caches from previous releases
find /etc/irods/yoda-ruleset -type f \( -name "*.pyc" -o -name "*.re.*" \) -delete 2>/dev/null || true

# Ensure symlink /etc/irods/yoda_ruleset -> yoda-ruleset exists
if [[ ! -e /etc/irods/yoda_ruleset ]]; then
  ln -s yoda-ruleset /etc/irods/yoda_ruleset
fi
