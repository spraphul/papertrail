#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="${PWD}/src${PYTHONPATH:+:${PYTHONPATH}}"

python3 -m unittest discover -s tests -v
python3 -m papertrail --home .papertrail doctor

if [[ "${1:-}" == "--verify" ]]; then
  python3 -m papertrail --home .papertrail init
  echo "PaperTrail verified. Run 'make demo' or 'make serve' next."
else
  exec python3 -m papertrail --home .papertrail serve
fi
