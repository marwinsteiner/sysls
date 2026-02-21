# Slack MCP Configuration Options

The project `.mcp.json` must be configured with one of the following options.
Claude Code agents read this file automatically at session start.

## Option A: User OAuth Token (Recommended)

Best for: Full access including message search. Requires creating a Slack App.

**Setup:**
1. Go to https://api.slack.com/apps → Create New App → From Scratch
2. Name it "sysls-agent", select your workspace
3. Go to **OAuth & Permissions** → Add these Bot Token Scopes:
   - `channels:read`, `channels:history` — read channels
   - `chat:write` — send messages
   - `groups:read`, `groups:history` — read private channels
   - `im:write` — send DMs
   - `users:read` — list users
   - `search:read` — search messages
   - `reactions:write` — add reactions
   - `files:write` — upload files
4. Add these User Token Scopes (for search):
   - `search:read`
5. Install to workspace, copy the **User OAuth Token** (starts with `xoxp-`)
6. Create the 5 required channels and invite the bot to each

**`.mcp.json`:**
```json
{
  "mcpServers": {
    "slack": {
      "command": "npx",
      "args": ["-y", "slack-mcp-server@latest", "--transport", "stdio"],
      "env": {
        "SLACK_MCP_XOXP_TOKEN": "xoxp-your-token-here",
        "SLACK_MCP_ADD_MESSAGE_TOOL": "true",
        "SLACK_MCP_MARK_MESSAGES_AS_READ": "true"
      }
    }
  }
}
```

## Option B: Browser Session Tokens (Stealth Mode)

Best for: No admin approval needed. Uses your personal session tokens.

**Setup:**
1. Open your Slack workspace in Chrome
2. Open DevTools (F12) → Console tab
3. Type `allow pasting` then enter
4. Paste: `JSON.parse(localStorage.localConfig_v2).teams[document.location.pathname.match(/^\/client\/([A-Z0-9]+)/)[1]].token`
5. Copy the `xoxc-*` token
6. Go to Application tab → Cookies → find `d` cookie starting with `xoxd-`
7. Copy both tokens

**`.mcp.json`:**
```json
{
  "mcpServers": {
    "slack": {
      "command": "npx",
      "args": ["-y", "slack-mcp-server@latest", "--transport", "stdio"],
      "env": {
        "SLACK_MCP_XOXC_TOKEN": "xoxc-your-session-token",
        "SLACK_MCP_XOXD_TOKEN": "xoxd-your-cookie-token",
        "SLACK_MCP_ADD_MESSAGE_TOOL": "true",
        "SLACK_MCP_MARK_MESSAGES_AS_READ": "true"
      }
    }
  }
}
```

**Note:** Session tokens expire every 1-2 weeks. You'll need to re-extract them periodically.

## Option C: Slack's Official Remote MCP

Best for: Enterprise environments with admin-managed OAuth. Cleanest long-term solution.

**`.mcp.json`:**
```json
{
  "mcpServers": {
    "slack": {
      "type": "http",
      "url": "https://mcp.slack.com/mcp"
    }
  }
}
```

**Note:** Requires OAuth flow via `/mcp` command in Claude Code. Workspace admin must approve the MCP client integration.

## Required Channels

Regardless of which option you choose, create these channels in your workspace:

| Channel | Purpose | PM Notification Setting |
|---|---|---|
| `#sysls-announcements` | Milestones, phase completions, releases | Normal |
| `#sysls-dev` | Day-to-day progress, commits, agent discussion | Muted (check periodically) |
| `#sysls-review` | Code review requests and feedback threads | Normal |
| `#sysls-blocked` | Agent escalations needing human PM input | **All messages** |
| `#sysls-architecture` | Cross-cutting design decisions and discussions | Normal |

**Critical:** Set `#sysls-blocked` to notify you on every message. This is the only channel where agents will wait for your response before proceeding.
