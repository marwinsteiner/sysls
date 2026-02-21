<#
.SYNOPSIS
    Resume the most recent Claude Code session or a specific one.
.USAGE
    .\scripts\resume.ps1                         # Resume most recent
    .\scripts\resume.ps1 -SessionId "abc123"     # Resume specific session
#>
param(
    [string]$SessionId
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

$timestamp = Get-Date -Format "yyyy-MM-dd-HHmmss"
$logFile = "logs\resume-$timestamp.log"

Write-Host "=== sysls Session Resume ===" -ForegroundColor Cyan

if ($SessionId) {
    Write-Host "Resuming session: $SessionId" -ForegroundColor Yellow
    claude --resume $SessionId 2>&1 | Tee-Object -FilePath $logFile
} else {
    Write-Host "Resuming most recent session..." -ForegroundColor Yellow
    claude --continue 2>&1 | Tee-Object -FilePath $logFile
}
