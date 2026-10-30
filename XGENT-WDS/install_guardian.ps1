[CmdletBinding()]
param(
    [string]$AgentDir = $PSScriptRoot,
    [string]$TaskName = 'XIDER Guardian',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$AgentDir = (Resolve-Path $AgentDir).Path
$envPath = Join-Path $AgentDir '.env'
$guardianExe = Join-Path $AgentDir 'XIDER-Guard-Keeper.exe'
$guardian = Join-Path $AgentDir 'xider_guardian_wds.py'
$pythonw = Join-Path $AgentDir 'venv\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $envPath)) { throw "Missing $envPath" }
$packagedGuardian = Test-Path -LiteralPath $guardianExe -PathType Leaf
if (-not $packagedGuardian) {
    if (-not (Test-Path -LiteralPath $guardian -PathType Leaf)) { throw "Missing $guardian and $guardianExe" }
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        $systemPythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if (-not $systemPythonw) { throw 'A windowless Guardian Python runtime (pythonw.exe) is missing.' }
        $pythonw = $systemPythonw.Source
    }
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
$guardianAction = if ($packagedGuardian) {
    New-ScheduledTaskAction -Execute $guardianExe -WorkingDirectory $AgentDir
} else {
    # python.exe opens a console window at every Guardian restart. Use the
    # paired windowless runtime so recovery remains silent without hiding the
    # visible task, logs, stop controls, or uninstall path.
    New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $guardian) -WorkingDirectory $AgentDir
}
if ($PreflightOnly) {
    Write-Host '[OK] Packaged Guardian or windowless Python fallback and Task Scheduler commands are present. Nothing changed.'
    return
}

icacls $envPath /grant:r "$($env:USERNAME):R" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'icacls failed to grant the current account read access to the XIDER .env file.' }
icacls $envPath /inheritance:r | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'icacls failed to remove inherited access from the XIDER .env file.' }

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $guardianAction -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER Windows Guard Keeper supervisor' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "[OK] $TaskName installed and started."
