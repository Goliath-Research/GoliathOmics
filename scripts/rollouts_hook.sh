#!/usr/bin/env bash
# Sourced by deploy scripts. Starts a Rollouts deployment and closes it from
# an EXIT trap. Reporting never changes the caller's success or failure.
# shellcheck shell=bash

_rollouts_reporter() {
  local dir
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  printf '%s/report_deploy.sh' "$dir"
}

rollouts_service_skipped() {
  local service="$1"
  if [[ "${GOLIATH_ROLLOUTS_EXTERNAL:-}" == "1" ]]; then
    return 0
  fi
  case ",${GOLIATH_ROLLOUTS_SKIP_SERVICES:-}," in
    *,"${service}",*) return 0 ;;
  esac
  return 1
}

rollouts_report() {
  local service=""
  local args=("$@")
  local i
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "--service" ]]; then
      service="${args[$((i + 1))]:-}"
    fi
  done
  if [[ -n "$service" ]] && rollouts_service_skipped "$service"; then
    return 0
  fi
  local reporter
  reporter="$(_rollouts_reporter)"
  if [[ ! -f "$reporter" ]]; then
    echo "[WARN] rollouts reporter missing at ${reporter}" >&2
    return 0
  fi
  bash "$reporter" "$@" || true
  return 0
}

rollouts_on_abort() {
  ROLLOUTS_ABORTED=1
  exit 130
}

rollouts_on_exit() {
  local rc=$?
  if [[ "${ROLLOUTS_REPORTED:-0}" == "1" ]]; then
    exit "$rc"
  fi
  ROLLOUTS_REPORTED=1
  local service="${ROLLOUTS_SERVICE:-}"
  local message="${ROLLOUTS_MESSAGE:-}"
  if [[ -z "$service" ]]; then
    exit "$rc"
  fi
  if [[ "$rc" -eq 0 ]]; then
    rollouts_report succeeded --service "$service" ${message:+--message "$message"}
  elif [[ "${ROLLOUTS_ABORTED:-0}" -eq 1 ]]; then
    rollouts_report aborted --service "$service" --message "${message:-aborted}"
  else
    rollouts_report failed --service "$service" --message "${message:-deploy failed} (exit ${rc})"
  fi
  exit "$rc"
}

# Update the message attached to the EXIT trap. Later assignments replace the
# text recorded at start, including on the success path.
rollouts_set_message() {
  ROLLOUTS_MESSAGE="${1:-}"
}

# rollouts_begin SERVICE [MESSAGE]
# Call after argument parsing, before the mutating work.
rollouts_begin() {
  local service="$1"
  local message="${2:-${ROLLOUTS_MESSAGE:-}}"
  ROLLOUTS_SERVICE="$service"
  ROLLOUTS_MESSAGE="$message"
  ROLLOUTS_ABORTED=0
  ROLLOUTS_REPORTED=0
  trap rollouts_on_abort INT TERM
  trap rollouts_on_exit EXIT
  rollouts_report start --service "$service" ${message:+--message "$message"}
}
