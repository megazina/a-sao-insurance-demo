#!/bin/sh
# Serves the portal on 8081 unless PORT is set.
set -e
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/run_ui.py
