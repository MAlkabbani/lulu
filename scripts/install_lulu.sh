#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
RUN_DIR="${REPO_ROOT}/run"
TIMESTAMP="$(date +"%Y%m%d-%H%M%S")"
LOG_FILE="${LOG_DIR}/install-${TIMESTAMP}.log"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
ENV_FILE="${REPO_ROOT}/.env"
REQUIREMENTS_FILE="${REPO_ROOT}/requirements.txt"
VENV_DIR="${REPO_ROOT}/.venv"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
OLLAMA_CHAT_MODEL="${OLLAMA_CHAT_MODEL:-llama3.2:3b}"
OLLAMA_EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
ROLLBACK_TYPES=()
ROLLBACK_ARGS=()
DRY_RUN=0
AUTO_START_OLLAMA=1
ALLOW_SYSTEM_PACKAGE_ROLLBACK="${ALLOW_SYSTEM_PACKAGE_ROLLBACK:-0}"
STARTED_OLLAMA_PID=""

mkdir -p "${LOG_DIR}" "${RUN_DIR}"
exec > >(tee -a "${LOG_FILE}") 2>&1

log() {
  local level="$1"
  shift
  printf '[%s] [%s] %s\n' "$(date +"%Y-%m-%d %H:%M:%S")" "${level}" "$*"
}

die() {
  log "ERROR" "$*"
  exit 1
}

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

