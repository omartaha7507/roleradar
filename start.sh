#!/usr/bin/env bash
# Start RoleRadar on macOS or Linux. The first run creates a private Python
# environment and installs the packages. Ollama, Claude Code and the Chrome
# extension are set up as described in the README.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "First run: creating the Python environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
    [ -f settings.env ] || cp settings.example.env settings.env
fi

exec .venv/bin/python -m roleradar "$@"
