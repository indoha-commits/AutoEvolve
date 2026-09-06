#!/usr/bin/env bash
set -euo pipefail
command -v python3 >/dev/null || { echo "MISSING: python3" >&2; exit 1; }
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required")
print(f"PASS: Python {sys.version.split()[0]}")
PY
echo "PASS: no Docker, server, tunnel, database service, or open port required"
