#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m unittest discover -s "${root}/tests" -v
for script in "${root}"/scripts/*.sh; do bash -n "${script}"; done
python3 -m compileall -q "${root}/g3_runtime"
echo '{"proof":"g3_bundle","status":"PASS","publish_capability":false,"draft_only":true}'

