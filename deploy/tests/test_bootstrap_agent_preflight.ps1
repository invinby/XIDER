$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-bootstrap-preflight-' + [guid]::NewGuid().ToString('N'))
$sourceRoot = Join-Path $fixture 'source'
$agentSource = Join-Path $sourceRoot 'XIDER-fixture\XGENT-WDS'
$envRoot = Join-Path $fixture 'env'
$envDir = Join-Path $envRoot 'XGENT-WDS'
$installRoot = Join-Path $fixture 'install'
$archive = Join-Path $fixture 'source.zip'
$traversalArchive = Join-Path $fixture 'traversal.zip'
$missingFileArchive = Join-Path $fixture 'missing-file.zip'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$global:XiderBootstrapDownloadSource = $null
$global:XiderBootstrapObservedUri = $null
$global:XiderBootstrapObservedTimeoutSec = $null
$global:XiderBootstrapObservedProgressPreference = $null
$originalUserProfile = $env:USERPROFILE
$originalXiderSshKey = $env:XIDER_SSH_KEY
$originalXiderRef = $env:XIDER_REF

function New-TraversalZip($path, $entryName) {
    Add-Type -AssemblyName System.IO.Compression
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry("../$entryName")
        $writer = [IO.StreamWriter]::new($entry.Open())
        try { $writer.Write('must not escape staging') } finally { $writer.Dispose() }
    } finally {
        $zip.Dispose()
        $stream.Dispose()
    }
}

function Invoke-WebRequest {
    param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing, [int]$TimeoutSec)
    Copy-Item -LiteralPath $global:XiderBootstrapDownloadSource -Destination $OutFile -Force
    $global:XiderBootstrapObservedUri = $Uri
    $global:XiderBootstrapObservedTimeoutSec = $TimeoutSec
    $global:XiderBootstrapObservedProgressPreference = [string]$ProgressPreference
}

function global:ssh.exe {
    $global:XiderBootstrapSshArgs = @($args)
    $global:LASTEXITCODE = 0
    @(
        'SHARED_KEY=fixture-secret-not-real',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_TLS=true',
        'MQTT_USERNAME=fixture-user',
        'MQTT_PASSWORD=fixture-password',
        'ENCRYPT_PAYLOAD=true',
        'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9',
        'SERVER_PRIVATE_SENTINEL=must-not-transfer'
    )
}

function Invoke-XiderElevationGuardRegression {
    param([string]$Bootstrap, [string]$InstallRoot, [string]$SourceArchive)

    $global:XiderElevationTestInstallerCalls = 0
    function Get-ScheduledTask {
        param($TaskName, $ErrorAction)
        return $null
    }
    function powershell.exe {
        $global:XiderElevationTestInstallerCalls++
        $global:LASTEXITCODE = 17
    }

    $failure = ''
    $failureType = ''
    try {
        & $Bootstrap -InstallRoot $InstallRoot -SourceArchive $SourceArchive -HealthTimeoutSeconds 1
    } catch {
        $failure = $_.Exception.Message
        $failureType = $_.Exception.GetType().FullName
    }
    if ($failureType -ne 'System.UnauthorizedAccessException') {
        throw 'A non-elevated full install did not fail with the actionable elevation error.'
    }
    if ((Test-Path -LiteralPath $InstallRoot) -or $global:XiderElevationTestInstallerCalls -ne 0) {
        throw 'A non-elevated full install changed the target or invoked the installer before refusing.'
    }
}

