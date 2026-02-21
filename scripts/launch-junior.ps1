<#
.SYNOPSIS
    Launch a Junior Agent in its own git worktree (native Claude Code isolation).
    Use this for multi-terminal mode where each junior runs in a separate terminal.
.USAGE
    .\scripts\launch-junior.ps1 -JuniorId 1 -Phase 0
    .\scripts\launch-junior.ps1 -JuniorId 2 -Phase 0 -Interactive
#>
param(
    [Parameter(Mandatory=$true)]
    [int]$JuniorId,
    [Parameter(Mandatory=$true)]
    [int]$Phase,
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$logFile = "logs\junior-${JuniorId}-phase-${Phase}-${timestamp}.log"

$prompt = @"
You are **Junior Agent $JuniorId** for the sysls trading framework.

FIRST ACTIONS:
1. Read CLAUDE.md completely — especially Coding Conventions and Architecture.
2. Pull latest: git checkout main && git pull origin main
3. Check Slack:
   - #sysls-dev for YOUR task assignments (look for "Junior-$JuniorId" or unassigned tasks)
   - #sysls-review for review feedback on your open PRs
   - #sysls-architecture for design decisions
4. Post to #sysls-dev: "Junior-$JuniorId online, reading assignments."

WHEN YOU FIND YOUR TASK:
5. Create branch from main: git checkout -b <branch-name> main
6. Implement following ALL CLAUDE.md conventions.
7. Write tests alongside implementation.
8. Commit atomically as you go.
9. When done: git push -u origin <branch-name>
10. Open PR: gh pr create --base main --title "<layer>: <desc>" --body "<summary>"
11. Post to #sysls-review with PR link.

RULES:
- NEVER merge your own PRs. Only the Architect merges.
- NEVER push to main directly. Always feature branches + PRs.
- Follow ALL coding conventions in CLAUDE.md.
- If stuck: search web, try 3 fixes, isolate, then post to #sysls-blocked.
- If no task assigned: post to #sysls-dev that you're ready for work.
"@

Write-Host "=== sysls Junior Agent $JuniorId ===" -ForegroundColor Cyan
Write-Host "Phase: $Phase" -ForegroundColor Yellow
Write-Host "Log:   $logFile" -ForegroundColor Yellow
Write-Host "(Using native --worktree for isolation)" -ForegroundColor Gray
Write-Host ""

if ($Interactive) {
    Write-Host "Mode: Interactive TUI (in worktree)" -ForegroundColor Green
    claude --worktree "junior-$JuniorId"
} else {
    Write-Host "Mode: Autonomous (headless, in worktree)" -ForegroundColor Green
    Write-Host "Streaming to $logFile. Press Ctrl+C to stop." -ForegroundColor Gray
    Write-Host ""
    claude --worktree "junior-$JuniorId" -p $prompt --dangerously-skip-permissions 2>&1 | Tee-Object -FilePath $logFile
}
