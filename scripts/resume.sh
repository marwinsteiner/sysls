#!/usr/bin/env bash
# Usage: ./scripts/resume.sh [session-id]
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p logs

TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
LOG="logs/resume-${TIMESTAMP}.log"

echo "=== sysls Session Resume ==="

if [ "${1:-}" ]; then
    echo "Resuming session: $1"
    claude --resume "$1" 2>&1 | tee "$LOG"
else
    echo "Resuming most recent session..."
    claude --continue 2>&1 | tee "$LOG"
fi
