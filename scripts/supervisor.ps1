<#
.SYNOPSIS
    Supervisor that monitors and auto-restarts agent processes.
    Uses native Claude Code --worktree for junior isolation.
.USAGE
    .\scripts\supervisor.ps1 -Phase 0                  # Architect + 3 juniors
    .\scripts\supervisor.ps1 -Phase 0 -Juniors 2       # Architect + 2 juniors
    .\scripts\supervisor.ps1 -Phase 0 -ArchitectOnly   # Just the architect
    .\scripts\supervisor.ps1 -Phase 0 -JuniorsOnly     # Just 3 juniors (architect runs separately)
#>
param(
    [Parameter(Mandatory=$true)]
    [int]$Phase,
    [int]$Juniors = 3,
    [switch]$ArchitectOnly,
    [switch]$JuniorsOnly,
    [int]$HeartbeatMinutes = 30,
    [int]$MaxRestarts = 5
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

$ts = Get-Date -Format "yyyy-MM-dd-HHmmss"
$supervisorLog = "logs\supervisor-${ts}.log"

function Write-Log {
    param([string]$Message, [string]$Color = "White")
    $now = Get-Date -Format "HH:mm:ss"
    $line = "[$now] $Message"
    Write-Host $line -ForegroundColor $Color
    Add-Content -Path $supervisorLog -Value $line
}

$agents = @{}
$restartCounts = @{}

function Start-Agent {
    param([string]$Name, [string]$Script, [string[]]$ScriptArgs)

    if (-not $restartCounts.ContainsKey($Name)) { $restartCounts[$Name] = 0 }
    if ($restartCounts[$Name] -ge $MaxRestarts) {
        Write-Log "STOP $Name hit $MaxRestarts restarts. Check logs." "Red"
        return
    }

    $run = $restartCounts[$Name]
    $agentLog = "logs\${Name}-phase-${Phase}-${ts}-run${run}.log"
    Write-Log "Starting $Name (run $($run + 1))..." "Cyan"

    $allArgs = @("-NoProfile", "-File", $Script) + $ScriptArgs
    $proc = Start-Process -FilePath "powershell" `
        -ArgumentList $allArgs `
        -WindowStyle Normal `
        -PassThru `
        -RedirectStandardOutput $agentLog

    $agents[$Name] = @{
        Process   = $proc
        LogFile   = $agentLog
        StartTime = Get-Date
    }
    Write-Log "  PID $($proc.Id) -> $agentLog" "Gray"
}

function Test-AgentAlive {
    param([string]$Name)
    if (-not $agents.ContainsKey($Name)) { return $false }
    return -not $agents[$Name].Process.HasExited
}

# --- Main ---
Write-Log "=== sysls Dark Code Factory ===" "Magenta"
Write-Log "Phase: $Phase | Heartbeat: ${HeartbeatMinutes}min | Max restarts: $MaxRestarts" "Yellow"
Write-Log "Log: $supervisorLog" "Gray"
Write-Log ""

# Launch architect
if (-not $JuniorsOnly) {
    Start-Agent -Name "architect" `
        -Script "$projectRoot\scripts\launch-architect.ps1" `
        -ScriptArgs @("-Phase", "$Phase")
}

# Launch juniors (staggered by 30s)
if (-not $ArchitectOnly) {
    for ($j = 1; $j -le $Juniors; $j++) {
        if ($j -gt 1) {
            Write-Log "  Staggering 30s before junior ${j}..." "Gray"
            Start-Sleep -Seconds 30
        }
        Start-Agent -Name "junior-${j}" `
            -Script "$projectRoot\scripts\launch-junior.ps1" `
            -ScriptArgs @("-JuniorId", "$j", "-Phase", "$Phase")
    }
}

Write-Log ""
Write-Log "All agents launched. Monitoring... (Ctrl+C to stop all)" "Green"
Write-Log ""

$lastHeartbeat = Get-Date

try {
    while ($true) {
        Start-Sleep -Seconds 15

        foreach ($name in @($agents.Keys)) {
            if (-not (Test-AgentAlive $name)) {
                $info = $agents[$name]
                $runtime = (Get-Date) - $info.StartTime
                $mins = [math]::Round($runtime.TotalMinutes, 1)

                Write-Log "$name exited after ${mins}min (code $($info.Process.ExitCode))" "Red"

                if ($runtime.TotalSeconds -lt 30) {
                    Write-Log "  Immediate crash - not restarting (check config)" "Red"
                    $restartCounts[$name] = $MaxRestarts
                } else {
                    $restartCounts[$name]++
                    $rc = $restartCounts[$name]
                    Write-Log "  Restarting (${rc}/${MaxRestarts})..." "Yellow"

                    if ($name -eq "architect") {
                        Start-Agent -Name "architect" `
                            -Script "$projectRoot\scripts\launch-architect.ps1" `
                            -ScriptArgs @("-Phase", "$Phase")
                    } elseif ($name -match "^junior-(\d+)$") {
                        $jid = $Matches[1]
                        Start-Agent -Name $name `
                            -Script "$projectRoot\scripts\launch-junior.ps1" `
                            -ScriptArgs @("-JuniorId", "$jid", "-Phase", "$Phase")
                    }
                }
            }
        }

        # Heartbeat
        if (((Get-Date) - $lastHeartbeat).TotalMinutes -ge $HeartbeatMinutes) {
            $alive = @($agents.Keys | Where-Object { Test-AgentAlive $_ }).Count
            $total = $agents.Count
            Write-Log "--- Heartbeat: ${alive}/${total} agents alive ---" "Magenta"

            foreach ($name in $agents.Keys | Sort-Object) {
                $up = Test-AgentAlive $name
                $icon = if ($up) { "UP" } else { "DOWN" }
                $rc = $restartCounts[$name]
                Write-Log "  [$icon] $name (restarts: $rc)" $(if ($up) { "Green" } else { "Red" })
            }
            $lastHeartbeat = Get-Date
        }
    }
} finally {
    Write-Log ""
    Write-Log "Supervisor shutting down..." "Yellow"
    foreach ($name in $agents.Keys) {
        if (Test-AgentAlive $name) {
            Write-Log "  Stopping $name (PID $($agents[$name].Process.Id))" "Gray"
            Stop-Process -Id $agents[$name].Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Log "All agents stopped." "Red"
}
