#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d "venv" ]]; then
  echo "venv not found. Run: python3 -m venv venv"
  exit 1
fi

source venv/bin/activate

python main.py "$@"
