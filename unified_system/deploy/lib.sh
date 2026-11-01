#!/usr/bin/env bash

DEPLOY_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DIR="$(cd -- "$DEPLOY_DIR/.." && pwd)"
COMPOSE_FILE="$SYSTEM_DIR/docker-compose.prod.yml"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env.production}"

require_deploy_env() {
  if [[ ! -r "$ENV_FILE" ]]; then
    printf 'Missing deployment environment file: %s\n' "$ENV_FILE" >&2
    return 1
  fi
  if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker CLI is required.\n' >&2
    return 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    printf 'Docker Compose v2 plugin is required.\n' >&2
    return 1
  fi
}

compose() {
  docker compose --project-directory "$SYSTEM_DIR" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# Read only a named, non-shell-expanded KEY=value from the deployment file.
# This deliberately does not source or print the file, which contains secrets.
read_deploy_value() {
  local key="$1" value=""
  value="$(printenv "$key" 2>/dev/null || true)"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return 0
  fi
  awk -v key="$key" 'index($0, key "=") == 1 { sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit }' "$ENV_FILE"
}

stop_if_profile_service_running() {
  local profile="$1" service="$2" container_id
  container_id="$(compose --profile "$profile" ps -q "$service" 2>/dev/null || true)"
  if [[ -n "$container_id" ]]; then
    compose --profile "$profile" stop "$service"
  fi
}
