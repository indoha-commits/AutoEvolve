#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"

install_engine() {
  local directory="$1"
  local extras="${2:-}"
  "$PYTHON_BIN" -m venv "$directory/.venv"
  "$directory/.venv/bin/python" -m pip install --upgrade pip
  if [[ -n "$extras" ]]; then
    "$directory/.venv/bin/python" -m pip install -e "$directory[$extras]"
  else
    "$directory/.venv/bin/python" -m pip install -e "$directory"
  fi
}

install_engine "$ROOT_DIR/engines/g1"
install_engine "$ROOT_DIR/engines/g2" "network-voice"
install_engine "$ROOT_DIR/engines/g3"
