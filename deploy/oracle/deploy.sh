#!/usr/bin/env bash
set -euo pipefail

directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config_directory=${BASKET_CONFIG_DIR:-/opt/basket/config}
mode=${1:-stage}
if [[ $mode != stage && $mode != activate && $mode != stop-workers ]]; then
  echo 'Usage: deploy.sh [stage|activate|stop-workers]' >&2
  exit 1
fi
if [[ ! -f $config_directory/runtime.env || ! -f $config_directory/compose.env ]]; then
  echo 'Install runtime.env and compose.env in the configuration directory first.' >&2
  exit 1
fi

# Do not print the resolved Compose configuration: it includes private env_file values.
export BASKET_ENV_FILE=$config_directory/runtime.env
export BASKET_POSTGRES_ENV_FILE=$config_directory/postgres.env
export BASKET_DATABASE_URL_FILE=$config_directory/database-url
if [[ $mode == activate ]]; then
  if [[ ${BASKET_PREVIOUS_WORKERS_STOPPED:-} != true ]]; then
    echo 'Stop the previous host schedulers, then set BASKET_PREVIOUS_WORKERS_STOPPED=true.' >&2
    exit 1
  fi
  export NEWS_MONITOR_ENABLED=true LIVE_REFRESH_SECONDS=10
else
  export NEWS_MONITOR_ENABLED=false LIVE_REFRESH_SECONDS=0
fi

compose=(docker compose --project-name basket-oracle --env-file "$config_directory/compose.env" -f "$directory/compose.yaml")
if [[ -f $config_directory/local-postgres.enabled ]]; then
  compose+=(-f "$directory/compose.postgres.yaml")
fi
"${compose[@]}" config --quiet
if [[ $mode == stage ]]; then
  "${compose[@]}" build basket
fi
"${compose[@]}" up --detach --no-build --wait --wait-timeout 180
"${compose[@]}" exec -T basket python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:3000/api/health", timeout=5).read().decode())'
"${compose[@]}" ps
echo "Deployment mode: $mode. Verify public HTTPS before changing the live link."
