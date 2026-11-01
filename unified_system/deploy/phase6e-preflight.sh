#!/usr/bin/env bash
# Read-only target-host inventory for Phase 6E. Does not install or start/stop services, modify persistent data, or publish anything.
set -uo pipefail

REPO_DIR="${REPO_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}"
SYSTEM_DIR="$REPO_DIR/unified_system"
EXPECTED_BRANCH="${PHASE6E_EXPECTED_BRANCH:-arena/bd5e1acd-chaoxing}"
EXPECTED_COMMIT="${PHASE6E_EXPECTED_COMMIT:-ddde002}"
# Prevent git status from opportunistically refreshing/writing the index.
export GIT_OPTIONAL_LOCKS=0

section() { printf '\n== %s ==\n' "$1"; }
mode_of() { stat -c '%a' -- "$1" 2>/dev/null || printf 'unknown'; }

section 'Repository baseline (no file contents or credentials printed)'
if git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch="$(git -C "$REPO_DIR" branch --show-current 2>/dev/null || true)"
  commit="$(git -C "$REPO_DIR" rev-parse --short=7 HEAD 2>/dev/null || true)"
  dirty_count="$(git -C "$REPO_DIR" status --porcelain=v1 2>/dev/null | wc -l | tr -d ' ')"
  printf 'Repository: present\nBranch: %s\nHEAD: %s\nDirty/untracked entries: %s\n' \
    "${branch:-detached/unknown}" "${commit:-unknown}" "${dirty_count:-unknown}"
  [[ "$branch" == "$EXPECTED_BRANCH" ]] && printf 'Expected branch: PASS\n' || printf 'Expected branch: FAIL (expected %s)\n' "$EXPECTED_BRANCH"
  [[ "$commit" == "$EXPECTED_COMMIT" ]] && printf 'Expected baseline commit: PASS\n' || printf 'Expected baseline commit: FAIL (expected %s)\n' "$EXPECTED_COMMIT"
  [[ "$dirty_count" == 0 ]] && printf 'Clean worktree: PASS\n' || printf 'Clean worktree: FAIL\n'

  remote="$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)"
  if [[ -n "$remote" ]]; then
    # Deliberately print only host/path; strip URL credentials, query and fragment.
    safe_remote="$(printf '%s' "$remote" | python3 -c '
import re, sys
from urllib.parse import urlsplit
s=sys.stdin.read().strip()
if "://" in s:
    u=urlsplit(s)
    host=u.hostname or "unknown-host"
    path=u.path.lstrip("/")
    print((host + ("/" + path if path else "")).split("?",1)[0].split("#",1)[0])
elif "@" in s and ":" in s:
    hostpath=s.rsplit("@",1)[1]
    print(hostpath.split("?",1)[0].split("#",1)[0])
else:
    print("configured (format withheld)")
' 2>/dev/null || printf 'configured (format withheld)')"
    printf 'Origin (credentials stripped): %s\n' "$safe_remote"
  else
    printf 'Origin: not configured\n'
  fi
  if git -C "$REPO_DIR" rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1; then
    read -r ahead behind < <(git -C "$REPO_DIR" rev-list --left-right --count 'HEAD...@{upstream}' 2>/dev/null)
    printf 'Upstream divergence: local-only=%s remote-only=%s\n' "$ahead" "$behind"
  else
    printf 'Upstream: not configured\n'
  fi
  tag="$(git -C "$REPO_DIR" describe --tags --exact-match HEAD 2>/dev/null || true)"
  printf 'Exact tag at HEAD: %s\n' "${tag:-none}"
else
  printf 'Repository: FAIL (not a Git worktree at REPO_DIR)\n'
fi

section 'Operating system and resources (this host only)'
if [[ -r /etc/os-release ]]; then
  # Only print standard OS name/version fields; do not source arbitrary environment data.
  os_name="$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release | head -n1 | tr -d '"')"
  os_id="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | head -n1 | tr -d '"')"
  printf 'OS: %s (%s)\n' "${os_name:-unknown}" "${os_id:-unknown}"
