#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -f .env ]]; then
  echo 'Create the server .env before deployment.' >&2
  exit 1
fi

compose=(docker compose --project-name giantkiller --env-file .env
  -f compose.yaml -f compose.deploy.yaml)
if [[ -f /etc/giantkiller/compose.override.yaml ]]; then
  compose+=(-f /etc/giantkiller/compose.override.yaml)
fi

"${compose[@]}" config --quiet
"${compose[@]}" build api
"${compose[@]}" up -d --wait --wait-timeout 120 postgres
"${compose[@]}" run --rm --no-deps api alembic upgrade head
"${compose[@]}" up -d --no-build --wait --wait-timeout 180 api
"${compose[@]}" ps
