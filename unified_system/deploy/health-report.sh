#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
require_deploy_env

printf '%s\n' '== Compose services =='
compose ps
printf '\n%s\n' '== Edge readiness (loopback only) =='
edge_port="$(read_deploy_value EDGE_PORT)"
edge_port="${edge_port:-8080}"
if command -v curl >/dev/null 2>&1; then
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${edge_port}/healthz" >/dev/null
  printf 'edge: healthy at 127.0.0.1:%s\n' "$edge_port"
  curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${edge_port}/api/v1/health/ready" >/dev/null
  printf 'backend/database readiness: healthy\n'
else
  printf 'curl unavailable; use Docker health status shown above.\n'
fi
printf '\n%s\n' '== Host disk =='
df -h "$SYSTEM_DIR" /var/lib/docker 2>/dev/null || true
printf '\n%s\n' '== Container resource snapshot =='
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}' 2>/dev/null || true
printf '\nWorker liveness is reported through persisted WorkerInstance heartbeats in the admin UI/API. No platform Worker is enabled by this default Compose stack.\n'
