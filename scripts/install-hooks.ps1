<#
.SYNOPSIS
    Install git hooks for sysls project.
.USAGE
    .\scripts\install-hooks.ps1
#>
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$hookSource = Join-Path $projectRoot "scripts\pre-commit"
$hookDest = Join-Path $projectRoot ".git\hooks\pre-commit"

if (-not (Test-Path (Join-Path $projectRoot ".git"))) {
    Write-Host "Error: .git directory not found. Run from project root." -ForegroundColor Red
    exit 1
}

Copy-Item -Path $hookSource -Destination $hookDest -Force
Write-Host "Installed pre-commit hook to .git/hooks/pre-commit" -ForegroundColor Green
Write-Host "Tests, ruff check, and ruff format will run before every commit." -ForegroundColor Gray