else
  printf 'OS: /etc/os-release unavailable\n'
fi
printf 'Kernel: %s\nArchitecture: %s\nPython: %s\nCPU cores: %s\n' \
  "$(uname -r 2>/dev/null || printf unknown)" \
  "$(uname -m 2>/dev/null || printf unknown)" \
  "$(python3 --version 2>&1 || printf unavailable)" \
  "$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf unknown)"
awk '/MemTotal|MemAvailable/ {printf "%s=%s%s ", $1, $2, $3} END {print ""}' /proc/meminfo 2>/dev/null || true
for label_path in "project:$REPO_DIR" "home:${HOME:-/home/agentuser}" "docker:/var/lib/docker" "backup:$SYSTEM_DIR/deploy/backups"; do
  label="${label_path%%:*}"; path="${label_path#*:}"
  if [[ -e "$path" ]]; then
    # Print capacity only, not mount source/device identifiers.
    df -P "$path" 2>/dev/null | awk -v label="$label" 'NR==2 {printf "%s filesystem: size=%s used=%s available=%s use=%s\n", label, $2, $3, $4, $5}'
    df -Pi "$path" 2>/dev/null | awk -v label="$label" 'NR==2 {printf "%s inodes: used=%s available=%s use=%s\n", label, $3, $4, $5}'
  else
    printf '%s path: absent\n' "$label"
  fi
done

section 'Deployment environment file presence and permissions only'
for path in "$SYSTEM_DIR/deploy/.env.production" "$SYSTEM_DIR/deploy/.env.restore" "$SYSTEM_DIR/deploy/nginx/.env.nginx"; do
  if [[ -f "$path" && ! -L "$path" ]]; then
    printf '%s: present mode=%s (contents intentionally not read)\n' "${path##*/}" "$(mode_of "$path")"
  elif [[ -e "$path" || -L "$path" ]]; then
    printf '%s: present but not a regular non-symlink file\n' "${path##*/}"
  else
    printf '%s: absent\n' "${path##*/}"
  fi
done

section 'Docker, Compose, PostgreSQL and running Compose service states'
if command -v docker >/dev/null 2>&1; then
  printf 'Docker CLI: %s\n' "$(docker --version 2>&1 | sed -E 's/ (build|commit) .*$//' | head -n1)"
  if docker compose version >/dev/null 2>&1; then
    printf 'Docker Compose: %s\n' "$(docker compose version --short 2>/dev/null || printf 'present/version unavailable')"
  else
    printf 'Docker Compose: unavailable\n'
  fi
  server_version="$(timeout 8s docker info --format '{{.ServerVersion}}' 2>/dev/null || true)"
  printf 'Docker daemon: %s\n' "${server_version:+connected version=$server_version}"
  [[ -n "$server_version" ]] || printf 'Docker daemon: unavailable/not authorized\n'
  if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    printf 'Current account in docker group: yes (root-equivalent privilege; restrict membership)\n'
  else
    printf 'Current account in docker group: no\n'
  fi
  if [[ -n "$server_version" ]]; then
    for service in postgres backend edge worker-chaoxing worker-zhihuishu worker-yinghua; do
      ids="$(docker ps -aq --filter "label=com.docker.compose.service=$service" 2>/dev/null || true)"
      if [[ -z "$ids" ]]; then
        printf '%s: not running/present\n' "$service"
      else
        while IFS= read -r id; do
          [[ -n "$id" ]] || continue
          state="$(docker inspect --format '{{.State.Status}}{{if .State.Health}}/{{.State.Health.Status}}{{end}}' "$id" 2>/dev/null || printf unknown)"
          printf '%s: %s\n' "$service" "$state"
          if [[ "$service" == postgres ]]; then
            pgver="$(docker exec "$id" postgres --version 2>/dev/null || true)"
            printf 'PostgreSQL server binary: %s\n' "${pgver##* }"
          fi
        done <<< "$ids"
      fi
    done
  fi
