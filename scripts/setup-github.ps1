<#
.SYNOPSIS
    Configure GitHub branch protection on main.
    Requires GitHub CLI (gh) authenticated with admin access.
.USAGE
    .\scripts\setup-github.ps1
#>
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# Get repo name from git remote
$remote = git remote get-url origin 2>$null
if (-not $remote) {
    Write-Host "Error: No git remote 'origin' configured." -ForegroundColor Red
    Write-Host "Run: gh repo create sysls --private --source=. --push" -ForegroundColor Yellow
    exit 1
}

Write-Host "=== GitHub Repository Setup ===" -ForegroundColor Cyan
Write-Host "Remote: $remote" -ForegroundColor Gray

# Enable branch protection on main
Write-Host ""
Write-Host "Setting branch protection on 'main'..." -ForegroundColor Yellow

$body = @{
    required_status_checks = @{
        strict = $true
        contexts = @("test (3.12)")
    }
    enforce_admins = $false
    required_pull_request_reviews = $null
    restrictions = $null
    required_linear_history = $true
    allow_force_pushes = $false
    allow_deletions = $false
} | ConvertTo-Json -Depth 5

$result = $body | gh api repos/{owner}/{repo}/branches/main/protection --method PUT --input - 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "Branch protection enabled on main:" -ForegroundColor Green
    Write-Host "   - CI must pass before merge" -ForegroundColor Gray
    Write-Host "   - Linear history required (no merge commits)" -ForegroundColor Gray
    Write-Host "   - Force pushes blocked" -ForegroundColor Gray
} else {
    Write-Host "Could not set branch protection via API." -ForegroundColor Yellow
    Write-Host "   This is normal for free-tier private repos (requires Pro/Team)." -ForegroundColor Gray
    Write-Host "   Set it manually at: $($remote -replace '\.git$','')/settings/branches" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Recommended settings:" -ForegroundColor White
    Write-Host "   - Require status checks: 'test (3.12)'" -ForegroundColor Gray
    Write-Host "   - Require linear history" -ForegroundColor Gray
    Write-Host "   - Block force pushes" -ForegroundColor Gray
    Write-Host "   - (Optional) Require PR reviews" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== Labels ===" -ForegroundColor Cyan

$labels = @(
    @{ name = "phase-0"; color = "0E8A16"; description = "Foundation: events, types, config" },
    @{ name = "phase-1"; color = "1D76DB"; description = "Data layer" },
    @{ name = "phase-2"; color = "5319E7"; description = "Execution: single venue" },
    @{ name = "phase-3"; color = "D93F0B"; description = "Strategy framework" },
    @{ name = "phase-4"; color = "FBCA04"; description = "Backtesting" },
    @{ name = "phase-5"; color = "B60205"; description = "Multi-venue" },
    @{ name = "phase-6"; color = "006B75"; description = "Analytics and CLI" },
    @{ name = "phase-7"; color = "C2E0C6"; description = "Production hardening" },
    @{ name = "architect-review"; color = "D4C5F9"; description = "Awaiting architect review" },
    @{ name = "changes-requested"; color = "E99695"; description = "Review feedback pending" }
)

foreach ($label in $labels) {
    gh label create $label.name --color $label.color --description $label.description --force 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Created label: $($label.name)" -ForegroundColor Gray
    } else {
        Write-Host "  Label exists: $($label.name)" -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "GitHub setup complete." -ForegroundColor Green
