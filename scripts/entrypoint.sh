#!/bin/bash
set -euo pipefail

/scripts/configure.sh

# Fresh log for every start; rsyslog writes it, supervisord streams it to stdout.
: > /var/log/maillog

# Optional web UI (see README). It is only started when a password is set.
rm -f /etc/supervisord.d/webui.ini
if [[ "${WEBUI_ENABLED:-false}" =~ ^([Tt]rue|TRUE|[Yy]es|1)$ ]]; then
  if [ -z "${WEBUI_PASSWORD:-}" ]; then
    echo "[entrypoint] WARNING: WEBUI_ENABLED is set but WEBUI_PASSWORD is empty: web UI not started" >&2
  else
    cp /etc/supervisord.d/webui.ini.disabled /etc/supervisord.d/webui.ini
    echo "[entrypoint] web UI enabled on port ${WEBUI_PORT:-8080}"
  fi
fi

exec "$@"
