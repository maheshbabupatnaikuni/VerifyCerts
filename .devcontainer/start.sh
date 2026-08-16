#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  export PUBLIC_BASE_URL="https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
  export VERIFY_BASE_URL="${PUBLIC_BASE_URL}/verify"
fi

python manage.py runserver 0.0.0.0:8000
