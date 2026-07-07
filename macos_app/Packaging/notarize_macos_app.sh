#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "" ]]; then
  echo "usage: $0 /path/to/Lulu.dmg" >&2
  exit 1
fi

ARTIFACT_PATH="$1"
APPLE_TEAM_ID="${LULU_APPLE_TEAM_ID:-}"
APPLE_ID="${LULU_APPLE_ID:-}"
APPLE_APP_PASSWORD="${LULU_APPLE_APP_PASSWORD:-}"
NOTARY_PROFILE="${LULU_NOTARY_PROFILE:-}"

if [[ ! -f "${ARTIFACT_PATH}" ]]; then
  echo "artifact not found: ${ARTIFACT_PATH}" >&2
  exit 1
fi

if ! command -v xcrun >/dev/null 2>&1; then
  echo "xcrun is required for notarization" >&2
  exit 1
fi

if [[ -n "${NOTARY_PROFILE}" ]]; then
  xcrun notarytool submit "${ARTIFACT_PATH}" \
    --keychain-profile "${NOTARY_PROFILE}" \
    --wait
else
  if [[ -z "${APPLE_TEAM_ID}" || -z "${APPLE_ID}" || -z "${APPLE_APP_PASSWORD}" ]]; then
    echo "set LULU_NOTARY_PROFILE or all of LULU_APPLE_TEAM_ID, LULU_APPLE_ID, and LULU_APPLE_APP_PASSWORD" >&2
    exit 1
  fi

  xcrun notarytool submit "${ARTIFACT_PATH}" \
    --apple-id "${APPLE_ID}" \
    --password "${APPLE_APP_PASSWORD}" \
    --team-id "${APPLE_TEAM_ID}" \
    --wait
fi

xcrun stapler staple "${ARTIFACT_PATH}"
echo "Notarized and stapled: ${ARTIFACT_PATH}"
