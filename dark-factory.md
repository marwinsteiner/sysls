# Dark Code Factory — Operations Manual

## What This Is

A fully autonomous multi-agent software development system. Multiple Claude Code instances coordinate through Slack and GitHub to build the sysls trading framework with minimal human intervention.

You (the PM) monitor Slack. The agents do everything else.

## Architecture

```
+--------------------------------------------------------------+
|                    SUPERVISOR (supervisor.ps1)                |
|              monitors, restarts, heartbeats                   |
+----------+------------+------------+-------------------------+
| Architect|  Junior-1  |  Junior-2  |  Junior-3               |
| (main)   | (worktree) | (worktree) | (worktree)              |
|          |            |            |                          |
| plans    | implements | implements | implements               |
| reviews  | tests      | tests      | tests                   |
| merges   | opens PRs  | opens PRs  | opens PRs               |
+----------+------------+------------+-------------------------+
|                         Slack MCP                             |
|  #dev  #review  #blocked  #architecture  #announcements      |
+--------------------------------------------------------------+
|                      GitHub (PRs + CI)                        |
|  branch protection / CI checks / squash merge                |
+--------------------------------------------------------------+
```

## Two Operating Modes

### Mode A: Orchestrator (recommended)

One Architect session spawns junior subagents automatically. The junior agent definition in `.claude/agents/junior.md` has `isolation: worktree` — each junior gets its own worktree created by Claude Code natively.

```powershell
.\scripts\launch-architect.ps1 -Phase 0
```

The Architect reads CLAUDE.md, plans the phase, spawns junior agents for each task, reviews their PRs, and merges. One terminal, one command.

### Mode B: Multi-Terminal

Separate terminals, each agent independent. Juniors use `claude --worktree` for native isolation.

```powershell
# Terminal 1 - Architect (project root)
.\scripts\launch-architect.ps1 -Phase 0

# Terminal 2 - Junior 1
.\scripts\launch-junior.ps1 -JuniorId 1 -Phase 0

# Terminal 3 - Junior 2
.\scripts\launch-junior.ps1 -JuniorId 2 -Phase 0
```

### Mode C: Full Factory with Supervisor

The supervisor launches everything, monitors PIDs, auto-restarts crashed agents:

```powershell
.\scripts\supervisor.ps1 -Phase 0            # architect + 3 juniors
.\scripts\supervisor.ps1 -Phase 0 -Juniors 2  # architect + 2 juniors
```

## One-Time Setup

### Prerequisites
- Claude Code installed (`npm install -g @anthropic-ai/claude-code`)
- Claude Max subscription (multiple concurrent agents burn through Pro limits fast)
- GitHub CLI: `winget install GitHub.cli` then `gh auth login`
- Node.js (for Slack MCP): https://nodejs.org

### Step 1: Configure
```powershell
cd C:\Users\you\PycharmProjects\sysls
cp .env.example .env                       # fill in API keys
cp .mcp.json.example .mcp.json             # fill in Slack xoxp token
cp .claude\settings.local.json.example .claude\settings.local.json  # bypass mode
```

### Step 2: GitHub setup
```powershell
gh repo create sysls --private --source=. --push
.\scripts\setup-github.ps1                  # branch protection + labels
```

### Step 3: GitHub MCP
Create a PAT at https://github.com/settings/tokens with `repo` scope:
```powershell
claude mcp add -s user --transport http github https://api.githubcopilot.com/mcp -H "Authorization: Bearer YOUR_PAT"
```

### Step 4: Git hooks
```powershell
.\scripts\install-hooks.ps1
```

### Step 5: Verify
```powershell
claude         # open Claude Code
/mcp           # should show slack and github connected
/agents        # should show architect and junior agents
```

## Key Files

| File | Purpose |
|---|---|
| `.claude/agents/architect.md` | Architect agent definition (plans, reviews, merges) |
| `.claude/agents/junior.md` | Junior agent definition (`isolation: worktree`) |
| `.worktreeinclude` | Files copied into auto-created worktrees (.env, .mcp.json) |
| `.claude/commands/*.md` | 12 slash commands for interactive mode |
| `.github/workflows/ci.yml` | CI pipeline (ruff, mypy, pytest) |
| `scripts/supervisor.ps1` | Process supervisor with auto-restart |

## Your Role as PM

You monitor Slack. That's it.

| Channel | What to expect | Action needed |
|---|---|---|
| `#sysls-announcements` | Phase starts/completions | Read for progress |
| `#sysls-dev` | Task assignments, progress | Read for awareness |
| `#sysls-review` | PRs submitted, review feedback | Optional: read for quality |
| `#sysls-blocked` | **Agents stuck** | **Reply in thread** |
| `#sysls-architecture` | Design decisions | Chime in if you disagree |

**The rule:** Silence = approval. Only `#sysls-blocked` requires your input.

## What Can Go Wrong

| Problem | Automatic fix | Manual fix |
|---|---|---|
| Agent hits context limit | Supervisor restarts. New session reads Slack for context. | None needed |
| Agent crashes immediately | Supervisor detects <30s runtime, stops retrying. | Check log file |
| Agent loops | Repetitive Slack posts visible. | Reply in #sysls-dev to redirect |
| Merge conflict | Architect tells junior to rebase via Slack thread. | None needed |
| CI fails on PR | Architect requests changes. Junior fixes. | None needed |
| All restarts exhausted | Supervisor logs warning. | Fix issue, re-run supervisor |

## Stopping

- `Ctrl+C` in the supervisor terminal stops everything.
- Or close individual terminals for manual mode.
