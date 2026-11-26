#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker compose -f "${SCRIPT_DIR}/compose-s3/docker-compose.yml" --env-file "${SCRIPT_DIR}/compose-s3/.env" down "$@"
