$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-bootstrap-transaction-' + [guid]::NewGuid().ToString('N'))
$sourceRoot = Join-Path $fixture 'source'
$sourceRepo = Join-Path $sourceRoot 'XIDER-fixture'
$archive = Join-Path $fixture 'source.zip'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$global:XiderBootstrapSetupResult = 8
$global:XiderBootstrapSetupCalls = 0
$global:XiderBootstrapSetupArgs = @()
$global:XiderBootstrapDownloadSource = $archive
$global:XiderBootstrapObservedUri = $null
$originalXiderSshKey = $env:XIDER_SSH_KEY
$originalXiderRef = $env:XIDER_REF

function powershell.exe {
    $global:XiderBootstrapSetupCalls++
    $global:XiderBootstrapSetupArgs = @($args)
    $global:LASTEXITCODE = $global:XiderBootstrapSetupResult
}

function Invoke-WebRequest {
    param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing, [int]$TimeoutSec)
    $global:XiderBootstrapObservedUri = $Uri
    Copy-Item -LiteralPath $global:XiderBootstrapDownloadSource -Destination $OutFile -Force
}

function New-OldCheckout($installRoot) {
    $oldRepo = Join-Path $installRoot 'git-ver'
    $oldAgent = Join-Path $oldRepo 'XGENT-WDS'
    New-Item -ItemType Directory -Path $oldAgent -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $oldRepo 'old-marker.txt'), 'previous-version', $utf8)
    [IO.File]::WriteAllText((Join-Path $oldAgent '.env'), 'SHARED_KEY=old-local-secret', $utf8)
}

