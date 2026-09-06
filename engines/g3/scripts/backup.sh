#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
ledger="${1:-${root}/state/g3.sqlite3}"
[[ -f "${ledger}" ]] || { echo "Ledger not found: ${ledger}" >&2; exit 1; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="${root}/backups/g3-ledger-${stamp}.sqlite3"
cp "${ledger}" "${target}"
echo "${target}"