else
  printf 'Docker CLI: absent\nDocker daemon: not checked\nCompose services: not checked\n'
fi
if command -v psql >/dev/null 2>&1; then printf 'PostgreSQL client: %s\n' "$(psql --version 2>/dev/null | sed -E 's/.*PostgreSQL\) //')"; else printf 'PostgreSQL client: absent\n'; fi
pg_binary_found=0
for pg_binary in "$(command -v postgres 2>/dev/null || true)" /usr/lib/postgresql/*/bin/postgres; do
  if [[ -n "$pg_binary" && -x "$pg_binary" ]]; then
    printf 'PostgreSQL server binary: %s\n' "$("$pg_binary" --version 2>/dev/null | sed -E 's/^postgres \(PostgreSQL\) //')"
    pg_binary_found=1
  fi
done
[[ "$pg_binary_found" == 1 ]] || printf 'PostgreSQL server binary: not found outside running Compose containers\n'
if command -v age >/dev/null 2>&1; then printf 'age: %s\n' "$(age --version 2>/dev/null | head -n1)"; else printf 'age: absent\n'; fi
if command -v openssl >/dev/null 2>&1; then printf 'OpenSSL: %s\n' "$(openssl version 2>/dev/null | head -n1)"; else printf 'OpenSSL: absent\n'; fi

section 'Nginx and systemd services (read-only checks)'
if command -v nginx >/dev/null 2>&1; then
  nginx_version="$(nginx -v 2>&1 | sed -E 's/^.*nginx\///')"
  printf 'Nginx: present version=%s (config syntax not tested during read-only preflight)\n' "${nginx_version:-unknown}"
else
  printf 'Nginx: absent\n'
fi
if command -v systemctl >/dev/null 2>&1; then
  for unit in docker nginx postgresql unified-learning-backup.timer unified-learning-monitor.timer; do
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    printf '%s: %s\n' "$unit" "${state:-unknown/not-installed}"
  done
else
  printf 'systemd: absent\n'
fi

section 'Listening TCP port numbers (addresses withheld)'
if command -v ss >/dev/null 2>&1; then
  ss -ltnH 2>/dev/null | awk '{local=$4; sub(/^.*:/,"",local); if (local ~ /^[0-9]+$/) print local}' | sort -n -u | paste -sd ',' - | sed 's/^/Listening ports: /'
else
  printf 'ss: unavailable\n'
fi

section 'Compose configuration validation (no build/start; secrets not displayed)'
env_file="$SYSTEM_DIR/deploy/.env.production"
if [[ -f "$env_file" && ! -L "$env_file" ]] && command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  mode="$(mode_of "$env_file")"
  if [[ "$mode" =~ ^[0-7]*[0-7][0-7][0-7]$ ]] && (( (8#$mode & 077) == 0 )); then
    if docker compose --project-directory "$SYSTEM_DIR" --env-file "$env_file" \
      -f "$SYSTEM_DIR/docker-compose.prod.yml" config --quiet >/dev/null 2>&1; then
      printf 'Production Compose syntax/interpolation: PASS (output suppressed)\n'
    else
      printf 'Production Compose syntax/interpolation: FAIL (details withheld; do not paste secret-bearing diagnostics)\n'
    fi
  else
    printf 'Production Compose syntax/interpolation: NOT RUN (env file permissions are not mode 0600-equivalent)\n'
  fi
else
  printf 'Production Compose syntax/interpolation: NOT RUN (protected env file or Docker Compose unavailable)\n'
fi

printf '\nNo services were started/stopped; no images were built; no files, firewall rules, DNS, SSH, certificates, or ports were changed. This is a host preflight only.\n'
