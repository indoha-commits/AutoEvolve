#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
[[ $# -ge 1 ]] || { echo "Usage: $0 G3_HANDOFF.json [ASSET_ROOT]" >&2; exit 2; }
[[ -f "${root}/config/g3.env" ]] && source "${root}/config/g3.env"
handoff="$1"
asset_args=()
if [[ $# -ge 2 ]]; then asset_args=(--asset-root "$2"); fi
g3="${root}/.venv/bin/company-core-g3"
"${g3}" doctor --require x --require instagram
"${g3}" validate "${handoff}" "${asset_args[@]}"
"${g3}" draft "${handoff}" "${asset_args[@]}" --dry-run
echo "PASS: validation and draft preview. Remove --dry-run only when ready to create Buffer drafts."
