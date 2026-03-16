#!/bin/sh

set -eu

WEB_DIR="/workspace/services/web-ui"
LOCKFILE="${WEB_DIR}/package-lock.json"
STAMP_DIR="${WEB_DIR}/node_modules/.cache"
STAMP_FILE="${STAMP_DIR}/package-lock.sha256"

cd "${WEB_DIR}"

mkdir -p "${STAMP_DIR}"

CURRENT_HASH="$(sha256sum "${LOCKFILE}" | awk '{print $1}')"
PREVIOUS_HASH="$(cat "${STAMP_FILE}" 2>/dev/null || true)"

if [ ! -x "${WEB_DIR}/node_modules/.bin/next" ] || [ "${CURRENT_HASH}" != "${PREVIOUS_HASH}" ]; then
  npm ci
  mkdir -p "${STAMP_DIR}"
  printf "%s" "${CURRENT_HASH}" > "${STAMP_FILE}"
fi

exec npm run dev -- --hostname 0.0.0.0 --port 3001
