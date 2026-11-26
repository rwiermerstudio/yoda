#!/usr/bin/env bash
# Reproducible .deb / .rpm builder for yoda-ruleset with an isolated Python venv.
# Eliminates live git clone, git apply, make install, and pip --break-system-packages on prod hosts.
#
# Usage:
#   ./build-package.sh --version 2.2.0 --ref v2.2.0 --format deb [--patch /path/to/local.patch]

set -euo pipefail

VERSION=""
REF="development"
REPO="https://github.com/UtrechtUniversity/yoda-ruleset.git"
FORMAT="deb"
OUT_DIR="./dist"
PATCH_FILE=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --format) FORMAT="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --patch) PATCH_FILE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${VERSION}" ]]; then
  echo "Error: --version is required (e.g. --version 2.2.0)" >&2
  exit 1
fi

if [[ "${FORMAT}" != "deb" && "${FORMAT}" != "rpm" ]]; then
  echo "Error: --format must be 'deb' or 'rpm'" >&2
  exit 1
fi

if ! command -v fpm >/dev/null 2>&1; then
  echo "Error: 'fpm' is required to build packages (gem install fpm)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

STAGE="${WORK_DIR}/stage"
RULESET_DST="${STAGE}/etc/irods/yoda-ruleset"
VENV_DST="${STAGE}/opt/yoda/ruleset-venv"
EXT_DIR="${STAGE}/etc/irods/yoda-extensions.d"
EXEC_DIR="${STAGE}/var/lib/irods/msiExecCmd_bin"

mkdir -p "${RULESET_DST}" "${EXT_DIR}" "${EXEC_DIR}" "${OUT_DIR}"

echo "[1/4] Cloning yoda-ruleset (${REF})..."
git clone --depth 1 --branch "${REF}" "${REPO}" "${RULESET_DST}"

if [[ -n "${PATCH_FILE}" ]]; then
  echo "[1b] Applying build-time institutional patch (${PATCH_FILE})..."
  git -C "${RULESET_DST}" apply "${PATCH_FILE}"
fi

echo "[2/4] Running make install in staged ruleset..."
make -C "${RULESET_DST}" install
rm -rf "${RULESET_DST}/.git"

echo "[3/4] Building isolated Python virtualenv at /opt/yoda/ruleset-venv..."
"${PYTHON_BIN}" -m venv "${VENV_DST}"
"${VENV_DST}/bin/pip" install --no-cache-dir --upgrade pip
"${VENV_DST}/bin/pip" install --no-cache-dir -r "${RULESET_DST}/requirements.txt"

# Stage ExecCmd helper scripts
if [[ -f "${RULESET_DST}/tools/securecopy.sh" ]]; then
  install -m 0755 "${RULESET_DST}/tools/securecopy.sh" "${EXEC_DIR}/securecopy.sh"
fi

echo "[4/4] Packaging yoda-ruleset ${VERSION} as .${FORMAT}..."
fpm \
  -s dir \
  -t "${FORMAT}" \
  -n "yoda-ruleset" \
  -v "${VERSION}" \
  -C "${STAGE}" \
  --maintainer "Yoda Team <yoda@uu.nl>" \
  --description "Yoda iRODS ruleset with isolated Python runtime (no live pip/git on prod)" \
  --url "https://github.com/UtrechtUniversity/yoda-ruleset" \
  --after-install "${SCRIPT_DIR}/postinst.sh" \
  --config-files /etc/irods/yoda-extensions.d \
  -p "${OUT_DIR}/yoda-ruleset_${VERSION}_amd64.${FORMAT}" \
  etc opt var

echo "Built package in ${OUT_DIR}/yoda-ruleset_${VERSION}_amd64.${FORMAT}"
