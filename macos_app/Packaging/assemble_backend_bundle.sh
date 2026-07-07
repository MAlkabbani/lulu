#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS_APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MACOS_APP_DIR}/.." && pwd)"

if [[ "${1:-}" == "" ]]; then
  echo "usage: $0 /path/to/Lulu.app" >&2
  exit 1
fi

APP_BUNDLE_PATH="$1"
RESOURCES_DIR="${APP_BUNDLE_PATH}/Contents/Resources"
BACKEND_DIR="${RESOURCES_DIR}/backend"
PYTHON_RUNTIME_SOURCE="${LULU_BUNDLED_PYTHON_DIR:-${REPO_ROOT}/.venv}"
PACKAGED_RUNTIME_DIR="${BACKEND_DIR}/runtime"

if [[ ! -d "${APP_BUNDLE_PATH}" ]]; then
  echo "app bundle not found: ${APP_BUNDLE_PATH}" >&2
  exit 1
fi

if [[ ! -d "${PYTHON_RUNTIME_SOURCE}" ]]; then
  echo "bundled Python runtime not found: ${PYTHON_RUNTIME_SOURCE}" >&2
  echo "set LULU_BUNDLED_PYTHON_DIR to a relocatable virtualenv or Python runtime directory" >&2
  exit 1
fi

mkdir -p "${BACKEND_DIR}"
rm -rf "${BACKEND_DIR:?}/"*

rsync -a \
  --exclude ".git/" \
  --exclude ".github/" \
  --exclude ".trae/" \
  --exclude ".venv/" \
  --exclude ".pytest_cache/" \
  --exclude ".ruff_cache/" \
  --exclude ".mypy_cache/" \
  --exclude ".dbg/" \
  --exclude "__pycache__/" \
  --exclude "docs/" \
  --exclude "exports/" \
  --exclude "logs/" \
  --exclude "run/" \
  --exclude "tests/" \
  --exclude "macos_app/" \
  --exclude ".DS_Store" \
  "${REPO_ROOT}/" "${BACKEND_DIR}/"

mkdir -p "${PACKAGED_RUNTIME_DIR}"
rsync -a --delete "${PYTHON_RUNTIME_SOURCE}/" "${PACKAGED_RUNTIME_DIR}/"

cat > "${BACKEND_DIR}/PACKAGED_RUNTIME_LAYOUT.txt" <<EOF
This directory is assembled by macos_app/Packaging/assemble_backend_bundle.sh.

Layout:
- backend source snapshot: ${BACKEND_DIR}
- bundled Python runtime: ${PACKAGED_RUNTIME_DIR}

The packaged desktop shell resolves the backend root from Bundle.main.resourceURL
and launches the backend with the bundled runtime instead of the repo-local .venv.
EOF

echo "Assembled bundled backend runtime at ${BACKEND_DIR}"
