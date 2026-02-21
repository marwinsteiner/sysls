# Getting Started — Autonomous Development with sysls

This guide gets you from zero to agents building the framework unassisted.

## Prerequisites

1. **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`
2. **Anthropic API key** or **Claude Max subscription** (for the Claude Code quota)
3. **Node.js 18+** (for the Slack MCP server, which runs via npx)
4. **Python 3.12+** and **uv** (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
5. **A Slack workspace** you control (free tier is fine)

## Step 1: Clone and configure

```bash
git clone <your-repo-url>
cd sysls

# Copy config templates
cp .env.example .env
cp .mcp.json.example .mcp.json
cp .claude/settings.local.json.example .claude/settings.local.json
```

## Step 2: Set up Slack

See `docs/slack-setup.md` for detailed instructions. The short version:

1. **Create a Slack App** at https://api.slack.com/apps with these scopes:
   `channels:read`, `channels:history`, `chat:write`, `groups:read`, `groups:history`,
   `im:write`, `users:read`, `search:read`, `reactions:write`, `files:write`

2. **Create 5 channels** in your workspace:
   - `#sysls-announcements` — milestones (set to normal notifications)
   - `#sysls-dev` — day-to-day progress (mute this, check periodically)
   - `#sysls-review` — code review threads (normal notifications)
   - `#sysls-blocked` — **set to notify on ALL messages** — this is the only channel where agents wait for you
   - `#sysls-architecture` — design discussions (normal notifications)

3. **Add your token** to `.mcp.json`:
   ```json
   {
     "mcpServers": {
       "slack": {
         "command": "npx",
         "args": ["-y", "slack-mcp-server@latest", "--transport", "stdio"],
         "env": {
           "SLACK_MCP_XOXP_TOKEN": "xoxp-YOUR-ACTUAL-TOKEN",
           "SLACK_MCP_ADD_MESSAGE_TOOL": "true",
           "SLACK_MCP_MARK_MESSAGES_AS_READ": "true"
         }
       }
     }
   }
   ```

4. **Invite the bot** to all 5 channels (go to each channel → Integrations → Add apps).

## Step 3: Verify the setup

```bash
# Quick verification — launch Claude Code interactively
claude

# Inside the Claude Code session, run:
# /mcp
# → Confirm the slack server is connected and shows its tools
#
# Then ask Claude: "List all Slack channels you can see"
# → Should show the 5 sysls channels
#
# Then: "Post a test message to #sysls-dev saying 'Agent online, setup verified'"
# → Confirm the message appears in Slack
#
# Exit with /exit
```

## Step 4: Understand the settings files

```
sysls/
├── .claude/
│   ├── settings.json              # COMMITTED — shared project permissions
│   │                              # Granular allow list for all tools agents need:
│   │                              # file ops, git, python, uv, ruff, mypy,
│   │                              # docker, web search, and all Slack MCP tools.
│   │                              # Deny list blocks dangerous ops (rm -rf /, sudo, .env reads).
│   │
│   ├── settings.local.json        # NOT COMMITTED — your personal overrides
│   │                              # Set "defaultMode": "bypassPermissions" here
│   │                              # for fully autonomous operation.
│   │                              # Or remove it to get permission prompts.
│   │
│   └── commands/                  # Slash commands for agent workflows
│       ├── start-phase.md         # /start-phase N — begin a development phase
│       ├── commit.md              # /commit — commit with discipline
│       ├── code-review.md         # /code-review — hierarchical review
│       ├── stuck.md               # /stuck — escalation protocol
│       ├── new-venue.md           # /new-venue — add a venue adapter
│       ├── new-connector.md       # /new-connector — add a data connector
│       ├── new-strategy.md        # /new-strategy — add an example strategy
│       ├── integration-test.md    # /integration-test — write integration tests
│       └── review-architecture.md # /review-architecture — audit consistency
│
├── .mcp.json                      # NOT COMMITTED — Slack MCP server config with your token
├── CLAUDE.md                      # COMMITTED — project intelligence, architecture, all rules
└── .gitignore                     # Excludes .env, .mcp.json, settings.local.json
```

