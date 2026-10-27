param([switch]$UnicodeRefreshOnly)
$ErrorActionPreference = 'Stop'
$currentPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Windows staged install/rollback fixture skipped: run this test elevated to exercise Task Scheduler registration.'
    return
}
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
$global:XiderMockProcesses = @()
$global:XiderMockStoppedPids = @()
$global:XiderMockFilterResults = @()
$global:XiderMockHealthStatus = 'missing'
$global:XiderMockOldTasks = $true
$global:XiderMockOldRunning = @{ 'XIDER Agent' = $true; 'XIDER Guardian' = $true }
$global:XiderMockRestored = @()
$global:XiderMockRestarted = @()

function global:ssh.exe {
    $global:LASTEXITCODE = 0
    'SHARED_KEY=remote-must-not-overwrite-local'
    'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9'
    'SERVER_PRIVATE_SENTINEL=must-not-transfer'
}

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
function Stop-ScheduledTask {
    param($TaskName)
    if ($global:XiderMockTasksRunning) {
        $global:XiderMockTasksRunning = $false
    } else {
        $global:XiderMockOldRunning[$TaskName] = $false
    }
}
function Register-ScheduledTask {
    param($TaskName, $Xml, [switch]$Force)
    $global:XiderMockRestored += $TaskName
}
function Start-ScheduledTask {
    param($TaskName)
    $global:XiderMockRestarted += $TaskName
    $global:XiderMockOldRunning[$TaskName] = $true
}
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    $processes = @($global:XiderMockProcesses)
    $processIdMatch = [regex]::Match([string]$Filter, 'ProcessId\s*=\s*(\d+)')
    if ($processIdMatch.Success) {
        $requestedProcessId = [int]$processIdMatch.Groups[1].Value
        $processes = @($processes | Where-Object { [int]$_.ProcessId -eq $requestedProcessId })
        $returnedProcessIds = @($processes | ForEach-Object { [int]$_.ProcessId }) -join ','
        $global:XiderMockFilterResults += "${requestedProcessId}->${returnedProcessIds}"
    }
    return $processes
}
function Start-Sleep { param([int]$Seconds) }
function Stop-Process {
    param([int]$Id, [switch]$Force, $ErrorAction)
    $global:XiderMockStoppedPids += $Id
    $global:XiderMockProcesses = @($global:XiderMockProcesses | Where-Object { [int]$_.ProcessId -ne $Id })
}
function powershell.exe {
    $global:XiderMockInstallerCalls++
    if ($global:XiderMockInstallerResult -eq 'success') {
        $agentIndex = [Array]::IndexOf($args, '-AgentDir')
        $global:XiderMockAgentDir = $args[$agentIndex + 1]
        $global:XiderMockTasksRunning = $true
        if ($global:XiderMockHealthStatus -ne 'missing') {
            $healthDir = Join-Path $env:USERPROFILE '.xgent'
            New-Item -ItemType Directory -Path $healthDir -Force | Out-Null
            $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000
            if ($global:XiderMockHealthStatus -eq 'stale') { $stamp -= 120 }
            [IO.File]::WriteAllText((Join-Path $healthDir 'agent-health.json'), (@{
                component = 'agent'; connected = $true; updated_at = $stamp; pid = 1111; version = 'fixture'
            } | ConvertTo-Json -Compress), $utf8)
            [IO.File]::WriteAllText((Join-Path $healthDir 'guardian-health.json'), (@{
                component = 'guardian'; connected = $true; updated_at = $stamp; pid = 2222; version = 'fixture'
            } | ConvertTo-Json -Compress), $utf8)
        }
        $global:LASTEXITCODE = 0
    } else {
        $global:LASTEXITCODE = 17
    }
}

function New-OldInstall($installRoot) {
    $agentDir = Join-Path $installRoot 'git-ver\XGENT-WDS'
    $global:XiderMockAgentDir = $agentDir
    $global:XiderMockProcesses = @([pscustomobject]@{
        Name = 'XGENT-WDS.exe'
        ExecutablePath = Join-Path $agentDir 'XGENT-WDS.exe'
        CommandLine = '"' + (Join-Path $agentDir 'XGENT-WDS.exe') + '"'
        ProcessId = 3333
        CreationDate = '20261007000000.000000+000'
    }, [pscustomobject]@{
        Name = 'pythonw.exe'
        ExecutablePath = Join-Path $agentDir 'venv\Scripts\pythonw.exe'
        CommandLine = '"' + (Join-Path $agentDir 'venv\Scripts\pythonw.exe') + '" "' + (Join-Path $agentDir 'xgent_wds.py') + '"'
        ProcessId = 5555
        CreationDate = '20261007000000.000001+000'
    }, [pscustomobject]@{
        Name = 'notepad.exe'
        ExecutablePath = 'C:\Windows\System32\notepad.exe'
        CommandLine = 'notepad.exe "' + (Join-Path $agentDir 'xgent_wds.py') + '"'
        ProcessId = 4444
        CreationDate = '20261007000001.000000+000'
    })
    New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $agentDir 'old-marker.txt'), 'previous-version', $utf8)
    [IO.File]::WriteAllText((Join-Path $agentDir 'xgent_wds.py'), 'fixture', $utf8)
    [IO.File]::WriteAllText((Join-Path $agentDir 'xider_guardian_wds.py'), 'fixture', $utf8)
    [IO.File]::WriteAllLines((Join-Path $agentDir '.env'), @(
        'SHARED_KEY=test-secret-not-real-0123456789',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_PREFIX=xgent/test',
        'MQTT_TLS=true',
        'MQTT_USERNAME=test-user',
        'MQTT_PASSWORD=test-password-not-real',
        'ENCRYPT_PAYLOAD=true',
        'DEVICE_ID=preserve-device',
        'UNRELATED=preserve-setting'
    ), $utf8)
}

