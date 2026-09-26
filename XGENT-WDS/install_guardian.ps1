[CmdletBinding()]
param(
    [string]$AgentDir = $PSScriptRoot,
    [string]$TaskName = 'XIDER Guardian'
)

$ErrorActionPreference = 'Stop'
$AgentDir = (Resolve-Path $AgentDir).Path
$envPath = Join-Path $AgentDir '.env'
$guardian = Join-Path $AgentDir 'xider_guardian_wds.py'
$python = Join-Path $AgentDir 'venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $envPath)) { throw "Missing $envPath" }
if (-not (Test-Path -LiteralPath $guardian)) { throw "Missing $guardian" }
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python.exe -ErrorAction Stop).Source }

icacls.exe $envPath /inheritance:r | Out-Null
icacls.exe $envPath /grant:r "$($env:USERNAME):R" | Out-Null

$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}"' -f $guardian) -WorkingDirectory $AgentDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER Windows Guardian supervisor' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "[OK] $TaskName installed and started."
