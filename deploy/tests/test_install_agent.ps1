$ErrorActionPreference = 'Stop'
$installer = (Resolve-Path (Join-Path $PSScriptRoot '..\..\XGENT-WDS\install_agent.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-install-agent-test-' + [guid]::NewGuid().ToString('N'))
$utf8 = New-Object System.Text.UTF8Encoding($false)

function icacls { $global:XiderAclCallCount++; $global:LASTEXITCODE = 0 }
function New-ScheduledTaskAction {
    param($Execute, $Argument, $WorkingDirectory)
    return [pscustomobject]@{ Execute = $Execute; Argument = $Argument; WorkingDirectory = $WorkingDirectory }
}
function New-ScheduledTaskTrigger { param([switch]$AtLogOn, $User) return 'fixture-trigger' }
function New-ScheduledTaskSettingsSet { param([switch]$Hidden, $ExecutionTimeLimit, $RestartCount, $RestartInterval) return 'fixture-settings' }
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel) return 'fixture-principal' }
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Settings, $Principal, $Description, [switch]$Force)
    $global:XiderRegisterCount++
    $global:XiderTestActions[$TaskName] = $Action
}
function Start-ScheduledTask { param($TaskName) $global:XiderStartCount++ }

try {
    $venv = Join-Path $fixture 'venv\Scripts'
    New-Item -ItemType Directory -Path $venv -Force | Out-Null
    foreach ($file in @(
        '.env', 'XGENT-WDS.exe', 'xgent_wds.py', 'xider_guardian_wds.py',
        'venv\Scripts\pythonw.exe', 'venv\Scripts\python.exe'
    )) {
        Set-Content -LiteralPath (Join-Path $fixture $file) -Value 'fixture'
    }
    $secureEnv = @(
        'SHARED_KEY=test-secret-not-real-0123456789',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_PREFIX=xgent/v1',
        'MQTT_TLS=true',
        'MQTT_USERNAME=test-user',
        'MQTT_PASSWORD=test-password-not-real',
        'ENCRYPT_PAYLOAD=true'
    )
    [IO.File]::WriteAllLines((Join-Path $fixture '.env'), [string[]]$secureEnv, $utf8)
    Copy-Item -LiteralPath (Join-Path (Split-Path $installer -Parent) 'install_guardian.ps1') `
        -Destination (Join-Path $fixture 'install_guardian.ps1')

    $global:XiderAclCallCount = 0
    $global:XiderRegisterCount = 0
    $global:XiderStartCount = 0
    $global:XiderTestActions = @{}
    & $installer -AgentDir $fixture -TaskName 'XIDER Fixture' -PreflightOnly
    if ($global:XiderTestActions.Count -ne 0 -or $global:XiderAclCallCount -ne 0 -or
        $global:XiderRegisterCount -ne 0 -or $global:XiderStartCount -ne 0) {
        throw 'Preflight changed ACLs, registered a task, or started a task.'
    }

    & $installer -AgentDir $fixture -TaskName 'XIDER Fixture' -PreferPython
    if ($global:XiderTestActions['XIDER Fixture'].Execute -ne (Join-Path $venv 'pythonw.exe')) {
        throw 'PreferPython selected the stale executable instead of the downloaded Python source.'
    }
    if ($global:XiderRegisterCount -ne 2 -or $global:XiderStartCount -ne 2) {
        throw 'Agent installation did not register and start both visible scheduled tasks.'
    }

    & $installer -AgentDir $fixture -TaskName 'XIDER Fixture'
    if ($global:XiderTestActions['XIDER Fixture'].Execute -ne (Join-Path $fixture 'XGENT-WDS.exe')) {
        throw 'Direct install no longer preserves executable-first behavior.'
    }
    if ($global:XiderRegisterCount -ne 4 -or $global:XiderStartCount -ne 4) {
        throw 'Repeat installation did not register and start both tasks.'
    }

    $registerBeforeSecurityCheck = $global:XiderRegisterCount
    foreach ($case in @(
        @{ Name = 'MQTT_TLS'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_TLS=' }) + 'MQTT_TLS=false' },
        @{ Name = 'MQTT_PASSWORD'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PASSWORD=' }) + 'MQTT_PASSWORD=' }
    )) {
        [IO.File]::WriteAllLines((Join-Path $fixture '.env'), [string[]]$case.Lines, $utf8)
        $rejected = $false
        try { & $installer -AgentDir $fixture -TaskName 'XIDER Fixture' -PreflightOnly }
        catch { $rejected = $_.Exception.Message -match [regex]::Escape($case.Name) }
        if (-not $rejected -or $global:XiderRegisterCount -ne $registerBeforeSecurityCheck) {
            throw "Installer accepted unsafe $($case.Name) or changed a Scheduled Task."
        }
    }
    [IO.File]::WriteAllLines((Join-Path $fixture '.env'), [string[]]$secureEnv, $utf8)

    Remove-Item -LiteralPath (Join-Path $venv 'pythonw.exe') -Force
    $missingPythonRejected = $false
    try { & $installer -AgentDir $fixture -TaskName 'XIDER Fixture' -PreferPython }
    catch { $missingPythonRejected = $true }
    if (-not $missingPythonRejected -or $global:XiderRegisterCount -ne 4 -or $global:XiderStartCount -ne 4) {
        throw 'PreferPython did not reject a missing Python runtime before task registration.'
    }
    Write-Host 'Windows agent preflight and executable selection fixtures passed.'
}
finally {
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
    Remove-Item Function:\icacls,Function:\New-ScheduledTaskAction,Function:\New-ScheduledTaskTrigger,Function:\New-ScheduledTaskSettingsSet,Function:\New-ScheduledTaskPrincipal,Function:\Register-ScheduledTask,Function:\Start-ScheduledTask -ErrorAction SilentlyContinue
    Remove-Variable XiderTestActions,XiderAclCallCount,XiderRegisterCount,XiderStartCount -Scope Global -ErrorAction SilentlyContinue
}
