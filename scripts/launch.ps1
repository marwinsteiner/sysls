<#
.SYNOPSIS
    Launch Claude Code autonomously for a specific phase.
.USAGE
    .\scripts\launch.ps1 -Phase 0
    .\scripts\launch.ps1 -Phase 2 -Interactive
#>
param(
    [Parameter(Mandatory=$true)]
    [int]$Phase,
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"

# Ensure we're in the project root
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# Create logs directory
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$logFile = "logs\phase-$Phase-$timestamp.log"

$prompt = @"
You are the Architect Agent for the sysls trading framework.

FIRST ACTIONS (do all of these before writing any code):
1. Read CLAUDE.md completely — it contains all project conventions, architecture, and operational rules.
2. Check Slack channels for context:
   - Read #sysls-blocked for any unresolved issues
   - Read #sysls-architecture for prior design decisions
   - Read #sysls-announcements for PM direction changes
3. Post to #sysls-announcements: "Starting Phase $Phase development session."
4. Review what Phase $Phase requires (defined in CLAUDE.md phase plan).
5. Post your implementation plan to #sysls-dev.

THEN: Execute the phase plan autonomously following ALL operational rules in CLAUDE.md:
- Commit early and often (atomic units, tests must pass)
- Push feature branches and open PRs for significant work (use gh pr create)
- Use Task subagent for code reviews on public APIs and modules >200 lines
- Post progress to #sysls-dev, milestones to #sysls-announcements
- Search the web freely for docs, examples, error solutions
- Escalate to #sysls-blocked ONLY after exhausting self-troubleshooting

Work until Phase $Phase is complete or you hit a blocker that requires human input.
"@

Write-Host "=== sysls Autonomous Development ===" -ForegroundColor Cyan
Write-Host "Phase: $Phase" -ForegroundColor Yellow
Write-Host "Log:   $logFile" -ForegroundColor Yellow
Write-Host ""

if ($Interactive) {
    Write-Host "Mode: Interactive (TUI)" -ForegroundColor Green
    Write-Host "Starting Claude Code... type /start-phase $Phase once loaded." -ForegroundColor Gray
    claude
} else {
    Write-Host "Mode: Autonomous (headless)" -ForegroundColor Green
    Write-Host "Streaming output to $logFile" -ForegroundColor Gray
    Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
    Write-Host ""
    claude -p $prompt --dangerously-skip-permissions 2>&1 | Tee-Object -FilePath $logFile
}
