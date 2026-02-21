<#
.SYNOPSIS
    Set up git worktrees for multi-agent operation.
.USAGE
    .\scripts\setup-worktrees.ps1              # Create 3 junior worktrees
    .\scripts\setup-worktrees.ps1 -Count 2     # Create 2 junior worktrees
    .\scripts\setup-worktrees.ps1 -Cleanup     # Remove all worktrees
#>
param(
    [int]$Count = 3,
    [switch]$Cleanup
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if ($Cleanup) {
    Write-Host "Removing all junior worktrees..." -ForegroundColor Yellow
    for ($i = 1; $i -le 5; $i++) {
        $wt = "worktrees\junior-$i"
        if (Test-Path $wt) {
            git worktree remove $wt --force 2>$null
            Write-Host "  Removed $wt" -ForegroundColor Gray
        }
    }
    git worktree prune
    Write-Host "Done." -ForegroundColor Green
    exit 0
}

# Ensure worktrees directory exists
if (-not (Test-Path "worktrees")) {
    New-Item -ItemType Directory -Path "worktrees" | Out-Null
}

Write-Host "=== Setting up $Count junior worktrees ===" -ForegroundColor Cyan

for ($i = 1; $i -le $Count; $i++) {
    $wtPath = "worktrees\junior-$i"
    $branch = "junior-$i-workspace"

    if (Test-Path $wtPath) {
        Write-Host "  junior-$i: already exists, skipping" -ForegroundColor Gray
        continue
    }

    Write-Host "  Creating worktree: $wtPath (branch: $branch)" -ForegroundColor Yellow
    git worktree add $wtPath -b $branch main 2>&1 | Out-Null
    
    # Copy .mcp.json to worktree so the junior agent has Slack access
    if (Test-Path ".mcp.json") {
        Copy-Item ".mcp.json" "$wtPath\.mcp.json"
    }
    # Copy .env if it exists
    if (Test-Path ".env") {
        Copy-Item ".env" "$wtPath\.env"
    }

    Write-Host "  junior-$i: ready at $wtPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "Worktrees created. Launch junior agents with:" -ForegroundColor Cyan
for ($i = 1; $i -le $Count; $i++) {
    Write-Host "  .\scripts\launch-junior.ps1 -JuniorId $i -Phase 0" -ForegroundColor White
}
Write-Host ""
Write-Host "Launch the architect with:" -ForegroundColor Cyan
Write-Host "  .\scripts\launch-architect.ps1 -Phase 0" -ForegroundColor White
