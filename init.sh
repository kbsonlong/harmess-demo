#!/bin/bash
set -euo pipefail

echo "=== Harness Init ==="
echo "cwd: $(pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "missing: uv"
  echo "install: https://docs.astral.sh/uv/"
  exit 1
fi

echo "=== Unit Tests ==="
uv run --group dev python -m pytest

if [ "${RUN_E2E:-0}" = "1" ]; then
  echo "=== E2E (Kind + Sandbox) ==="
  if ! command -v docker >/dev/null 2>&1; then echo "missing: docker" && exit 1; fi
  if ! command -v kind >/dev/null 2>&1; then echo "missing: kind" && exit 1; fi
  if ! command -v kubectl >/dev/null 2>&1; then echo "missing: kubectl" && exit 1; fi
  ./run_inspection.sh
else
  echo "skip e2e: set RUN_E2E=1 to run Kind + sandbox inspection"
fi

echo "=== Verification Complete ==="
echo "Next:"
echo "1) Read feature_list.json and pick ONE unfinished feature"
echo "2) Implement only that feature"
echo "3) Re-run ./init.sh before claiming done"
