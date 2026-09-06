#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  echo ".env already exists; leaving it unchanged."
else
  cp .env.example .env

  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl is required to generate local secrets." >&2
    exit 1
  fi

  while grep -q '=GENERATE_ME' .env; do
    secret="$(openssl rand -hex 32)"
    sed -i "0,/=GENERATE_ME/s//=${secret}/" .env
  done

  chmod 600 .env
  echo "Created .env with local secrets. Add provider credentials when needed."
fi

mkdir -p data projects
touch data/.gitkeep projects/.gitkeep

for name in code_workspaces services; do
  if [[ ! -f "config/${name}.json" ]]; then
    cp "config/${name}.example.json" "config/${name}.json"
  fi
done

if [[ ! -f engines/g3/config/g3.env ]]; then
  cp engines/g3/config/g3.env.example engines/g3/config/g3.env
fi
