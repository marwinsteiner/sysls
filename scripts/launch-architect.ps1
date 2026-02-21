<#
.SYNOPSIS
    Launch the Architect Agent. Can run as orchestrator (spawns juniors as subagents)
    or as standalone reviewer in multi-terminal mode.
.USAGE
    .\scripts\launch-architect.ps1 -Phase 0                # Orchestrator: plans + spawns juniors
    .\scripts\launch-architect.ps1 -Phase 0 -Interactive    # TUI mode
#>
param(
    [Parameter(Mandatory=$true)]
    [int]$Phase,
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$logFile = "logs\architect-phase-${Phase}-${timestamp}.log"

$prompt = @"
You are the **Architect Agent** for the sysls trading framework.

FIRST ACTIONS:
1. Read CLAUDE.md completely.
2. Check Slack channels (#sysls-blocked, #sysls-review, #sysls-dev, #sysls-architecture, #sysls-announcements).
3. Post to #sysls-announcements: "Architect starting Phase $Phase."

PLANNING:
4. Review Phase $Phase requirements in CLAUDE.md.
5. Break it into tasks assignable to junior agents.
6. Post task assignments to #sysls-dev.

EXECUTION:
7. Spawn junior subagents for each task. They run in isolated worktrees automatically.
   Give each junior a complete, specific task description with module, branch name, requirements, and acceptance criteria.
8. Monitor #sysls-review for their PRs.
9. Review PRs: gh pr diff, gh pr checks, gh pr review --approve or --request-changes.
10. Merge approved PRs: gh pr merge --squash --delete-branch.
11. Post review feedback in Slack threads.
12. When all tasks merged, assign next batch or announce phase complete.

RULES:
- Do NOT write implementation code. Only review, plan, and merge.
- Keep main green: uv run pytest after merging.
- Use the 'junior' agent for implementation tasks — it runs in its own worktree.
"@

Write-Host "=== sysls Architect Agent ===" -ForegroundColor Magenta
Write-Host "Phase: $Phase" -ForegroundColor Yellow
Write-Host "Log:   $logFile" -ForegroundColor Yellow
Write-Host ""

if ($Interactive) {
    Write-Host "Mode: Interactive TUI" -ForegroundColor Green
    Write-Host "Use /agents to see available agents, or just tell it to spawn juniors." -ForegroundColor Gray
    claude
} else {
    Write-Host "Mode: Autonomous (headless)" -ForegroundColor Green
    Write-Host "Streaming to $logFile. Press Ctrl+C to stop." -ForegroundColor Gray
    Write-Host ""
    claude -p $prompt --dangerously-skip-permissions 2>&1 | Tee-Object -FilePath $logFile
}
