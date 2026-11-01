#!/usr/bin/env bash
set -euo pipefail

minimum_days="${1:-30}"
certificate="${2:-${TLS_FULLCHAIN_PATH:-}}"
if [[ ! "$minimum_days" =~ ^[0-9]+$ || ${#minimum_days} -gt 3 ]]; then
  printf 'Usage: %s [minimum-validity-days:1..365] [fullchain.pem]\n' "$0" >&2
  exit 2
fi
minimum_days=$((10#$minimum_days))
if (( minimum_days < 1 || minimum_days > 365 )); then
  printf 'Usage: %s [minimum-validity-days:1..365] [fullchain.pem]\n' "$0" >&2
  exit 2
fi
if [[ -z "$certificate" && -n "${PUBLIC_DOMAIN:-}" ]]; then
  certificate="/etc/letsencrypt/live/${PUBLIC_DOMAIN}/fullchain.pem"
fi
if [[ -z "$certificate" || ! -r "$certificate" ]]; then
  printf 'Set TLS_FULLCHAIN_PATH or PUBLIC_DOMAIN and provide a readable fullchain.pem.\n' >&2
  exit 2
fi
if ! command -v openssl >/dev/null 2>&1; then
  printf 'OpenSSL is required.\n' >&2
  exit 2
fi

expiry="$(openssl x509 -in "$certificate" -noout -enddate | sed 's/^notAfter=//')"
if openssl x509 -in "$certificate" -noout -checkend "$((minimum_days * 86400))" >/dev/null; then
  printf 'Certificate is valid beyond %s days; expiry: %s\n' "$minimum_days" "$expiry"
else
  printf 'Certificate is expired or expires within %s days; expiry: %s\n' "$minimum_days" "$expiry" >&2
  exit 1
fi
