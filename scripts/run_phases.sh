#!/usr/bin/env bash
# Run validation phases in order:
# 1) unit tests
# 2) integration tests (only if phase 1 passes)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Phase 1: Running unit tests..."
venv/bin/pytest tests/unit -q

echo "Phase 2: Running integration tests..."
venv/bin/pytest tests/integration -q
