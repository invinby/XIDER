$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path (Join-Path $PSScriptRoot '..\..\XGENT-WDS\install_agent.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-install-agent-test-' + [guid]::NewGuid().ToString('N'))

function icacls { $global:LASTEXITCODE = 0 }
function New-ScheduledTaskAction {
    param($Execute, $Argument, $WorkingDirectory)
    return [pscustomobject]@{ Execute = $Execute; Argument = $Argument; WorkingDirectory = $WorkingDirectory }
}
function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) return 'fixture-trigger' }
function New-ScheduledTaskSettingsSet { param([switch]$Hidden, $ExecutionTimeLimit, $RestartCount, $RestartInterval) return 'fixture-settings' }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel) return 'fixture-principal' }
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
    $global:XiderTestAction = $Action
}
function Start-ScheduledTask { param($TaskName) }

try {
    $venv = Join-Path $fixture 'venv\Scripts'
    New-Item -ItemType Directory -Path $venv -Force | Out-Null
    foreach ($file in @('.env', 'XGENT-WDS.exe', 'xgent_wds.py', 'venv\Scripts\pythonw.exe')) {
        Set-Content -LiteralPath (Join-Path $fixture $file) -Value 'fixture'
    }

    $global:XiderTestAction = $null
    & $installer -AgentDir $fixture -TaskName 'XIDER Fixture' -PreferPython
    if ($global:XiderTestAction.Execute -ne (Join-Path $venv 'pythonw.exe')) {
        throw 'PreferPython selected the stale executable instead of the downloaded Python source.'
    }

    $global:XiderTestAction = $null
    & $installer -AgentDir $fixture -TaskName 'XIDER Fixture'
    if ($global:XiderTestAction.Execute -ne (Join-Path $fixture 'XGENT-WDS.exe')) {
        throw 'Direct install no longer preserves executable-first behavior.'
    }

    Remove-Item -LiteralPath (Join-Path $venv 'pythonw.exe') -Force
    $global:XiderTestAction = $null
    $missingPythonRejected = $false
    try { & $installer -AgentDir $fixture -TaskName 'XIDER Fixture' -PreferPython }
    catch { $missingPythonRejected = $true }
    if (-not $missingPythonRejected -or $global:XiderTestAction) {
        throw 'PreferPython did not reject a missing Python runtime before task registration.'
    }
    Write-Host 'Windows agent executable selection fixture passed.'
}
finally {
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
    Remove-Item Function:\icacls,Function:\New-ScheduledTaskAction,Function:\New-ScheduledTaskTrigger,Function:\New-ScheduledTaskSettingsSet,Function:\New-ScheduledTaskPrincipal,Function:\Register-ScheduledTask,Function:\Start-ScheduledTask -ErrorAction SilentlyContinue
    Remove-Variable XiderTestAction -Scope Global -ErrorAction SilentlyContinue
}
