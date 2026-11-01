#!/usr/bin/env bash
set -u

section() { printf '\n== %s ==\n' "$1"; }
report_cmd() {
  local label="$1"; shift
  printf '%-28s' "$label"
  if "$@"; then :; else printf 'not available / check failed\n'; fi
}

section 'Environment identity (run on the target only)'
printf 'User: '; id -un 2>/dev/null || true
printf 'UID/groups: '; id 2>/dev/null || true
printf 'HOME: %s\n' "${HOME:-not-set}"
printf 'Kernel: '; uname -srmo 2>/dev/null || true
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  printf 'OS: %s (%s)\n' "${PRETTY_NAME:-unknown}" "${VERSION_ID:-unknown}"
else
  printf 'OS: /etc/os-release unavailable\n'
fi
printf 'Python: '; python3 --version 2>&1 || true
printf 'Architecture: '; uname -m 2>/dev/null || true
printf 'CPU cores: '; getconf _NPROCESSORS_ONLN 2>/dev/null || true
printf 'Memory: '; if command -v free >/dev/null 2>&1; then free -h | awk '/^Mem:/ {print $2 " total, " $7 " available"}'; else awk '/MemTotal|MemAvailable/ {printf "%s=%s%s ", $1, $2, $3} END {print ""}' /proc/meminfo; fi
printf 'Filesystem: '; df -h "${HOME:-/}" /var/lib/docker 2>/dev/null | awk 'NR > 1 {print $6 ": " $4 " free of " $2}' || true

section 'Docker and Compose'
if command -v docker >/dev/null 2>&1; then
  docker --version 2>&1 || true
  docker compose version 2>&1 || true
  docker info --format 'Docker daemon: server={{.ServerVersion}} storage={{.Driver}}' 2>&1 || true
  if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    printf 'Current user belongs to docker group (equivalent to high host privilege).\n'
  else
    printf 'Current user is not listed in the docker group.\n'
  fi
else
  printf 'Docker CLI not installed.\n'
fi

section 'Optional host tooling (versions only)'
for tool in nginx age curl openssl systemctl; do
  if command -v "$tool" >/dev/null 2>&1; then
    case "$tool" in
      nginx) printf 'nginx: '; nginx -v 2>&1 || true ;;
      age) printf 'age: '; age --version 2>&1 || true ;;
      curl) printf 'curl: '; curl --version 2>/dev/null | head -n 1 || true ;;
      openssl) printf 'openssl: '; openssl version 2>&1 || true ;;
      systemctl) printf 'systemd: '; systemctl --version 2>/dev/null | head -n 1 || true ;;
    esac
  else
    printf '%s: not installed\n' "$tool"
  fi
done

section 'Network and listening ports'
if command -v ip >/dev/null 2>&1; then ip route show default 2>&1 || true; fi
if command -v getent >/dev/null 2>&1; then
  printf 'Docker registry DNS: '
  getent ahosts registry-1.docker.io 2>/dev/null | awk '{print $1}' | sort -u | paste -sd ',' - || printf 'unresolved\n'
fi
if command -v ss >/dev/null 2>&1; then
  printf 'Listening TCP sockets (review ports 80/443/8080/5432/6379):\n'
  ss -ltn 2>&1 || true
else
  printf 'ss command unavailable.\n'
fi

section 'Filesystem and service permissions'
for path in /home/agentuser /var/lib/docker /etc/nginx /var/log/nginx; do
  if [[ -e "$path" ]]; then
    stat -c '%A %U:%G %n' "$path" 2>/dev/null || true
    [[ -w "$path" ]] && printf '  writable by current user: yes\n' || printf '  writable by current user: no\n'
  else
    printf '%s: not present\n' "$path"
  fi
done

section 'Domain and HTTPS readiness (optional, outbound probe only)'
if [[ -n "${PUBLIC_DOMAIN:-}" ]]; then
  printf 'Domain: %s\n' "$PUBLIC_DOMAIN"
  printf 'DNS: '
  if command -v getent >/dev/null 2>&1 && getent ahosts "$PUBLIC_DOMAIN" >/dev/null 2>&1; then
    getent ahosts "$PUBLIC_DOMAIN" | awk '{print $1}' | sort -u | paste -sd ',' -
  else
    printf 'not resolving\n'
  fi
  if command -v curl >/dev/null 2>&1; then
    curl --silent --show-error --max-time 8 -o /dev/null \
      -w 'Existing HTTPS endpoint: HTTP %{http_code}; TLS verification result %{ssl_verify_result}\n' \
      "https://${PUBLIC_DOMAIN}/" 2>&1 || true
  else
    printf 'curl unavailable; HTTPS endpoint not probed.\n'
  fi
else
  printf 'Set PUBLIC_DOMAIN in the shell to perform a DNS/TLS readiness probe. No request was sent.\n'
fi

printf '\nThis script reports the host where it is executed. It does not install packages, change firewall rules, open ports, or deploy services.\n'
