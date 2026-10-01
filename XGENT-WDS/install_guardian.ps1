[CmdletBinding()]
param(
    [string]$AgentDir = $PSScriptRoot,
    [string]$TaskName = 'XIDER Guardian',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$AgentDir = (Resolve-Path $AgentDir).Path
$envPath = Join-Path $AgentDir '.env'
$guardian = Join-Path $AgentDir 'xider_guardian_wds.py'
$python = Join-Path $AgentDir 'venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $envPath)) { throw "Missing $envPath" }
if (-not (Test-Path -LiteralPath $guardian)) { throw "Missing $guardian" }
if (-not (Test-Path -LiteralPath $python)) {
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $systemPython) { throw 'Guardian Python runtime is missing.' }
    $python = $systemPython.Source
}
foreach ($commandName in @(
    'New-ScheduledTaskAction', 'New-ScheduledTaskTrigger',
    'New-ScheduledTaskSettingsSet', 'New-ScheduledTaskPrincipal',
    'Register-ScheduledTask', 'Start-ScheduledTask'
)) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required Windows Scheduled Tasks command is unavailable: $commandName"
    }
}
if ($PreflightOnly) {
    Write-Host '[OK] Guardian script, config, Python runtime, and Task Scheduler commands are present. Nothing changed.'
    return
}

icacls $envPath /inheritance:r | Out-Null
icacls $envPath /grant:r "$($env:USERNAME):R" | Out-Null

$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}"' -f $guardian) -WorkingDirectory $AgentDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER Windows Guardian supervisor' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "[OK] $TaskName installed and started."