try {
    New-Item -ItemType Directory -Path (Join-Path $sourceRepo 'deploy'),(Join-Path $sourceRepo 'XGENT-WDS') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $sourceRepo 'deploy\setup-all.ps1'), '# fixture', $utf8)
    [IO.File]::WriteAllText((Join-Path $sourceRepo 'XGENT-WDS\install_agent.ps1'), '# fixture', $utf8)
    [IO.File]::WriteAllText((Join-Path $sourceRepo 'new-marker.txt'), 'new-version', $utf8)
    Compress-Archive -Path $sourceRepo -DestinationPath $archive

    $rollbackRoot = Join-Path $fixture 'rollback-install'
    New-OldCheckout $rollbackRoot
    $failedAsExpected = $false
    try {
        & $bootstrap -InstallRoot $rollbackRoot -KeyPath (Join-Path $fixture 'missing.key') -SourceArchive $archive
    } catch { $failedAsExpected = $_.Exception.Message -match 'XIDER setup failed' }
    if (-not $failedAsExpected) { throw 'Simulated setup failure did not fail bootstrap.' }
    $activeRepo = Join-Path $rollbackRoot 'git-ver'
    if (-not (Test-Path -LiteralPath (Join-Path $activeRepo 'old-marker.txt'))) {
        throw 'The previous checkout was not restored.'
    }
    if ((Get-Content -LiteralPath (Join-Path $activeRepo 'XGENT-WDS\.env') -Raw) -ne 'SHARED_KEY=old-local-secret') {
        throw 'The prior agent configuration was not restored exactly.'
    }
    $failedRepos = @(Get-ChildItem -LiteralPath $rollbackRoot -Directory -Filter 'git-ver.failed.*')
    if ($failedRepos.Count -ne 1 -or (Test-Path -LiteralPath (Join-Path $failedRepos[0].FullName 'XGENT-WDS\.env'))) {
        throw 'The failed checkout was not kept without the copied secret.'
    }

    $successRoot = Join-Path $fixture 'success-install'
    New-OldCheckout $successRoot
    $global:XiderBootstrapSetupResult = 0
    $env:XIDER_SSH_KEY = Join-Path $fixture 'selected-key.key'
    & $bootstrap -InstallRoot $successRoot -SourceArchive $archive
    $env:XIDER_SSH_KEY = $originalXiderSshKey
    if ($global:XiderBootstrapSetupArgs -notcontains '-KeyPath' -or
        $global:XiderBootstrapSetupArgs -notcontains (Join-Path $fixture 'selected-key.key')) {
        throw 'The short Windows bootstrap did not forward XIDER_SSH_KEY to setup-all.'
    }
    $activeRepo = Join-Path $successRoot 'git-ver'
    if (-not (Test-Path -LiteralPath (Join-Path $activeRepo 'new-marker.txt'))) {
        throw 'The new checkout was not activated.'
    }
    if ((Get-Content -LiteralPath (Join-Path $successRoot 'XGENT-WDS\.env') -Raw) -ne 'SHARED_KEY=old-local-secret') {
        throw 'The agent configuration was not carried into the new checkout.'
    }
    $backups = @(Get-ChildItem -LiteralPath $successRoot -Directory -Filter 'git-ver.previous.*')
    if ($backups.Count -ne 1 -or -not (Test-Path -LiteralPath (Join-Path $backups[0].FullName 'old-marker.txt'))) {
        throw 'The previous checkout was not retained as a recoverable backup.'
    }

    $pinnedRoot = Join-Path $fixture 'pinned-install'
    New-OldCheckout $pinnedRoot
    $env:XIDER_REF = 'b' * 40
    $global:XiderBootstrapSetupResult = 0
    & $bootstrap -InstallRoot $pinnedRoot
    $expectedPinnedUri = "https://github.com/invinby/XIDER/archive/{0}.zip" -f $env:XIDER_REF
    if ($global:XiderBootstrapObservedUri -ne $expectedPinnedUri) {
        throw 'Windows full bootstrap did not download the archive for the pinned commit.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $pinnedRoot 'git-ver/new-marker.txt'))) {
        throw 'The pinned Windows bootstrap did not activate the downloaded checkout.'
    }
    $env:XIDER_REF = ''
    $invalidRefRoot = Join-Path $fixture 'invalid-ref-install'
    New-OldCheckout $invalidRefRoot
    $invalidRefRejected = $false
    try { & $bootstrap -InstallRoot $invalidRefRoot -Ref main }
    catch { $invalidRefRejected = $_.Exception.Message -match 'XIDER_REF' }
    if (-not $invalidRefRejected -or -not (Test-Path -LiteralPath (Join-Path $invalidRefRoot 'git-ver/old-marker.txt'))) {
        throw 'An invalid pinned reference was accepted or changed the current checkout.'
    }

    $firstInstallRoot = Join-Path $fixture 'first-install'
    $global:XiderBootstrapSetupResult = 8
    $failedFirstInstall = $false
    try {
        & $bootstrap -InstallRoot $firstInstallRoot -KeyPath (Join-Path $fixture 'missing.key') -SourceArchive $archive
    } catch { $failedFirstInstall = $_.Exception.Message -match 'XIDER setup failed' }
    if (-not $failedFirstInstall) { throw 'Simulated first-install failure did not fail bootstrap.' }
    $partial = @(Get-ChildItem -LiteralPath $firstInstallRoot -Directory -Filter 'git-ver.failed.*')
    if ($partial.Count -ne 1 -or (Test-Path -LiteralPath (Join-Path $partial[0].FullName 'XGENT-WDS\.env'))) {
        throw 'A failed first install was not isolated without its copied configuration.'
    }

    Write-Host 'Windows full bootstrap transaction and rollback fixture passed.'
}
finally {
    $env:XIDER_SSH_KEY = $originalXiderSshKey
    $env:XIDER_REF = $originalXiderRef
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-bootstrap-transaction-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
    Remove-Variable XiderBootstrapSetupResult,XiderBootstrapSetupCalls,XiderBootstrapSetupArgs,XiderBootstrapDownloadSource,XiderBootstrapObservedUri -Scope Global -ErrorAction SilentlyContinue
}
