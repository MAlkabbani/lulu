#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_PATH="${MACOS_APP_DIR}/Lulu.xcodeproj"
SCHEME="Lulu"
CONFIGURATION="${CONFIGURATION:-Release}"
OUTPUT_DIR="${MACOS_APP_DIR}/build/release"
DERIVED_DATA_PATH="${MACOS_APP_DIR}/build/DerivedData"
APP_NAME="Lulu.app"
APP_PATH="${OUTPUT_DIR}/${APP_NAME}"
DMG_PATH="${OUTPUT_DIR}/Lulu-${CONFIGURATION}.dmg"
SKIP_SIGNING="${LULU_SKIP_SIGNING:-0}"
SKIP_DMG="${LULU_SKIP_DMG:-0}"
SIGN_IDENTITY="${LULU_CODESIGN_IDENTITY:-}"

if [[ -d /Applications/Xcode.app/Contents/Developer && -z "${DEVELOPER_DIR:-}" ]]; then
  export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
fi

if [[ ! -d "${PROJECT_PATH}" ]]; then
  echo "Xcode project not found: ${PROJECT_PATH}" >&2
  exit 1
fi

if ! command -v xcodebuild >/dev/null 2>&1; then
  echo "xcodebuild is required to package the macOS app" >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to assemble the packaged backend runtime" >&2
  exit 1
fi

rm -rf "${OUTPUT_DIR}" "${DERIVED_DATA_PATH}"
mkdir -p "${OUTPUT_DIR}"

xcodebuild \
  -project "${PROJECT_PATH}" \
  -scheme "${SCHEME}" \
  -configuration "${CONFIGURATION}" \
  -destination "platform=macOS" \
  -derivedDataPath "${DERIVED_DATA_PATH}" \
  CODE_SIGNING_ALLOWED=NO \
  build

BUILT_APP_PATH="${DERIVED_DATA_PATH}/Build/Products/${CONFIGURATION}/${APP_NAME}"
if [[ ! -d "${BUILT_APP_PATH}" ]]; then
  echo "build completed but app bundle was not found: ${BUILT_APP_PATH}" >&2
  exit 1
fi

rsync -a --delete "${BUILT_APP_PATH}/" "${APP_PATH}/"
"${SCRIPT_DIR}/assemble_backend_bundle.sh" "${APP_PATH}"

if [[ "${SKIP_SIGNING}" != "1" ]]; then
  if [[ -z "${SIGN_IDENTITY}" ]]; then
    echo "LULU_CODESIGN_IDENTITY is required unless LULU_SKIP_SIGNING=1" >&2
    exit 1
  fi
  codesign \
    --force \
    --deep \
    --options runtime \
    --entitlements "${MACOS_APP_DIR}/Lulu.entitlements" \
    --sign "${SIGN_IDENTITY}" \
    "${APP_PATH}"
fi

if [[ "${SKIP_DMG}" != "1" ]]; then
  if ! command -v hdiutil >/dev/null 2>&1; then
    echo "hdiutil is required to create a DMG artifact" >&2
    exit 1
  fi
  rm -f "${DMG_PATH}"
  hdiutil create \
    -volname "Lulu" \
    -srcfolder "${APP_PATH}" \
    -ov \
    -format UDZO \
    "${DMG_PATH}"
fi

echo "Packaged app bundle: ${APP_PATH}"
if [[ "${SKIP_DMG}" != "1" ]]; then
  echo "Packaged DMG: ${DMG_PATH}"
fi
