#!/usr/bin/env bash
# Usage: ./scripts/launch.sh <phase> [--interactive]
set -euo pipefail

PHASE="${1:?Usage: launch.sh <phase-number> [--interactive]}"
INTERACTIVE="${2:-}"

cd "$(dirname "$0")/.."
mkdir -p logs

TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
LOG="logs/phase-${PHASE}-${TIMESTAMP}.log"

PROMPT="You are the Architect Agent for the sysls trading framework.

FIRST ACTIONS (do all of these before writing any code):
1. Read CLAUDE.md completely — it contains all project conventions, architecture, and operational rules.
2. Check Slack channels for context:
   - Read #sysls-blocked for any unresolved issues
   - Read #sysls-architecture for prior design decisions
   - Read #sysls-announcements for PM direction changes
3. Post to #sysls-announcements: \"Starting Phase ${PHASE} development session.\"
4. Review what Phase ${PHASE} requires (defined in CLAUDE.md phase plan).
5. Post your implementation plan to #sysls-dev.

THEN: Execute the phase plan autonomously following ALL operational rules in CLAUDE.md:
- Commit early and often (atomic units, tests must pass)
- Push feature branches and open PRs for significant work (use gh pr create)
- Use Task subagent for code reviews on public APIs and modules >200 lines
- Post progress to #sysls-dev, milestones to #sysls-announcements
- Search the web freely for docs, examples, error solutions
- Escalate to #sysls-blocked ONLY after exhausting self-troubleshooting

Work until Phase ${PHASE} is complete or you hit a blocker that requires human input."

echo "=== sysls Autonomous Development ==="
echo "Phase: ${PHASE}"
echo "Log:   ${LOG}"

if [ "$INTERACTIVE" = "--interactive" ]; then
    echo "Mode: Interactive (TUI)"
    echo "Starting Claude Code... type /start-phase ${PHASE} once loaded."
    claude
else
    echo "Mode: Autonomous (headless)"
    echo "Streaming output to ${LOG}"
    echo "Press Ctrl+C to stop."
    echo ""
    claude -p "$PROMPT" --dangerously-skip-permissions 2>&1 | tee "$LOG"
fi
