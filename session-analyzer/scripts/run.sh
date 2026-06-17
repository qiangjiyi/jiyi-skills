#!/usr/bin/env bash
# Run a session-analyzer script from any working directory.
#
# The SKILL.md commands are copied verbatim into the agent's shell, but the
# agent's cwd is the user's project dir, not the skill base — so `python3
# scripts/scan.py` fails with "can't open file .../scripts/scan.py". This
# wrapper resolves the skill base from its own location, then exec's the named
# script with the remaining args.
#
# Usage:
#   bash scripts/run.sh scan.py > /tmp/session_scan.json
#   bash scripts/run.sh close_agents.py
#   bash scripts/run.sh precleanup.py
#   bash scripts/run.sh server.py /tmp/session_scan.json
#   bash scripts/run.sh build_report.py /tmp/session_scan.json ~/Desktop/report.html

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <script-name> [args...]" >&2
  echo "       e.g. $0 scan.py / scan.py --flag / server.py path.json" >&2
  exit 64
fi

script_name="$1"
shift

# scripts/run.sh → skill base = parent of scripts/
skill_base="$(cd "$(dirname "$0")/.." && pwd)"
script_path="${skill_base}/scripts/${script_name}"

if [[ ! -f "${script_path}" ]]; then
  echo "run.sh: no such script: scripts/${script_name}" >&2
  echo "        (looked in ${skill_base}/scripts/)" >&2
  exit 66
fi

exec python3 -u "${script_path}" "$@"
