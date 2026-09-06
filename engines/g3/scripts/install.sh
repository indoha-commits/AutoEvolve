#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
"${root}/scripts/preflight.sh"
python3 -m venv "${root}/.venv"
"${root}/.venv/bin/python" -m pip install --disable-pip-version-check -e "${root}"
mkdir -p "${root}/state" "${root}/backups"
if [[ ! -f "${root}/config/g3.env" ]]; then
  cp "${root}/config/g3.env.example" "${root}/config/g3.env"
fi
chmod 600 "${root}/config/g3.env"
echo "Installed. Fill config/g3.env, then run: source config/g3.env"
