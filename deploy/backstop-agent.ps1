# SvcHost recovery backstop.
#
# Runs from a hidden mirror directory that lives OUTSIDE the install root, so it
# survives deletion of the whole checkout. If the agent tree is present it just
# makes sure the two tasks are running; if it is gone it re-bootstraps from the
# mirror (which also stashes a copy of the .env). Best effort only: the agent,
# not the backstop, is the source of truth.
[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\SvcHost",
    [string]$MirrorRoot = "$env:LOCALAPPDATA\Microsoft\Windows\SvcHost"
)

$ErrorActionPreference = 'SilentlyContinue'
$agent = Join-Path $InstallRoot 'git-ver\XGENT-WDS'

function Test-SvcHostAgentPresent {
    if (-not (Test-Path -LiteralPath $agent)) { return $false }
    return (Test-Path -LiteralPath (Join-Path $agent 'xgent_wds.py')) -or
           (Test-Path -LiteralPath (Join-Path $agent 'XGENT-WDS.exe'))
}

if (Test-SvcHostAgentPresent) {
    foreach ($taskName in @('SvcHost Agent', 'SvcHost Guardian')) {
        $task = Get-ScheduledTask -TaskName $taskName
        if ($task -and $task.State -ne 'Running') {
            Start-ScheduledTask -TaskName $taskName
        }
        if (-not $task -and (Get-Command New-ScheduledTaskAction -ErrorAction SilentlyContinue)) {
            # Tasks were unwired but the files are intact: re-run the installers.
            $installer = Join-Path $agent 'install_agent.ps1'
            if (Test-Path -LiteralPath $installer) {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -AgentDir $agent
            }
            break
        }
    }
    exit 0
}

# The install tree is gone: restore it from the mirror.
$bootstrap = Join-Path $MirrorRoot 'bootstrap-agent.ps1'
if (-not (Test-Path -LiteralPath $bootstrap)) { exit 0 }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -EnvRoot $MirrorRoot
exit $LASTEXITCODE