function Get-Process {
    param([int]$Id, $ErrorAction)
    if ($Id -in @(1111, 2222)) { return [pscustomobject]@{ Id = $Id } }
    if ($global:XiderMockProcesses | Where-Object { [int]$_.ProcessId -eq $Id }) { return [pscustomobject]@{ Id = $Id } }
    return $null
}

try {
    $originalUserProfile = $env:USERPROFILE
    $env:USERPROFILE = Join-Path $fixture 'profile'
    New-Item -ItemType Directory -Path $agentSource -Force | Out-Null
    foreach ($name in @('config.py','xgent_wds.py','xider_guardian_wds.py','install_agent.ps1','install_guardian.ps1','requirements.txt')) {
        $body = if ($name -eq 'requirements.txt') { '' } elseif ($name -eq 'config.py') { 'VERSION = "fixture"' } else { 'fixture' }
        [IO.File]::WriteAllText((Join-Path $agentSource $name), $body, $utf8)
    }
    $dockClient = Join-Path $sourceRoot 'XIDER-fixture\deploy\x_dock_client.py'
    New-Item -ItemType Directory -Path (Split-Path -Parent $dockClient) -Force | Out-Null
    [IO.File]::WriteAllText($dockClient, '# fixture client', $utf8)
    [IO.File]::WriteAllText((Join-Path $agentSource 'new-marker.txt'), 'new-version', $utf8)
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $archive

    if (-not $UnicodeRefreshOnly) {
    $rollbackRoot = Join-Path $fixture 'rollback-install'
    New-OldInstall $rollbackRoot
    . $bootstrap -InstallRoot $rollbackRoot -SourceArchive $archive -PreflightOnly
    $pythonProcess = $global:XiderMockProcesses | Where-Object { [int]$_.ProcessId -eq 5555 }
    $pythonScriptPath = Join-Path $global:XiderMockAgentDir 'xgent_wds.py'
    $null = Get-XiderPathForms -Path $pythonScriptPath
    $shortScriptBuffer = New-Object System.Text.StringBuilder(32768)
    $shortScriptLength = [XiderNativePath]::GetShortPathName(
        $pythonScriptPath, $shortScriptBuffer, [uint32]$shortScriptBuffer.Capacity
    )
    if ($shortScriptLength -gt 0 -and $shortScriptLength -lt $shortScriptBuffer.Capacity) {
        # WMI can report the same running script through its 8.3 alias.
        $pythonProcess.CommandLine = '"' + $pythonProcess.ExecutablePath + '" "' + $shortScriptBuffer.ToString() + '"'
    }
    $managedBeforeSwap = @(
        Get-XiderManagedProcesses -AgentDirectory (Join-Path $rollbackRoot 'git-ver\XGENT-WDS') |
            ForEach-Object { [int]$_.ProcessId }
    )
    if (5555 -notin $managedBeforeSwap) {
        throw "The process matcher did not recognize its Python fixture. Name=$($pythonProcess.Name); executable=$($pythonProcess.ExecutablePath); command line=$($pythonProcess.CommandLine); managed PIDs=$($managedBeforeSwap -join ',')"
    }
    $failedAsExpected = $false
    $failureMessage = ''
    try { & $bootstrap -InstallRoot $rollbackRoot -SourceArchive $archive -HealthTimeoutSeconds 1 }
    catch {
        $failureMessage = $_.Exception.Message
        $failedAsExpected = -not [string]::IsNullOrWhiteSpace($failureMessage)
    }
    if (-not $failedAsExpected) {
        throw 'Simulated installer failure did not fail the bootstrap.'
    }
    if ($global:XiderMockInstallerCalls -ne 1) {
        throw "The failure did not reach the simulated installer; rollback was not exercised. Bootstrap error: $failureMessage"
    }
    if (3333 -notin $global:XiderMockStoppedPids) {
        throw 'The lingering managed XGENT-WDS.exe process was not stopped before the directory swap.'
    }
    if (5555 -notin $global:XiderMockStoppedPids) {
        $filterResults = $global:XiderMockFilterResults -join '; '
        $stoppedPids = $global:XiderMockStoppedPids -join ','
        throw "The lingering managed Python agent process was not stopped before the directory swap. CIM filter results: $filterResults; stopped PIDs: $stoppedPids"
    }
    if (4444 -in $global:XiderMockStoppedPids) {
        throw 'An unrelated editor process referencing an agent script was stopped.'
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

    foreach ($healthStatus in @('missing', 'stale')) {
        $healthRoot = Join-Path $fixture "${healthStatus}-health-install"
        New-OldInstall $healthRoot
        $global:XiderMockInstallerResult = 'success'
        $global:XiderMockHealthStatus = $healthStatus
        $global:XiderMockOldTasks = $false
        $rejectedAsUnhealthy = $false
        $healthFailureMessage = ''
        try { & $bootstrap -InstallRoot $healthRoot -SourceArchive $archive -HealthTimeoutSeconds 1 }
        catch {
            $healthFailureMessage = $_.Exception.Message
            $rejectedAsUnhealthy = $healthFailureMessage -match 'MQTT|соединен|connection|health|здоров'
        }
        if (-not $rejectedAsUnhealthy -or
            -not (Test-Path -LiteralPath (Join-Path $healthRoot 'git-ver\XGENT-WDS\old-marker.txt'))) {
            $rootEntries = @(Get-ChildItem -LiteralPath $healthRoot -Force -ErrorAction SilentlyContinue |
                ForEach-Object { $_.Name + $(if ($_.PSIsContainer) { '/' } else { '' }) }) -join ', '
            $oldMarkerPath = Join-Path $healthRoot 'git-ver\XGENT-WDS\old-marker.txt'
            throw "Bootstrap rollback assertion failed for $healthStatus health. rejected=$rejectedAsUnhealthy; failure='$healthFailureMessage'; oldMarker=$(Test-Path -LiteralPath $oldMarkerPath); entries=[$rootEntries]"
        }
    }

    }
    $successRoot = Join-Path $fixture 'success-install'
    New-OldInstall $successRoot
    # ASCII source keeps this fixture safe under PS5.1's own script decoding.
    # The .env itself is intentionally BOM-less UTF-8 with Unicode credentials.
    $unicodePassword = -join @([char]0x043F, [char]0x0430, [char]0x0440, [char]0x043E, [char]0x043B, [char]0x044C, [char]0x4E2D)
    $unicodeSetting = -join @([char]0x043D, [char]0x0430, [char]0x0441, [char]0x0442, [char]0x0440, [char]0x043E, [char]0x0439, [char]0x043A, [char]0x0430)
    $successEnvPath = Join-Path $successRoot 'git-ver\XGENT-WDS\.env'
    $successEnvText = [IO.File]::ReadAllText($successEnvPath, $utf8).
        Replace('MQTT_PASSWORD=test-password-not-real', "MQTT_PASSWORD=$unicodePassword").
        Replace('UNRELATED=preserve-setting', "UNRELATED=$unicodeSetting")
    $successEnvText += "XIDER_COMMAND_VERIFY_KEYSET_B64=old`r`nPRESERVE_LF=keep`n"
    [IO.File]::WriteAllText($successEnvPath, $successEnvText, $utf8)
    $originalEnvBytes = [IO.File]::ReadAllBytes($successEnvPath)
    if ($originalEnvBytes[0] -eq 0xEF) { throw 'Unicode fixture unexpectedly contains a BOM.' }
    $publicKeyset = 'eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9'
    $expectedEnvBytes = $utf8.GetBytes($successEnvText.Replace('XIDER_COMMAND_VERIFY_KEYSET_B64=old', "XIDER_COMMAND_VERIFY_KEYSET_B64=$publicKeyset"))
    $global:XiderMockInstallerResult = 'success'
    $global:XiderMockHealthStatus = 'connected'
    $global:XiderMockOldTasks = $false
    & $bootstrap -InstallRoot $successRoot -SourceArchive $archive -HealthTimeoutSeconds 3
    $actualEnvBytes = [IO.File]::ReadAllBytes($successEnvPath)
    if ([Convert]::ToBase64String($actualEnvBytes) -cne [Convert]::ToBase64String($expectedEnvBytes)) {
        throw 'Public refresh corrupted BOM-less UTF-8 values or unrelated line endings.'
    }
    $installedEnv = Get-Content -LiteralPath $successEnvPath -Encoding UTF8
    foreach ($expected in @('SHARED_KEY=test-secret-not-real-0123456789', 'DEVICE_ID=preserve-device',
                            "MQTT_PASSWORD=$unicodePassword", "UNRELATED=$unicodeSetting", "XIDER_COMMAND_VERIFY_KEYSET_B64=$publicKeyset")) {
        if ($installedEnv -notcontains $expected) { throw "Bootstrap lost or failed to deliver $expected." }
    }
    if ($installedEnv -match 'SERVER_PRIVATE_SENTINEL|PRIVATE KEY') { throw 'Private server data reached the agent.' }
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
    if ($originalUserProfile) { $env:USERPROFILE = $originalUserProfile }
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-bootstrap-swap-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
    Remove-Variable XiderMockInstallerResult,XiderMockInstallerCalls,XiderMockTasksRunning,XiderMockAgentDir,XiderMockProcesses,XiderMockStoppedPids,XiderMockFilterResults,XiderMockHealthStatus,XiderMockOldTasks,XiderMockOldRunning,XiderMockRestored,XiderMockRestarted -Scope Global -ErrorAction SilentlyContinue
}
