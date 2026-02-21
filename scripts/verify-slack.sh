#!/usr/bin/env bash
# Verify Slack MCP setup for sysls agent workflow.
#
# Run this before starting development to confirm:
# 1. Slack MCP server is reachable
# 2. Required channels exist
# 3. Agent can read and post messages
#
# Usage (from Claude Code):
#   bash scripts/verify-slack.sh
#
# This script is a reference — in practice, agents should verify
# Slack connectivity by running the MCP tools directly:
#
#   mcp__slack__channels_list()
#   → Confirm these channels exist:
#     #sysls-announcements
#     #sysls-dev
#     #sysls-review
#     #sysls-blocked
#     #sysls-architecture
#
#   mcp__slack__conversations_add_message(
#     channel_id="#sysls-dev",
#     payload=":wave: *sysls Agent Online* — Slack MCP connectivity verified."
#   )
#
# If channels are missing, ask the human PM (via whatever channel IS available)
# to create them. See .env.example for the full channel list and descriptions.

echo "=== sysls Slack MCP Setup Verification ==="
echo ""
echo "This is a reference script. Agents should verify Slack connectivity"
echo "by calling MCP tools directly in their Claude Code session."
echo ""
echo "Required Slack channels:"
echo "  #sysls-announcements  — Milestones, phase completions"
echo "  #sysls-dev            — Day-to-day progress, agent discussion"
echo "  #sysls-review         — Code review requests and feedback"
echo "  #sysls-blocked        — Escalations needing human PM input"
echo "  #sysls-architecture   — Cross-cutting design decisions"
echo ""
echo "Required environment:"
echo "  SYSLS_SLACK_XOXP_TOKEN  — User OAuth token (xoxp-*)"
echo "  OR"
echo "  SYSLS_SLACK_XOXC_TOKEN  — Browser session token (xoxc-*)"
echo "  SYSLS_SLACK_XOXD_TOKEN  — Browser cookie token (xoxd-*)"
echo ""

if [ -n "$SYSLS_SLACK_XOXP_TOKEN" ]; then
    echo "✓ SYSLS_SLACK_XOXP_TOKEN is set (xoxp mode)"
elif [ -n "$SYSLS_SLACK_XOXC_TOKEN" ] && [ -n "$SYSLS_SLACK_XOXD_TOKEN" ]; then
    echo "✓ SYSLS_SLACK_XOXC_TOKEN and SYSLS_SLACK_XOXD_TOKEN are set (stealth mode)"
else
    echo "✗ No Slack tokens found. Set SYSLS_SLACK_XOXP_TOKEN or both XOXC/XOXD tokens."
    echo "  See .env.example for details."
    exit 1
fi

echo ""
echo "Verify .mcp.json is present:"
if [ -f ".mcp.json" ]; then
    echo "✓ .mcp.json found"
    cat .mcp.json
else
    echo "✗ .mcp.json not found in project root"
    exit 1
fi

echo ""
echo "Setup looks good. Start a Claude Code session and run:"
echo "  mcp__slack__channels_list()"
echo "to confirm the MCP server is connected and channels are visible."
