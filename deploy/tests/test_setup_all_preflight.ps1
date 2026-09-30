$ErrorActionPreference = 'Stop'
$scriptSource = (Resolve-Path (Join-Path $PSScriptRoot '..\setup-all.ps1')).Path
$fixture = Join-Path $env:TEMP ('xider-setup-preflight-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $fixture 'repo'
$deploy = Join-Path $repo 'deploy'
$agentDir = Join-Path $repo 'XGENT-WDS'
$envDir = Join-Path $fixture 'XGENT-WDS'
$envPath = Join-Path $envDir '.env'
$marker = Join-Path $fixture 'deploy-called.marker'
$utf8 = New-Object System.Text.UTF8Encoding($false)

try {
    New-Item -ItemType Directory -Path $deploy,$agentDir,$envDir -Force | Out-Null
    Copy-Item -LiteralPath $scriptSource -Destination (Join-Path $deploy 'setup-all.ps1')
    $safeMarker = $marker.Replace("'", "''")
    [IO.File]::WriteAllText(
        (Join-Path $deploy 'upload-and-install.ps1'),
        "[IO.File]::WriteAllText('$safeMarker', 'called')",
        $utf8
    )
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
    [IO.File]::WriteAllLines($envPath, $secureEnv, $utf8)

    $output = @(& (Join-Path $deploy 'setup-all.ps1') -PreflightOnly *>&1)
    $outputText = ($output | Out-String)
    if ($outputText -notmatch 'конфигурация агента проверена') {
        throw 'Secure configuration did not pass preflight.'
    }
    if ($outputText.Contains('test-secret-not-real') -or $outputText.Contains('test-password-not-real')) {
        throw 'Preflight leaked a configuration secret.'
    }
    if (Test-Path -LiteralPath $marker) { throw 'Preflight called the VPS uploader.' }
    if (Test-Path -LiteralPath (Join-Path $agentDir '.env')) { throw 'Preflight copied configuration into the checkout.' }

    $withoutPrefix = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PREFIX=' })
    [IO.File]::WriteAllLines($envPath, [string[]]$withoutPrefix, $utf8)
    $defaultedOutput = @(& (Join-Path $deploy 'setup-all.ps1') -PreflightOnly *>&1)
    if (($defaultedOutput | Out-String) -notmatch 'конфигурация агента проверена') {
        throw 'A missing MQTT_PREFIX did not use the shared xgent/v1 default.'
    }
    if (Test-Path -LiteralPath $marker) { throw 'Defaulted preflight called the VPS uploader.' }
    [IO.File]::WriteAllLines($envPath, $secureEnv, $utf8)

    $badCases = @(
        @{ Name = 'MQTT_TLS'; Expected = 'MQTT_TLS=true'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_TLS=' }) + 'MQTT_TLS=false' },
        @{ Name = 'ENCRYPT_PAYLOAD'; Expected = 'ENCRYPT_PAYLOAD=true'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^ENCRYPT_PAYLOAD=' }) + 'ENCRYPT_PAYLOAD=false' },
        @{ Name = 'MQTT_PASSWORD'; Expected = 'MQTT_PASSWORD'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PASSWORD=' }) + 'MQTT_PASSWORD=' },
        @{ Name = 'MQTT_PREFIX'; Expected = 'MQTT_PREFIX'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PREFIX=' }) + 'MQTT_PREFIX=' },
        @{ Name = 'MQTT_PREFIX'; Expected = 'MQTT_PREFIX'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PREFIX=' }) + 'MQTT_PREFIX=xgent/v1' + 'MQTT_PREFIX=xgent/v2' },
        @{ Name = 'MQTT_PREFIX'; Expected = 'MQTT_PREFIX'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PREFIX=' }) + 'MQTT_PREFIX=invalid prefix' },
        @{ Name = 'MQTT_PORT'; Expected = 'MQTT_PORT'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^MQTT_PORT=' }) + 'MQTT_PORT=70000' },
        @{ Name = 'SHARED_KEY'; Expected = 'SHARED_KEY'; Lines = @($secureEnv | Where-Object { $_ -notmatch '^SHARED_KEY=' }) + 'SHARED_KEY=XGENT-2026-shared-secret' }
    )
    foreach ($case in $badCases) {
        [IO.File]::WriteAllLines($envPath, [string[]]$case.Lines, $utf8)
        $rejected = $false
        try { & (Join-Path $deploy 'setup-all.ps1') -PreflightOnly | Out-Null }
        catch { $rejected = $_.Exception.Message -match [regex]::Escape($case.Expected) }
        if (-not $rejected) { throw "Preflight accepted invalid $($case.Name) configuration." }
        if (Test-Path -LiteralPath $marker) { throw "Invalid $($case.Name) reached the VPS uploader." }
        if (Test-Path -LiteralPath (Join-Path $agentDir '.env')) { throw "Invalid $($case.Name) changed the local installation." }
    }
    Write-Host 'Windows full-setup preflight fixture passed.'
}
finally {
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-setup-preflight-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
