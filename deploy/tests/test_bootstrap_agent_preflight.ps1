$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-bootstrap-preflight-' + [guid]::NewGuid().ToString('N'))
$sourceRoot = Join-Path $fixture 'source'
$agentSource = Join-Path $sourceRoot 'XIDER-fixture\XGENT-WDS'
$envRoot = Join-Path $fixture 'env'
$envDir = Join-Path $envRoot 'XGENT-WDS'
$installRoot = Join-Path $fixture 'install'
$archive = Join-Path $fixture 'source.zip'
$missingFileArchive = Join-Path $fixture 'missing-file.zip'
$utf8 = New-Object System.Text.UTF8Encoding($false)

try {
    New-Item -ItemType Directory -Path $agentSource,$envDir -Force | Out-Null
    foreach ($name in @('xgent_wds.py','xider_guardian_wds.py','install_agent.ps1','install_guardian.ps1','requirements.txt')) {
        [IO.File]::WriteAllText((Join-Path $agentSource $name), 'fixture', $utf8)
    }
    Compress-Archive -Path (Join-Path $sourceRoot 'XIDER-fixture') -DestinationPath $archive

    $envPath = Join-Path $envDir '.env'
    $complete = @(
        'SHARED_KEY=test-secret-not-real-0123456789',
        'MQTT_BROKER=example.invalid',
        'MQTT_PORT=8883',
        'MQTT_TLS=true',
        'ENCRYPT_PAYLOAD=true'
    )
    [IO.File]::WriteAllLines($envPath, $complete, $utf8)
    & $bootstrap -InstallRoot $installRoot -EnvRoot $envRoot -SourceArchive $archive -PreflightOnly
    if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
        throw 'Preflight changed the target installation.'
    }

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
    if (Test-Path -LiteralPath (Join-Path $installRoot 'git-ver')) {
        throw 'Failed preflight changed the target installation.'
    }
    Write-Host 'Windows bootstrap preflight fixture passed.'
}
finally {
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-bootstrap-preflight-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
