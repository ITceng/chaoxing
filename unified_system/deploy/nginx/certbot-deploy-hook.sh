#!/usr/bin/env bash
set -euo pipefail

# Install as a root-owned Certbot deploy hook. It runs only after successful renewal.
nginx -t
systemctl reload nginx