**How permissions cascade (highest to lowest priority):**
1. `deny` rules always win
2. `settings.local.json` overrides `settings.json`
3. `settings.json` is the baseline

**Two modes of operation:**

| Mode | How | When |
|---|---|---|
| **Granular (recommended)** | Use `settings.json` allow list as-is. Agent asks permission only for unlisted tools. | Day-to-day development |
| **Full bypass** | Add `"defaultMode": "bypassPermissions"` to `settings.local.json` | Kicking off a phase you trust to run unattended |

## Step 5: Launch

### Option A: Headless (fire and forget)

```bash
# Make scripts executable
chmod +x scripts/launch.sh scripts/resume.sh

# Create logs directory
mkdir -p logs

# Launch Phase 0 — Foundation
./scripts/launch.sh 0

# Monitor progress in Slack. That's it.
# Agents will post to #sysls-dev, #sysls-review, #sysls-announcements.
# You only need to respond to #sysls-blocked messages.
```

What happens when you run this:
1. Claude Code starts in headless mode with `--dangerously-skip-permissions`
2. It reads `CLAUDE.md`, the full architecture and all rules
3. It checks Slack for any prior context
4. It posts "Starting Phase 0" to `#sysls-announcements`
5. It plans the implementation and posts the plan to `#sysls-dev`
6. It starts writing code, committing, posting progress, requesting reviews
7. When done, it posts the milestone to `#sysls-announcements`
8. Output streams to `logs/phase-0-YYYYMMDD-HHMMSS.log`

### Option B: Interactive (watch it work)

```bash
./scripts/launch.sh 0 --interactive
```

This opens the full Claude Code TUI. You can watch it work in real-time and intervene by typing. It uses `acceptEdits` mode (auto-approves file edits, prompts for bash commands).

### Option C: Manual (most control)

```bash
claude
# Then type: /start-phase 0
```

This opens Claude Code normally. You use slash commands to direct it.

## Step 6: Between phases

When Phase N finishes (you'll get a `#sysls-announcements` message):

```bash
# Start the next phase
./scripts/launch.sh 1

# Or resume if it stopped mid-phase (Claude Code sessions can time out)
./scripts/resume.sh

# Or resume a specific session
./scripts/resume.sh <session-id>
```

## Step 7: Your role as PM

Your workflow is:
1. **Watch `#sysls-announcements`** for milestone completions
2. **Respond to `#sysls-blocked`** when agents are stuck — this is the only channel that blocks them
3. **Occasionally skim `#sysls-dev`** to see what's happening (muted, low urgency)
4. **Review `#sysls-architecture`** for design discussions you want to weigh in on
5. **Post direction changes** in the relevant channel — agents read Slack at session start

Things you might need to do:
- Provide API keys when agents need them (they'll ask in `#sysls-blocked`)
- Make calls on ambiguous design decisions (they'll discuss in `#sysls-architecture`)
- Approve or redirect when a phase is complete
- Re-launch if a session times out or errors out

## Troubleshooting

**"Slack MCP server not connected"**
→ Check that `.mcp.json` exists and has a valid token. Run `claude` interactively and type `/mcp` to check.

**"Permission denied" on bash commands**
→ Check `.claude/settings.json` allow list. Add the missing command pattern. Or use `settings.local.json` with `bypassPermissions`.

**Session timed out mid-phase**
→ `./scripts/resume.sh` to continue the most recent session, or check `logs/` for the session ID.

**Agent posting to wrong channel / not reading Slack**
→ Verify the bot is invited to all 5 channels. Run `mcp__slack__channels_list()` interactively to confirm visibility.

**Rate limits on Anthropic API**
→ Increase your plan quota or add delays. For heavy autonomous use, Claude Max subscription is recommended.