strip_matching_quotes() {
  local value="$1"
  if [[ ${#value} -lt 2 ]]; then
    printf '%s' "${value}"
    return 0
  fi

  local first_char="${value:0:1}"
  local last_char="${value: -1}"
  if [[ ( "${first_char}" == '"' && "${last_char}" == '"' ) || ( "${first_char}" == "'" && "${last_char}" == "'" ) ]]; then
    printf '%s' "${value:1:${#value}-2}"
    return 0
  fi

  printf '%s' "${value}"
}

load_env_pair() {
  local raw_line="$1"
  local line key value

  line="$(trim_whitespace "${raw_line}")"
  if [[ -z "${line}" || "${line:0:1}" == "#" ]]; then
    return 0
  fi
  if [[ "${line}" != *=* ]]; then
    die "Invalid environment line in ${ENV_FILE}: ${raw_line}"
  fi

  key="$(trim_whitespace "${line%%=*}")"
  value="$(trim_whitespace "${line#*=}")"
  if [[ ! "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    die "Invalid environment variable name in ${ENV_FILE}: ${key}"
  fi

  value="$(strip_matching_quotes "${value}")"
  export "${key}=${value}"
}

parse_env_file() {
  local raw_line=""
  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    load_env_pair "${raw_line}"
  done < "${ENV_FILE}"
}

add_rollback() {
  ROLLBACK_TYPES+=("$1")
  ROLLBACK_ARGS+=("${2-}")
}

run_cmd() {
  local rendered=""
  printf -v rendered '%q ' "$@"
  log "INFO" "RUN: ${rendered% }"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return 0
  fi
  "$@"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

brew_formula_installed() {
  brew list --formula "$1" >/dev/null 2>&1
}

ensure_brew_formula() {
  local formula="$1"
  if brew_formula_installed "${formula}"; then
    log "INFO" "Homebrew formula already present: ${formula}"
    return 0
  fi

  run_cmd brew install "${formula}"
  if [[ "${ALLOW_SYSTEM_PACKAGE_ROLLBACK}" == "1" ]]; then
    add_rollback "brew_uninstall" "${formula}"
  fi
}

wait_for_ollama() {
  local timeout_seconds="${1:-30}"
  local elapsed=0
  while (( elapsed < timeout_seconds )); do
    if curl -fsS "${OLLAMA_BASE_URL%/}/api/version" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    ((elapsed += 1))
  done
  return 1
}

ensure_ollama_service() {
  if wait_for_ollama 2; then
    log "INFO" "Ollama is already online."
    return 0
  fi

  if [[ "${AUTO_START_OLLAMA}" -ne 1 ]]; then
    die "Ollama is offline and auto-start is disabled."
  fi

  log "INFO" "Starting temporary Ollama service for installation."
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return 0
  fi

  nohup ollama serve >"${LOG_DIR}/ollama-install-${TIMESTAMP}.log" 2>&1 &
  STARTED_OLLAMA_PID="$!"
  add_rollback "kill_pid" "${STARTED_OLLAMA_PID}"

  wait_for_ollama 30 || die "Ollama did not become healthy after startup."
}

ollama_model_present() {
  local model="$1"
  ollama list 2>/dev/null | awk -v model="${model}" '
    NR > 1 && ($1 == model || index($1, model ":") == 1) { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

ensure_model() {
  local model="$1"
  if ollama_model_present "${model}"; then
    log "INFO" "Ollama model already present: ${model}"
    return 0
  fi

  run_cmd ollama pull "${model}"
  add_rollback "ollama_rm_model" "${model}"
}

restore_file_if_missing() {
  local source="$1"
  local target="$2"
  if [[ -e "${target}" ]]; then
    log "INFO" "Keeping existing file: ${target}"
    return 0
  fi
  run_cmd cp "${source}" "${target}"
  add_rollback "remove_path" "${target}"
}

load_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    parse_env_file
  fi

  OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
  OLLAMA_CHAT_MODEL="${OLLAMA_CHAT_MODEL:-llama3.2:3b}"
  OLLAMA_EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
}

rollback() {
  local status="$1"
  if [[ "${status}" -eq 0 ]]; then
    return 0
  fi

  log "WARN" "Install failed. Starting rollback for repo-local changes."
  for (( idx=${#ROLLBACK_TYPES[@]}-1; idx>=0; idx-- )); do
    local type="${ROLLBACK_TYPES[idx]}"
    local arg="${ROLLBACK_ARGS[idx]}"
    case "${type}" in
      remove_path)
        if [[ -e "${arg}" ]]; then
          log "WARN" "Rollback: removing ${arg}"
          rm -rf -- "${arg}"
        fi
        ;;
      kill_pid)
        if [[ -n "${arg}" ]] && kill -0 "${arg}" >/dev/null 2>&1; then
          log "WARN" "Rollback: stopping PID ${arg}"
          kill "${arg}" >/dev/null 2>&1 || true
        fi
        ;;
      ollama_rm_model)
        if command_exists ollama && ollama_model_present "${arg}"; then
          log "WARN" "Rollback: removing Ollama model ${arg}"
          ollama rm "${arg}" >/dev/null 2>&1 || true
        fi
        ;;
      brew_uninstall)
        if [[ "${ALLOW_SYSTEM_PACKAGE_ROLLBACK}" == "1" ]] && brew_formula_installed "${arg}"; then
          log "WARN" "Rollback: uninstalling Homebrew formula ${arg}"
          brew uninstall "${arg}" >/dev/null 2>&1 || true
        fi
        ;;
      *)
        log "WARN" "Skipping unknown rollback action: ${type}"
        ;;
    esac
  done

  log "WARN" "Rollback finished. Review ${LOG_FILE} before retrying."
}

on_exit() {
  local status="$?"
  rollback "${status}"
  exit "${status}"
}

usage() {
  cat <<'EOF'
Usage: scripts/install_lulu.sh [--dry-run] [--no-auto-start-ollama]

Fresh-system installation for Lulu VAIA on macOS/Homebrew.

Options:
  --dry-run               Validate and print intended actions without mutating the system.
  --no-auto-start-ollama  Require Ollama to already be running.
  -h, --help              Show this help message.

Environment:
  ALLOW_SYSTEM_PACKAGE_ROLLBACK=1  Also roll back Homebrew formulas installed by this script.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --no-auto-start-ollama)
      AUTO_START_OLLAMA=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
  shift
done

trap on_exit EXIT

log "INFO" "Lulu installation started. Log file: ${LOG_FILE}"

[[ "$(uname -s)" == "Darwin" ]] || die "This installer currently supports macOS only."
[[ -f "${REQUIREMENTS_FILE}" ]] || die "Missing requirements file: ${REQUIREMENTS_FILE}"
[[ -f "${ENV_EXAMPLE}" ]] || die "Missing env example: ${ENV_EXAMPLE}"

command_exists brew || die "Homebrew is required. Install it from https://brew.sh/ first."
command_exists curl || die "curl is required."

log "INFO" "Validating required Homebrew formulas."
run_cmd brew update
ensure_brew_formula "python@3.12"
ensure_brew_formula "portaudio"
ensure_brew_formula "ffmpeg"
ensure_brew_formula "ollama"

command_exists python3.12 || die "python3.12 was not found after installation."

restore_file_if_missing "${ENV_EXAMPLE}" "${ENV_FILE}"
load_env_file

if [[ -d "${VENV_DIR}" ]]; then
  log "INFO" "Reusing existing virtual environment at ${VENV_DIR}"
else
  run_cmd python3.12 -m venv "${VENV_DIR}"
  add_rollback "remove_path" "${VENV_DIR}"
fi

run_cmd "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
run_cmd "${VENV_DIR}/bin/pip" install -r "${REQUIREMENTS_FILE}"

ensure_ollama_service
ensure_model "${OLLAMA_CHAT_MODEL}"
ensure_model "${OLLAMA_EMBED_MODEL}"

run_cmd "${VENV_DIR}/bin/python" - <<'PY'
import chromadb
import mlx
import requests
import rich
import sounddevice

print("Python dependencies imported successfully.")
PY

log "INFO" "Installation completed successfully."
log "INFO" "Next steps:"
log "INFO" "  1. Review ${ENV_FILE} and adjust AUDIO_INPUT_DEVICE if needed."
log "INFO" "  2. Start Lulu with ./scripts/start_lulu.sh"
log "INFO" "  3. If macOS prompts for microphone access, allow it for your terminal or IDE host process."
