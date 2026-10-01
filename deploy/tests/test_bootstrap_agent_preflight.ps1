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
        'ENCRYPT_PAYLOAD=true'
    )
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

    New-Item -ItemType Directory -Path $agentSource,$envDir -Force | Out-Null
    foreach ($name in @('xgent_wds.py','xider_guardian_wds.py','install_agent.ps1','install_guardian.ps1','requirements.txt')) {
        [IO.File]::WriteAllText((Join-Path $agentSource $name), 'fixture', $utf8)
    }
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $archive

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
    if ($global:XiderBootstrapSshArgs -notcontains '-i' -or
        $global:XiderBootstrapSshArgs -notcontains $explicitKey -or
        $global:XiderBootstrapSshArgs -notcontains 'IdentitiesOnly=yes') {
        throw 'Windows bootstrap did not pass XIDER_SSH_KEY to OpenSSH.'
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
