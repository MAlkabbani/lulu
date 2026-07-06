#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${REPO_ROOT}/logs"
RUN_DIR="${REPO_ROOT}/run"
ENV_FILE="${REPO_ROOT}/.env"
VENV_DIR="${REPO_ROOT}/.venv"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
OLLAMA_CHAT_MODEL="${OLLAMA_CHAT_MODEL:-llama3.2:3b}"
OLLAMA_EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
OLLAMA_HEALTH_URL="${OLLAMA_BASE_URL%/}/api/version"
TIMESTAMP="$(date +"%Y%m%d-%H%M%S")"
LOG_FILE="${LOG_DIR}/startup-${TIMESTAMP}.log"
APP_PID=""
OLLAMA_PID=""
SHUTDOWN_REQUESTED=0
MODE="voice"
CHECK_ONLY=0
RESTART_ON_FAILURE="${LULU_RESTART_ON_FAILURE:-true}"
MAX_RESTARTS="${LULU_MAX_RESTARTS:-2}"
RESTART_BACKOFF_SECONDS="${LULU_RESTART_BACKOFF_SECONDS:-2}"
AUTO_START_OLLAMA="${LULU_AUTO_START_OLLAMA:-true}"
APP_ARGS=()

mkdir -p "${LOG_DIR}" "${RUN_DIR}"

log() {
  local level="$1"
  shift
  printf '[%s] [%s] %s\n' "$(date +"%Y-%m-%d %H:%M:%S")" "${level}" "$*" | tee -a "${LOG_FILE}" >&2
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

usage() {
  cat <<'EOF'
Usage: scripts/start_lulu.sh [--mode voice|turn-based] [--check] [-- <extra app args>]

Startup wrapper for Lulu VAIA with prerequisite checks, optional Ollama bootstrap,
foreground monitoring, and graceful shutdown handling.

Options:
  --mode voice|turn-based       Launch mode. Default: voice.
  --check                       Validate prerequisites and exit without starting the app.
  -h, --help                    Show this help message.

Environment:
  LULU_AUTO_START_OLLAMA=true|false
  LULU_RESTART_ON_FAILURE=true|false
  LULU_MAX_RESTARTS=<int>
  LULU_RESTART_BACKOFF_SECONDS=<int>
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ $# -ge 2 ]] || die "--mode requires a value."
      MODE="$2"
      shift
      ;;
    --check)
      CHECK_ONLY=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      APP_ARGS+=("$@")
      break
      ;;
    *)
      APP_ARGS+=("$1")
      ;;
  esac
  shift
done

case "${MODE}" in
  voice)
    ;;
  turn-based)
    APP_ARGS+=("--turn-based")
    ;;
  *)
    die "Unsupported mode: ${MODE}"
    ;;
esac

load_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    log "INFO" "Loading environment from ${ENV_FILE}"
    parse_env_file
  else
    log "WARN" "No .env file found. Falling back to process environment only."
  fi

  OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
  OLLAMA_CHAT_MODEL="${OLLAMA_CHAT_MODEL:-llama3.2:3b}"
  OLLAMA_EMBED_MODEL="${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
  OLLAMA_HEALTH_URL="${OLLAMA_BASE_URL%/}/api/version"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

wait_for_ollama() {
  local timeout_seconds="${1:-30}"
  local elapsed=0
  while (( elapsed < timeout_seconds )); do
    if curl -fsS "${OLLAMA_HEALTH_URL}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    ((elapsed += 1))
  done
  return 1
}

ensure_ollama() {
  if wait_for_ollama 2; then
    log "INFO" "Ollama is online."
    return 0
  fi

  if [[ "${AUTO_START_OLLAMA}" != "true" ]]; then
    die "Ollama is offline and LULU_AUTO_START_OLLAMA is false."
  fi

  log "WARN" "Ollama is offline. Starting managed local service."
  nohup ollama serve >"${LOG_DIR}/ollama-runtime.log" 2>&1 &
  OLLAMA_PID="$!"
  printf '%s\n' "${OLLAMA_PID}" >"${RUN_DIR}/ollama.pid"

  wait_for_ollama 30 || die "Ollama did not become healthy after startup."
}

ensure_models() {
  local required_models=("${OLLAMA_CHAT_MODEL}" "${OLLAMA_EMBED_MODEL}")
  for model in "${required_models[@]}"; do
    if ! ollama list 2>/dev/null | awk -v model="${model}" '
      NR > 1 && ($1 == model || index($1, model ":") == 1) { found = 1 }
      END { exit(found ? 0 : 1) }
    '; then
      die "Required Ollama model is missing: ${model}. Run ./scripts/install_lulu.sh first."
    fi
  done
}

graceful_shutdown() {
  if [[ "${SHUTDOWN_REQUESTED}" -eq 1 ]]; then
    return 0
  fi
  SHUTDOWN_REQUESTED=1
  log "INFO" "Shutdown requested. Stopping managed processes."

  if [[ -n "${APP_PID}" ]] && kill -0 "${APP_PID}" >/dev/null 2>&1; then
    kill -TERM "${APP_PID}" >/dev/null 2>&1 || true
    wait "${APP_PID}" || true
  fi

  if [[ -n "${OLLAMA_PID}" ]] && kill -0 "${OLLAMA_PID}" >/dev/null 2>&1; then
    kill -TERM "${OLLAMA_PID}" >/dev/null 2>&1 || true
    wait "${OLLAMA_PID}" || true
  fi

  rm -f "${RUN_DIR}/lulu.pid" "${RUN_DIR}/ollama.pid"
}

trap graceful_shutdown INT TERM EXIT

load_env_file

require_command curl
require_command ollama
[[ -d "${VENV_DIR}" ]] || die "Virtual environment is missing. Run ./scripts/install_lulu.sh first."
[[ -x "${VENV_DIR}/bin/python" ]] || die "Python interpreter missing inside ${VENV_DIR}."

ensure_ollama
ensure_models

if [[ "${CHECK_ONLY}" -eq 1 ]]; then
  log "INFO" "Startup checks completed successfully."
  exit 0
fi

restart_count=0

while true; do
  app_command=("${VENV_DIR}/bin/python" "${REPO_ROOT}/main.py")
  if [[ ${#APP_ARGS[@]} -gt 0 ]]; then
    app_command+=("${APP_ARGS[@]}")
  fi

  log "INFO" "Starting Lulu in ${MODE} mode."
  log "INFO" "Wrapper log: ${LOG_FILE}"
  "${app_command[@]}" &
  APP_PID="$!"
  printf '%s\n' "${APP_PID}" >"${RUN_DIR}/lulu.pid"

  if wait "${APP_PID}"; then
    exit_code=0
  else
    exit_code="$?"
  fi
  APP_PID=""
  rm -f "${RUN_DIR}/lulu.pid"

  if [[ "${SHUTDOWN_REQUESTED}" -eq 1 ]]; then
    break
  fi

  if [[ "${exit_code}" -eq 0 ]]; then
    log "INFO" "Lulu exited cleanly."
    break
  fi

  log "ERROR" "Lulu exited unexpectedly with code ${exit_code}."
  if [[ "${RESTART_ON_FAILURE}" != "true" || "${restart_count}" -ge "${MAX_RESTARTS}" ]]; then
    die "Restart budget exhausted."
  fi

  restart_count=$((restart_count + 1))
  log "WARN" "Restarting Lulu in ${RESTART_BACKOFF_SECONDS}s (${restart_count}/${MAX_RESTARTS})."
  sleep "${RESTART_BACKOFF_SECONDS}"
done