try {
    $bootstrapSource = Get-Content -LiteralPath $bootstrap -Raw
    if ($bootstrapSource -match 'PubkeyAuthentication=no|PreferredAuthentications=password') {
        throw 'Windows bootstrap disables SSH key authentication.'
    }
    if ($bootstrapSource -notmatch '\$env:XIDER_SSH_KEY' -or
        $bootstrapSource -notmatch '\.ssh\\xider' -or
        $bootstrapSource -notmatch 'IdentitiesOnly=yes') {
        throw 'Windows bootstrap does not support the configured/default XIDER SSH key.'
    }

    $tokens = $null; $parseErrors = $null
    $bootstrapAst = [Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw 'Windows bootstrap has a PowerShell syntax error.' }
    $nativeProbeFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Invoke-XiderNativeProbe'
    }, $true)
    if (-not $nativeProbeFunction) { throw 'Native Python probe helper is missing.' }
    Invoke-Expression $nativeProbeFunction.Extent.Text
    $nativeProbe = Invoke-XiderNativeProbe -Executable $env:ComSpec -Arguments @(
        '/d', '/s', '/c', 'echo No suitable Python runtime found. 1>&2 & exit /b 1'
    )
    if ($nativeProbe.ExitCode -eq 0 -or $nativeProbe.Output.Count -ne 0) {
        throw 'A missing Python runtime must be treated as a probe miss, not as a bootstrap-stopping error.'
    }
    $safeVersionProbe = 'import sys; print(sys.version_info[0]*100 + sys.version_info[1])'
    if ([regex]::Matches($bootstrapSource, 'print\(sys\.version_info\[0\]\*100 \+ sys\.version_info\[1\]\)').Count -lt 3) {
        throw 'Python version checks must use the PowerShell 5.1-safe, quote-free probe.'
    }
    $pythonCandidates = @(
        (Join-Path $PSScriptRoot '..\..\.venv312\Scripts\python.exe'),
        (Get-Command python.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -Unique
    if ($pythonCandidates.Count -gt 0) {
        $versionProbe = Invoke-XiderNativeProbe -Executable $pythonCandidates[0] -Arguments @('-c', $safeVersionProbe)
        if ($versionProbe.ExitCode -ne 0 -or $versionProbe.Output.Count -ne 1 -or
            [string]$versionProbe.Output[0] -notmatch '^3(?:11|12|14)
    }
    $elevationFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Assert-XiderBootstrapElevation'
    }, $true)
    if (-not $elevationFunction) { throw 'Bootstrap elevation guard is missing.' }
    Invoke-Expression $elevationFunction.Extent.Text
    $nonAdminDenied = $false
    try { Assert-XiderBootstrapElevation -IsElevated:$false }
    catch { $nonAdminDenied = $_.Exception -is [UnauthorizedAccessException] }
    if (-not $nonAdminDenied) { throw 'The elevation guard did not reject a non-admin install.' }
    Assert-XiderBootstrapElevation -IsElevated:$false -PreflightOnly
    Assert-XiderBootstrapElevation -IsElevated:$true

    $secretCleanupFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Remove-XiderPrivateFile'
    }, $true)
    if (-not $secretCleanupFunction) { throw 'Failed-install secret cleanup helper is missing.' }
    Invoke-Expression $secretCleanupFunction.Extent.Text
    $stageCleanupFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Remove-XiderStageEnvironment'
    }, $true)
    if (-not $stageCleanupFunction) { throw 'Staging cleanup status helper is missing.' }
    Invoke-Expression $stageCleanupFunction.Extent.Text
    New-Item -ItemType Directory -Path $fixture -Force | Out-Null
    $restrictedSecret = Join-Path $fixture 'restricted-env.fixture'
    $preservedSibling = Join-Path $fixture 'preserved-marker.fixture'
    [IO.File]::WriteAllText($restrictedSecret, 'synthetic secret fixture', $utf8)
    [IO.File]::WriteAllText($preservedSibling, 'keep', $utf8)
    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $readOnlyGrant = "*$($ownerSid.Value):(R)"
    & icacls.exe $restrictedSecret /inheritance:r /grant:r $readOnlyGrant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the read-only .env ACL fixture.' }
    Remove-XiderPrivateFile -Path $restrictedSecret
    if ((Test-Path -LiteralPath $restrictedSecret) -or
        -not (Test-Path -LiteralPath $preservedSibling)) {
        throw 'Failed-install cleanup did not remove only its read-only .env copy.'
    }

    $successfulStage = Join-Path $fixture 'stage-cleanup-success'
    $successfulStageAgent = Join-Path $successfulStage 'XGENT-WDS'
    New-Item -ItemType Directory -Path $successfulStageAgent -Force | Out-Null
    $successfulStageEnv = Join-Path $successfulStageAgent '.env'
    $successfulStageMarker = Join-Path $successfulStage 'keep.marker'
    [IO.File]::WriteAllText($successfulStageEnv, 'synthetic stage secret', $utf8)
    [IO.File]::WriteAllText($successfulStageMarker, 'keep', $utf8)
    $successWarnings = @()
    $successCleanup = Remove-XiderStageEnvironment -StagePath $successfulStage -WarningVariable successWarnings
    $successWarningText = ($successWarnings | ForEach-Object { $_.Message }) -join ' '
    $withoutEnvPhrase = ([string][char]0x0431) + [char]0x0435 + [char]0x0437 + ' .env'
    if (-not $successCleanup -or (Test-Path -LiteralPath $successfulStageEnv) -or
        -not (Test-Path -LiteralPath $successfulStageMarker) -or
        $successWarningText -notlike "*$withoutEnvPhrase*") {
        throw 'Successful staging cleanup must remove only .env and report that it is gone.'
    }

    $failedStage = Join-Path $fixture 'stage-cleanup-failure'
    $failedStageAgent = Join-Path $failedStage 'XGENT-WDS'
    New-Item -ItemType Directory -Path $failedStageAgent -Force | Out-Null
    $failedStageEnv = Join-Path $failedStageAgent '.env'
    [IO.File]::WriteAllText($failedStageEnv, 'synthetic stage secret', $utf8)
    $realPrivateFileCleanup = (Get-Item -LiteralPath Function:\Remove-XiderPrivateFile).ScriptBlock
    try {
        Set-Item -LiteralPath Function:\Remove-XiderPrivateFile -Value {
            param([string]$Path)
            throw 'synthetic ACL failure'
        }
        $failureWarnings = @()
        $failureCleanup = Remove-XiderStageEnvironment -StagePath $failedStage -WarningVariable failureWarnings
    } finally {
        Set-Item -LiteralPath Function:\Remove-XiderPrivateFile -Value $realPrivateFileCleanup
    }
    $failureWarningText = ($failureWarnings | ForEach-Object { $_.Message }) -join ' '
    if ($failureCleanup -or -not (Test-Path -LiteralPath $failedStageEnv) -or
        $failureWarningText -like "*$withoutEnvPhrase*" -or
        $failureWarningText -notlike '*stage-cleanup-failure*') {
        throw 'Failed staging cleanup must retain the .env and report that it remains.'
    }

    New-Item -ItemType Directory -Path $agentSource,$envDir -Force | Out-Null
    foreach ($name in @('config.py','xgent_wds.py','xider_guardian_wds.py','install_agent.ps1','install_guardian.ps1','requirements.txt')) {
        $body = if ($name -eq 'config.py') { 'VERSION = "fixture"' } elseif ($name -eq 'requirements.txt') { '' } else { 'fixture' }
        [IO.File]::WriteAllText((Join-Path $agentSource $name), $body, $utf8)
    }
    $dockClient = Join-Path $sourceRoot 'XIDER-fixture\deploy\x_dock_client.py'
    New-Item -ItemType Directory -Path (Split-Path -Parent $dockClient) -Force | Out-Null
    [IO.File]::WriteAllText($dockClient, '# fixture client', $utf8)
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $archive

    $currentPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Invoke-XiderElevationGuardRegression -Bootstrap $bootstrap `
            -InstallRoot (Join-Path $fixture 'elevation-install') -SourceArchive $archive
    }

    $escapeName = 'xider-agent-bootstrap-escape-' + [guid]::NewGuid().ToString('N') + '.txt'
    # This bootstrap extracts beneath a second "unpacked" directory, so two
    # parent segments would escape its entire unique temp directory.
    New-TraversalZip $traversalArchive "../$escapeName"
    $traversalRejected = $false
    try {
        & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $traversalArchive -PreflightOnly
    } catch { $traversalRejected = $true }
    if (-not $traversalRejected -or
        (Test-Path -LiteralPath (Join-Path $env:TEMP $escapeName)) -or
        (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver'))) {
        throw 'Windows agent bootstrap accepted a traversal archive or changed the active installation.'
    }

    $env:USERPROFILE = Join-Path $fixture 'isolated-user'
    $defaultKey = Join-Path (Join-Path $env:USERPROFILE '.ssh') 'xider'
    New-Item -ItemType Directory -Path (Split-Path $defaultKey -Parent) -Force | Out-Null
    [IO.File]::WriteAllText($defaultKey, 'not-a-real-private-key', $utf8)
    $explicitKey = Join-Path $fixture 'separate-explicit-key'
    [IO.File]::WriteAllText($explicitKey, 'not-a-real-private-key', $utf8)
    $env:XIDER_SSH_KEY = $explicitKey
    $global:XiderBootstrapSshArgs = @()
    $remoteEnvRoot = Join-Path $fixture 'remote-env'
    New-Item -ItemType Directory -Path $remoteEnvRoot -Force | Out-Null
    & $bootstrap -InstallRoot (Join-Path $fixture 'remote-install') -EnvRoot $remoteEnvRoot -SourceArchive $archive -PreflightOnly
    if (($global:XiderBootstrapSshArgs -join ' ') -notmatch 'XIDER_COMMAND_VERIFY_KEYSET_B64' -or
        ($global:XiderBootstrapSshArgs -join ' ') -match 'command-signing-private|SERVER_PRIVATE_SENTINEL') {
        throw 'Windows bootstrap must request only the public command keyset.'
    }
    # Exercise the actual pure merge helper without running install side effects.
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
    $mergeFunction = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Merge-XiderCommandPublicKeyset'
    }, $true)
    if (-not $mergeFunction) { throw 'Public-only merge helper is missing.' }
    Invoke-Expression $mergeFunction.Extent.Text
    $localLines = @('SHARED_KEY=local-device-secret', 'MQTT_PASSWORD=local-password',
                    'DEVICE_ID=local-device', 'UNRELATED=keep', 'XIDER_COMMAND_VERIFY_KEYSET_B64=old')
    $remoteLines = @('SHARED_KEY=must-not-overwrite', 'SERVER_PRIVATE_SENTINEL=must-not-transfer',
                     'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9')
    $merged = @(Merge-XiderCommandPublicKeyset -ConfigLines $localLines -PublicLines $remoteLines)
    foreach ($expected in $localLines | Where-Object { $_ -notmatch '^XIDER_COMMAND_VERIFY_KEYSET_B64=' }) {
        if ($merged -notcontains $expected) { throw 'Public refresh overwrote device-specific configuration.' }
    }
    if ($merged -notcontains $remoteLines[2] -or $merged -match 'SERVER_PRIVATE_SENTINEL|must-not-overwrite') {
        throw 'Public merge lost the keyset or transferred server secrets.'
    }
    foreach ($publicCase in @(@(), @('XIDER_COMMAND_VERIFY_KEYSET_B64=invalid=='),
                             @($remoteLines[2], $remoteLines[2]))) {
        $withoutKey = @(Merge-XiderCommandPublicKeyset -ConfigLines $localLines -PublicLines $publicCase)
        if ($withoutKey -match '^XIDER_COMMAND_VERIFY_KEYSET_B64=') { throw 'Unsafe/missing public config enabled shell.' }
        if ($withoutKey -notcontains 'SHARED_KEY=local-device-secret') { throw 'Missing public config broke ordinary install.' }
    }
    foreach ($functionName in @('Read-XiderUtf8EnvText', 'Write-XiderPublicKeysetEnv')) {
        $fileFunction = $ast.Find({ param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName
        }, $true)
        if (-not $fileFunction) { throw 'Strict UTF-8 public refresh helper is missing.' }
        Invoke-Expression $fileFunction.Extent.Text
    }
    $unicodeValue = -join @([char]0x043F, [char]0x0430, [char]0x0440, [char]0x043E, [char]0x043B, [char]0x044C)
    $bomSource = Join-Path $fixture 'public-bom-source.env'
    $bomDestination = Join-Path $fixture 'public-bom-result.env'
    $bomText = "MQTT_PASSWORD=$unicodeValue`r`nUNRELATED=$unicodeValue`nXIDER_COMMAND_VERIFY_KEYSET_B64=old"
    [IO.File]::WriteAllBytes($bomSource, [byte[]](@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes($bomText)))
    Write-XiderPublicKeysetEnv -SourcePath $bomSource -DestinationPath $bomDestination -PublicLines @($remoteLines[2])
    $expectedBomBytes = [byte[]](@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes($bomText.Replace('XIDER_COMMAND_VERIFY_KEYSET_B64=old', $remoteLines[2])))
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($bomDestination)) -cne [Convert]::ToBase64String($expectedBomBytes)) {
        throw 'Public refresh changed a UTF-8 BOM, Unicode text, or original line terminators.'
    }
    $invalidUtf8Source = Join-Path $fixture 'invalid-utf8.env'
    $invalidUtf8Destination = Join-Path $fixture 'invalid-utf8-result.env'
    [IO.File]::WriteAllBytes($invalidUtf8Source, [byte[]]@(0xFF))
    $invalidUtf8Rejected = $false
    try { Write-XiderPublicKeysetEnv -SourcePath $invalidUtf8Source -DestinationPath $invalidUtf8Destination -PublicLines @($remoteLines[2]) }
    catch { $invalidUtf8Rejected = $true }
    if (-not $invalidUtf8Rejected -or (Test-Path -LiteralPath $invalidUtf8Destination)) {
        throw 'Public refresh accepted invalid UTF-8 or emitted a partially decoded file.'
    }
    if ($global:XiderBootstrapSshArgs -notcontains '-i' -or
        $global:XiderBootstrapSshArgs -notcontains $explicitKey -or
        $global:XiderBootstrapSshArgs -notcontains 'IdentitiesOnly=yes') {
        throw 'Windows bootstrap did not pass XIDER_SSH_KEY to OpenSSH.'
    }
    if ($global:XiderBootstrapSshArgs -notcontains 'BatchMode=yes') {
        throw 'Windows bootstrap must not prompt for an SSH password when no local .env exists.'
    }
    if ($global:XiderBootstrapSshArgs -contains 'PubkeyAuthentication=no' -or
        $global:XiderBootstrapSshArgs -contains 'PreferredAuthentications=password,keyboard-interactive') {
        throw 'Windows bootstrap disabled normal SSH public-key authentication.'
    }

    # The conventional key path should also work when the optional override is unset.
    $env:XIDER_SSH_KEY = ''
    $global:XiderBootstrapSshArgs = @()
    & $bootstrap -InstallRoot (Join-Path $fixture 'default-key-install') -EnvRoot $remoteEnvRoot -SourceArchive $archive -PreflightOnly
    if ($global:XiderBootstrapSshArgs -notcontains $defaultKey) {
        throw 'Windows bootstrap did not discover %USERPROFILE%\\.ssh\\xider.'
    }
    $env:USERPROFILE = $originalUserProfile
    $env:XIDER_SSH_KEY = $originalXiderSshKey

    $envPath = Join-Path $envDir '.env'
    $complete = @(
        'SHARED_KEY=test-secret-not-real-0123456789',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_TLS=true',
        'MQTT_USERNAME=test-user',
        'MQTT_PASSWORD=test-password-not-real',
        'ENCRYPT_PAYLOAD=true'
    )
    [IO.File]::WriteAllLines($envPath, $complete, $utf8)

    # Exercise the network branch through a deterministic local archive. Keep
    # PowerShell's byte-level progress UI disabled and retain the bounded HTTP timeout.
    $global:XiderBootstrapDownloadSource = $archive
    $env:XIDER_REF = 'a' * 40
    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -PreflightOnly
    if ($global:XiderBootstrapObservedUri -ne ("https://github.com/invinby/XIDER/archive/{0}.zip" -f $env:XIDER_REF)) {
        throw 'Windows agent bootstrap did not use the pinned commit archive URL.'
    }
    if ($global:XiderBootstrapObservedTimeoutSec -ne 90) {
        throw 'Windows bootstrap did not apply the expected bounded download timeout.'
    }
    if ($global:XiderBootstrapObservedProgressPreference -ne 'SilentlyContinue') {
        throw 'Windows bootstrap enabled per-byte progress output.'
    }
    $invalidRefRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -Ref main -PreflightOnly }
    catch { $invalidRefRejected = $_.Exception.Message -match 'XIDER_REF' }
    if (-not $invalidRefRejected) { throw 'Windows agent bootstrap accepted a mutable branch as XIDER_REF.' }
    $env:XIDER_REF = ''

    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly
    if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
        throw 'Preflight changed the target installation.'
    }

    [IO.File]::WriteAllLines($envPath, @($complete) + 'MQTT_PREFIX=xgent/v1', $utf8)
    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly
    [IO.File]::WriteAllLines($envPath, @($complete) + 'MQTT_PREFIX=', $utf8)
    $blankPrefixRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
    catch { $blankPrefixRejected = $_.Exception.Message -match 'MQTT_PREFIX' }
    if (-not $blankPrefixRejected) { throw 'Preflight accepted an explicitly blank MQTT_PREFIX.' }
    [IO.File]::WriteAllLines($envPath, @($complete) + 'MQTT_PREFIX=xgent/v1' + 'MQTT_PREFIX=duplicate', $utf8)
    $duplicatePrefixRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
    catch { $duplicatePrefixRejected = $_.Exception.Message -match 'MQTT_PREFIX' }
    if (-not $duplicatePrefixRejected) { throw 'Preflight accepted duplicate MQTT_PREFIX values.' }

    [IO.File]::WriteAllLines($envPath, @($complete | Where-Object { $_ -notmatch '^SHARED_KEY=' }), $utf8)
    $missingConfigRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
    catch { $missingConfigRejected = $_.Exception.Message -match 'SHARED_KEY' }
    if (-not $missingConfigRejected) { throw 'Preflight accepted a missing SHARED_KEY.' }

    [IO.File]::WriteAllLines($envPath, $complete, $utf8)
    Remove-Item -LiteralPath (Join-Path $agentSource 'install_guardian.ps1') -Force
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $missingFileArchive
    $missingSourceRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $missingFileArchive -PreflightOnly }
    catch { $missingSourceRejected = $_.Exception.Message -match 'install_guardian.ps1' }
    if (-not $missingSourceRejected) { throw 'Preflight accepted a missing installer file.' }

    $invalidSecurityCases = @(
        @{ Name = 'MQTT_TLS'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_TLS=' }) + 'MQTT_TLS=false' },
        @{ Name = 'ENCRYPT_PAYLOAD'; Lines = @($complete | Where-Object { $_ -notmatch '^ENCRYPT_PAYLOAD=' }) + 'ENCRYPT_PAYLOAD=false' },
        @{ Name = 'MQTT_USERNAME'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_USERNAME=' }) + 'MQTT_USERNAME=' },
        @{ Name = 'MQTT_PASSWORD'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_PASSWORD=' }) + 'MQTT_PASSWORD=' },
        @{ Name = 'MQTT_PORT'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_PORT=' }) + 'MQTT_PORT=70000' },
        @{ Name = 'MQTT_TLS'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_TLS=' }) + 'MQTT_TLS=true' + 'MQTT_TLS=false' }
    )
    foreach ($case in $invalidSecurityCases) {
        [IO.File]::WriteAllLines($envPath, [string[]]$case.Lines, $utf8)
        $rejected = $false
        try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
        catch { $rejected = $_.Exception.Message -match [regex]::Escape($case.Name) }
        if (-not $rejected) { throw "Preflight accepted invalid security setting $($case.Name)." }
        if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
            throw "Invalid $($case.Name) changed the target installation."
        }
    }
    if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
        throw 'Failed preflight changed the target installation.'
    }
    Write-Host 'Windows bootstrap preflight fixture passed.'
}
finally {
    $env:USERPROFILE = $originalUserProfile
    $env:XIDER_SSH_KEY = $originalXiderSshKey
    $env:XIDER_REF = $originalXiderRef
    Remove-Variable XiderBootstrapSshArgs -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable XiderBootstrapDownloadSource,XiderBootstrapObservedUri,XiderBootstrapObservedTimeoutSec,XiderBootstrapObservedProgressPreference -Scope Global -ErrorAction SilentlyContinue
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-bootstrap-preflight-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
) {
            $diagnosticOutput = @(& $pythonCandidates[0] -c $safeVersionProbe 2>&1)
            $diagnosticExit = $LASTEXITCODE
            throw ("The PowerShell 5.1 Python version probe did not return a supported runtime version. Executable=[$($pythonCandidates[0])]; HelperExit=$($versionProbe.ExitCode); HelperOutput=[$(@($versionProbe.Output) -join '|')]; DirectExit=$diagnosticExit; DirectOutput=[$($diagnosticOutput -join '|')]")
        }
    }
    $elevationFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Assert-XiderBootstrapElevation'
    }, $true)
    if (-not $elevationFunction) { throw 'Bootstrap elevation guard is missing.' }
    Invoke-Expression $elevationFunction.Extent.Text
    $nonAdminDenied = $false
    try { Assert-XiderBootstrapElevation -IsElevated:$false }
    catch { $nonAdminDenied = $_.Exception -is [UnauthorizedAccessException] }
    if (-not $nonAdminDenied) { throw 'The elevation guard did not reject a non-admin install.' }
    Assert-XiderBootstrapElevation -IsElevated:$false -PreflightOnly
    Assert-XiderBootstrapElevation -IsElevated:$true

    $secretCleanupFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Remove-XiderPrivateFile'
    }, $true)
    if (-not $secretCleanupFunction) { throw 'Failed-install secret cleanup helper is missing.' }
    Invoke-Expression $secretCleanupFunction.Extent.Text
    $stageCleanupFunction = $bootstrapAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq 'Remove-XiderStageEnvironment'
    }, $true)
    if (-not $stageCleanupFunction) { throw 'Staging cleanup status helper is missing.' }
    Invoke-Expression $stageCleanupFunction.Extent.Text
    New-Item -ItemType Directory -Path $fixture -Force | Out-Null
    $restrictedSecret = Join-Path $fixture 'restricted-env.fixture'
    $preservedSibling = Join-Path $fixture 'preserved-marker.fixture'
    [IO.File]::WriteAllText($restrictedSecret, 'synthetic secret fixture', $utf8)
    [IO.File]::WriteAllText($preservedSibling, 'keep', $utf8)
    $ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $readOnlyGrant = "*$($ownerSid.Value):(R)"
    & icacls.exe $restrictedSecret /inheritance:r /grant:r $readOnlyGrant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the read-only .env ACL fixture.' }
    Remove-XiderPrivateFile -Path $restrictedSecret
    if ((Test-Path -LiteralPath $restrictedSecret) -or
        -not (Test-Path -LiteralPath $preservedSibling)) {
        throw 'Failed-install cleanup did not remove only its read-only .env copy.'
    }

    $successfulStage = Join-Path $fixture 'stage-cleanup-success'
    $successfulStageAgent = Join-Path $successfulStage 'XGENT-WDS'
    New-Item -ItemType Directory -Path $successfulStageAgent -Force | Out-Null
    $successfulStageEnv = Join-Path $successfulStageAgent '.env'
    $successfulStageMarker = Join-Path $successfulStage 'keep.marker'
    [IO.File]::WriteAllText($successfulStageEnv, 'synthetic stage secret', $utf8)
    [IO.File]::WriteAllText($successfulStageMarker, 'keep', $utf8)
    $successWarnings = @()
    $successCleanup = Remove-XiderStageEnvironment -StagePath $successfulStage -WarningVariable successWarnings
    $successWarningText = ($successWarnings | ForEach-Object { $_.Message }) -join ' '
    $withoutEnvPhrase = ([string][char]0x0431) + [char]0x0435 + [char]0x0437 + ' .env'
    if (-not $successCleanup -or (Test-Path -LiteralPath $successfulStageEnv) -or
        -not (Test-Path -LiteralPath $successfulStageMarker) -or
        $successWarningText -notlike "*$withoutEnvPhrase*") {
        throw 'Successful staging cleanup must remove only .env and report that it is gone.'
    }

    $failedStage = Join-Path $fixture 'stage-cleanup-failure'
    $failedStageAgent = Join-Path $failedStage 'XGENT-WDS'
    New-Item -ItemType Directory -Path $failedStageAgent -Force | Out-Null
    $failedStageEnv = Join-Path $failedStageAgent '.env'
    [IO.File]::WriteAllText($failedStageEnv, 'synthetic stage secret', $utf8)
    $realPrivateFileCleanup = (Get-Item -LiteralPath Function:\Remove-XiderPrivateFile).ScriptBlock
    try {
        Set-Item -LiteralPath Function:\Remove-XiderPrivateFile -Value {
            param([string]$Path)
            throw 'synthetic ACL failure'
        }
        $failureWarnings = @()
        $failureCleanup = Remove-XiderStageEnvironment -StagePath $failedStage -WarningVariable failureWarnings
    } finally {
        Set-Item -LiteralPath Function:\Remove-XiderPrivateFile -Value $realPrivateFileCleanup
    }
    $failureWarningText = ($failureWarnings | ForEach-Object { $_.Message }) -join ' '
    if ($failureCleanup -or -not (Test-Path -LiteralPath $failedStageEnv) -or
        $failureWarningText -like "*$withoutEnvPhrase*" -or
        $failureWarningText -notlike '*stage-cleanup-failure*') {
        throw 'Failed staging cleanup must retain the .env and report that it remains.'
    }

    New-Item -ItemType Directory -Path $agentSource,$envDir -Force | Out-Null
    foreach ($name in @('config.py','xgent_wds.py','xider_guardian_wds.py','install_agent.ps1','install_guardian.ps1','requirements.txt')) {
        $body = if ($name -eq 'config.py') { 'VERSION = "fixture"' } elseif ($name -eq 'requirements.txt') { '' } else { 'fixture' }
        [IO.File]::WriteAllText((Join-Path $agentSource $name), $body, $utf8)
    }
    $dockClient = Join-Path $sourceRoot 'XIDER-fixture\deploy\x_dock_client.py'
    New-Item -ItemType Directory -Path (Split-Path -Parent $dockClient) -Force | Out-Null
    [IO.File]::WriteAllText($dockClient, '# fixture client', $utf8)
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $archive

    $currentPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Invoke-XiderElevationGuardRegression -Bootstrap $bootstrap `
            -InstallRoot (Join-Path $fixture 'elevation-install') -SourceArchive $archive
    }

    $escapeName = 'xider-agent-bootstrap-escape-' + [guid]::NewGuid().ToString('N') + '.txt'
    # This bootstrap extracts beneath a second "unpacked" directory, so two
    # parent segments would escape its entire unique temp directory.
    New-TraversalZip $traversalArchive "../$escapeName"
    $traversalRejected = $false
    try {
        & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $traversalArchive -PreflightOnly
    } catch { $traversalRejected = $true }
    if (-not $traversalRejected -or
        (Test-Path -LiteralPath (Join-Path $env:TEMP $escapeName)) -or
        (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver'))) {
        throw 'Windows agent bootstrap accepted a traversal archive or changed the active installation.'
    }

    $env:USERPROFILE = Join-Path $fixture 'isolated-user'
    $defaultKey = Join-Path (Join-Path $env:USERPROFILE '.ssh') 'xider'
    New-Item -ItemType Directory -Path (Split-Path $defaultKey -Parent) -Force | Out-Null
    [IO.File]::WriteAllText($defaultKey, 'not-a-real-private-key', $utf8)
    $explicitKey = Join-Path $fixture 'separate-explicit-key'
    [IO.File]::WriteAllText($explicitKey, 'not-a-real-private-key', $utf8)
    $env:XIDER_SSH_KEY = $explicitKey
    $global:XiderBootstrapSshArgs = @()
    $remoteEnvRoot = Join-Path $fixture 'remote-env'
    New-Item -ItemType Directory -Path $remoteEnvRoot -Force | Out-Null
    & $bootstrap -InstallRoot (Join-Path $fixture 'remote-install') -EnvRoot $remoteEnvRoot -SourceArchive $archive -PreflightOnly
    if (($global:XiderBootstrapSshArgs -join ' ') -notmatch 'XIDER_COMMAND_VERIFY_KEYSET_B64' -or
        ($global:XiderBootstrapSshArgs -join ' ') -match 'command-signing-private|SERVER_PRIVATE_SENTINEL') {
        throw 'Windows bootstrap must request only the public command keyset.'
    }
    # Exercise the actual pure merge helper without running install side effects.
    $tokens = $null; $parseErrors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
    $mergeFunction = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Merge-XiderCommandPublicKeyset'
    }, $true)
    if (-not $mergeFunction) { throw 'Public-only merge helper is missing.' }
    Invoke-Expression $mergeFunction.Extent.Text
    $localLines = @('SHARED_KEY=local-device-secret', 'MQTT_PASSWORD=local-password',
                    'DEVICE_ID=local-device', 'UNRELATED=keep', 'XIDER_COMMAND_VERIFY_KEYSET_B64=old')
    $remoteLines = @('SHARED_KEY=must-not-overwrite', 'SERVER_PRIVATE_SENTINEL=must-not-transfer',
                     'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9')
    $merged = @(Merge-XiderCommandPublicKeyset -ConfigLines $localLines -PublicLines $remoteLines)
    foreach ($expected in $localLines | Where-Object { $_ -notmatch '^XIDER_COMMAND_VERIFY_KEYSET_B64=' }) {
        if ($merged -notcontains $expected) { throw 'Public refresh overwrote device-specific configuration.' }
    }
    if ($merged -notcontains $remoteLines[2] -or $merged -match 'SERVER_PRIVATE_SENTINEL|must-not-overwrite') {
        throw 'Public merge lost the keyset or transferred server secrets.'
    }
    foreach ($publicCase in @(@(), @('XIDER_COMMAND_VERIFY_KEYSET_B64=invalid=='),
                             @($remoteLines[2], $remoteLines[2]))) {
        $withoutKey = @(Merge-XiderCommandPublicKeyset -ConfigLines $localLines -PublicLines $publicCase)
        if ($withoutKey -match '^XIDER_COMMAND_VERIFY_KEYSET_B64=') { throw 'Unsafe/missing public config enabled shell.' }
        if ($withoutKey -notcontains 'SHARED_KEY=local-device-secret') { throw 'Missing public config broke ordinary install.' }
    }
    foreach ($functionName in @('Read-XiderUtf8EnvText', 'Write-XiderPublicKeysetEnv')) {
        $fileFunction = $ast.Find({ param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName
        }, $true)
        if (-not $fileFunction) { throw 'Strict UTF-8 public refresh helper is missing.' }
        Invoke-Expression $fileFunction.Extent.Text
    }
    $unicodeValue = -join @([char]0x043F, [char]0x0430, [char]0x0440, [char]0x043E, [char]0x043B, [char]0x044C)
    $bomSource = Join-Path $fixture 'public-bom-source.env'
    $bomDestination = Join-Path $fixture 'public-bom-result.env'
    $bomText = "MQTT_PASSWORD=$unicodeValue`r`nUNRELATED=$unicodeValue`nXIDER_COMMAND_VERIFY_KEYSET_B64=old"
    [IO.File]::WriteAllBytes($bomSource, [byte[]](@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes($bomText)))
    Write-XiderPublicKeysetEnv -SourcePath $bomSource -DestinationPath $bomDestination -PublicLines @($remoteLines[2])
    $expectedBomBytes = [byte[]](@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes($bomText.Replace('XIDER_COMMAND_VERIFY_KEYSET_B64=old', $remoteLines[2])))
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($bomDestination)) -cne [Convert]::ToBase64String($expectedBomBytes)) {
        throw 'Public refresh changed a UTF-8 BOM, Unicode text, or original line terminators.'
    }
    $invalidUtf8Source = Join-Path $fixture 'invalid-utf8.env'
    $invalidUtf8Destination = Join-Path $fixture 'invalid-utf8-result.env'
    [IO.File]::WriteAllBytes($invalidUtf8Source, [byte[]]@(0xFF))
    $invalidUtf8Rejected = $false
    try { Write-XiderPublicKeysetEnv -SourcePath $invalidUtf8Source -DestinationPath $invalidUtf8Destination -PublicLines @($remoteLines[2]) }
    catch { $invalidUtf8Rejected = $true }
    if (-not $invalidUtf8Rejected -or (Test-Path -LiteralPath $invalidUtf8Destination)) {
        throw 'Public refresh accepted invalid UTF-8 or emitted a partially decoded file.'
    }
    if ($global:XiderBootstrapSshArgs -notcontains '-i' -or
        $global:XiderBootstrapSshArgs -notcontains $explicitKey -or
        $global:XiderBootstrapSshArgs -notcontains 'IdentitiesOnly=yes') {
        throw 'Windows bootstrap did not pass XIDER_SSH_KEY to OpenSSH.'
    }
    if ($global:XiderBootstrapSshArgs -notcontains 'BatchMode=yes') {
        throw 'Windows bootstrap must not prompt for an SSH password when no local .env exists.'
    }
    if ($global:XiderBootstrapSshArgs -contains 'PubkeyAuthentication=no' -or
        $global:XiderBootstrapSshArgs -contains 'PreferredAuthentications=password,keyboard-interactive') {
        throw 'Windows bootstrap disabled normal SSH public-key authentication.'
    }

    # The conventional key path should also work when the optional override is unset.
    $env:XIDER_SSH_KEY = ''
    $global:XiderBootstrapSshArgs = @()
    & $bootstrap -InstallRoot (Join-Path $fixture 'default-key-install') -EnvRoot $remoteEnvRoot -SourceArchive $archive -PreflightOnly
    if ($global:XiderBootstrapSshArgs -notcontains $defaultKey) {
        throw 'Windows bootstrap did not discover %USERPROFILE%\\.ssh\\xider.'
    }
    $env:USERPROFILE = $originalUserProfile
    $env:XIDER_SSH_KEY = $originalXiderSshKey

    $envPath = Join-Path $envDir '.env'
    $complete = @(
        'SHARED_KEY=test-secret-not-real-0123456789',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_TLS=true',
        'MQTT_USERNAME=test-user',
        'MQTT_PASSWORD=test-password-not-real',
        'ENCRYPT_PAYLOAD=true'
    )
    [IO.File]::WriteAllLines($envPath, $complete, $utf8)

    # Exercise the network branch through a deterministic local archive. Keep
    # PowerShell's byte-level progress UI disabled and retain the bounded HTTP timeout.
    $global:XiderBootstrapDownloadSource = $archive
    $env:XIDER_REF = 'a' * 40
    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -PreflightOnly
    if ($global:XiderBootstrapObservedUri -ne ("https://github.com/invinby/XIDER/archive/{0}.zip" -f $env:XIDER_REF)) {
        throw 'Windows agent bootstrap did not use the pinned commit archive URL.'
    }
    if ($global:XiderBootstrapObservedTimeoutSec -ne 90) {
        throw 'Windows bootstrap did not apply the expected bounded download timeout.'
    }
    if ($global:XiderBootstrapObservedProgressPreference -ne 'SilentlyContinue') {
        throw 'Windows bootstrap enabled per-byte progress output.'
    }
    $invalidRefRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -Ref main -PreflightOnly }
    catch { $invalidRefRejected = $_.Exception.Message -match 'XIDER_REF' }
    if (-not $invalidRefRejected) { throw 'Windows agent bootstrap accepted a mutable branch as XIDER_REF.' }
    $env:XIDER_REF = ''

    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly
    if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
        throw 'Preflight changed the target installation.'
    }

    [IO.File]::WriteAllLines($envPath, @($complete) + 'MQTT_PREFIX=xgent/v1', $utf8)
    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly
    [IO.File]::WriteAllLines($envPath, @($complete) + 'MQTT_PREFIX=', $utf8)
    $blankPrefixRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
    catch { $blankPrefixRejected = $_.Exception.Message -match 'MQTT_PREFIX' }
    if (-not $blankPrefixRejected) { throw 'Preflight accepted an explicitly blank MQTT_PREFIX.' }
    [IO.File]::WriteAllLines($envPath, @($complete) + 'MQTT_PREFIX=xgent/v1' + 'MQTT_PREFIX=duplicate', $utf8)
    $duplicatePrefixRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
    catch { $duplicatePrefixRejected = $_.Exception.Message -match 'MQTT_PREFIX' }
    if (-not $duplicatePrefixRejected) { throw 'Preflight accepted duplicate MQTT_PREFIX values.' }

    [IO.File]::WriteAllLines($envPath, @($complete | Where-Object { $_ -notmatch '^SHARED_KEY=' }), $utf8)
    $missingConfigRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
    catch { $missingConfigRejected = $_.Exception.Message -match 'SHARED_KEY' }
    if (-not $missingConfigRejected) { throw 'Preflight accepted a missing SHARED_KEY.' }

    [IO.File]::WriteAllLines($envPath, $complete, $utf8)
    Remove-Item -LiteralPath (Join-Path $agentSource 'install_guardian.ps1') -Force
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $missingFileArchive
    $missingSourceRejected = $false
    try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $missingFileArchive -PreflightOnly }
    catch { $missingSourceRejected = $_.Exception.Message -match 'install_guardian.ps1' }
    if (-not $missingSourceRejected) { throw 'Preflight accepted a missing installer file.' }

    $invalidSecurityCases = @(
        @{ Name = 'MQTT_TLS'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_TLS=' }) + 'MQTT_TLS=false' },
        @{ Name = 'ENCRYPT_PAYLOAD'; Lines = @($complete | Where-Object { $_ -notmatch '^ENCRYPT_PAYLOAD=' }) + 'ENCRYPT_PAYLOAD=false' },
        @{ Name = 'MQTT_USERNAME'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_USERNAME=' }) + 'MQTT_USERNAME=' },
        @{ Name = 'MQTT_PASSWORD'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_PASSWORD=' }) + 'MQTT_PASSWORD=' },
        @{ Name = 'MQTT_PORT'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_PORT=' }) + 'MQTT_PORT=70000' },
        @{ Name = 'MQTT_TLS'; Lines = @($complete | Where-Object { $_ -notmatch '^MQTT_TLS=' }) + 'MQTT_TLS=true' + 'MQTT_TLS=false' }
    )
    foreach ($case in $invalidSecurityCases) {
        [IO.File]::WriteAllLines($envPath, [string[]]$case.Lines, $utf8)
        $rejected = $false
        try { & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly }
        catch { $rejected = $_.Exception.Message -match [regex]::Escape($case.Name) }
        if (-not $rejected) { throw "Preflight accepted invalid security setting $($case.Name)." }
        if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
            throw "Invalid $($case.Name) changed the target installation."
        }
    }
    if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
        throw 'Failed preflight changed the target installation.'
    }
    Write-Host 'Windows bootstrap preflight fixture passed.'
}
finally {
    $env:USERPROFILE = $originalUserProfile
    $env:XIDER_SSH_KEY = $originalXiderSshKey
    $env:XIDER_REF = $originalXiderRef
    Remove-Variable XiderBootstrapSshArgs -Scope Global -ErrorAction SilentlyContinue
    Remove-Variable XiderBootstrapDownloadSource,XiderBootstrapObservedUri,XiderBootstrapObservedTimeoutSec,XiderBootstrapObservedProgressPreference -Scope Global -ErrorAction SilentlyContinue
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-bootstrap-preflight-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
