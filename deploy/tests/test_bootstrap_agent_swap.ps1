$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-bootstrap-swap-' + [guid]::NewGuid().ToString('N'))
$sourceRoot = Join-Path $fixture 'source'
$agentSource = Join-Path $sourceRoot 'XIDER-fixture\XGENT-WDS'
$archive = Join-Path $fixture 'source.zip'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$global:XiderMockInstallerResult = 'fail'
$global:XiderMockInstallerCalls = 0
$global:XiderMockTasksRunning = $false
$global:XiderMockAgentDir = $null
$global:XiderMockOldTasks = $true
$global:XiderMockOldRunning = @{ 'XIDER Agent' = $true; 'XIDER Guardian' = $true }
$global:XiderMockRestored = @()
$global:XiderMockRestarted = @()

function Get-ScheduledTask {
    param($TaskName, $ErrorAction)
    if ($global:XiderMockTasksRunning) {
        $binary = if ($TaskName -eq 'XIDER Agent') { 'pythonw.exe' } else { 'python.exe' }
        $execute = Join-Path $global:XiderMockAgentDir ("venv\Scripts\$binary")
        return [pscustomobject]@{
            TaskName = $TaskName
            State = 'Running'
            Actions = [pscustomobject]@{ Execute = $execute }
        }
    }
    if ($global:XiderMockOldTasks) {
        $state = if ($global:XiderMockOldRunning[$TaskName]) { 'Running' } else { 'Ready' }
        return [pscustomobject]@{ TaskName = $TaskName; State = $state }
    }
    return $null
}
function Export-ScheduledTask { param($TaskName) return "<Task>$TaskName</Task>" }
function Stop-ScheduledTask { param($TaskName) $global:XiderMockOldRunning[$TaskName] = $false }
function Register-ScheduledTask {
    param($TaskName, $Xml, [switch]$Force)
    $global:XiderMockRestored += $TaskName
}
function Start-ScheduledTask {
    param($TaskName)
    $global:XiderMockRestarted += $TaskName
    $global:XiderMockOldRunning[$TaskName] = $true
}
function powershell.exe {
    $global:XiderMockInstallerCalls++
    if ($global:XiderMockInstallerResult -eq 'success') {
        $agentIndex = [Array]::IndexOf($args, '-AgentDir')
        $global:XiderMockAgentDir = $args[$agentIndex + 1]
        $global:XiderMockTasksRunning = $true
        $global:LASTEXITCODE = 0
    } else {
        $global:LASTEXITCODE = 17
    }
}

function New-OldInstall($installRoot) {
    $agentDir = Join-Path $installRoot 'git-ver\XGENT-WDS'
    New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $agentDir 'old-marker.txt'), 'previous-version', $utf8)
    [IO.File]::WriteAllLines((Join-Path $agentDir '.env'), @(
        'SHARED_KEY=test-secret-not-real-0123456789',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_PREFIX=xgent/test',
        'MQTT_TLS=true',
        'MQTT_USERNAME=test-user',
        'MQTT_PASSWORD=test-password-not-real',
        'ENCRYPT_PAYLOAD=true'
    ), $utf8)
}

try {
    New-Item -ItemType Directory -Path $agentSource -Force | Out-Null
    foreach ($name in @('xgent_wds.py','xider_guardian_wds.py','install_agent.ps1','install_guardian.ps1','requirements.txt')) {
        $body = if ($name -eq 'requirements.txt') { '' } else { 'fixture' }
        [IO.File]::WriteAllText((Join-Path $agentSource $name), $body, $utf8)
    }
    [IO.File]::WriteAllText((Join-Path $agentSource 'new-marker.txt'), 'new-version', $utf8)
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $archive

    $rollbackRoot = Join-Path $fixture 'rollback-install'
    New-OldInstall $rollbackRoot
    $failedAsExpected = $false
    try { & $bootstrap -InstallRoot $rollbackRoot -SourceArchive $archive }
    catch { $failedAsExpected = $_.Exception.Message -match 'Windows-' }
    if (-not $failedAsExpected) { throw 'Simulated installer failure did not fail the bootstrap.' }
    if ($global:XiderMockInstallerCalls -ne 1) {
        throw 'The failure did not reach the simulated installer; rollback was not exercised.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $rollbackRoot 'git-ver\XGENT-WDS\old-marker.txt'))) {
        throw 'The previous checkout was not restored after installer failure.'
    }
    $failedDirs = @(Get-ChildItem -LiteralPath $rollbackRoot -Directory -Filter 'git-ver.failed.*')
    if ($failedDirs.Count -ne 1) {
        throw "Expected one quarantined failed checkout, found $($failedDirs.Count)."
    }
    $failedEnv = Join-Path $failedDirs[0].FullName 'XGENT-WDS\.env'
    if (Test-Path -LiteralPath $failedEnv) {
        throw "Failed checkout retained its copied secret: $failedEnv"
    }
    if ($global:XiderMockRestored.Count -ne 2 -or $global:XiderMockRestarted.Count -ne 2) {
        throw 'Previous Scheduled Tasks or their running state were not restored.'
    }

    $successRoot = Join-Path $fixture 'success-install'
    New-OldInstall $successRoot
    $global:XiderMockInstallerResult = 'success'
    $global:XiderMockOldTasks = $false
    & $bootstrap -InstallRoot $successRoot -SourceArchive $archive
    if (-not (Test-Path -LiteralPath (Join-Path $successRoot 'git-ver\XGENT-WDS\new-marker.txt'))) {
        throw 'The new checkout was not activated.'
    }
    $backups = @(Get-ChildItem -LiteralPath $successRoot -Directory -Filter 'git-ver.previous.*')
    if ($backups.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $backups[0].FullName 'XGENT-WDS\old-marker.txt'))) {
        throw 'The previous checkout was not kept as a recoverable backup.'
    }
    Write-Host 'Windows staged install and rollback fixture passed.'
}
finally {
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-bootstrap-swap-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
    Remove-Variable XiderMockInstallerResult,XiderMockInstallerCalls,XiderMockTasksRunning,XiderMockAgentDir,XiderMockOldTasks,XiderMockOldRunning,XiderMockRestored,XiderMockRestarted -Scope Global -ErrorAction SilentlyContinue
}
