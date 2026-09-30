[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\.ssh\xider",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$root = (Resolve-Path (Join-Path $repo '..')).Path
$agentDir = Join-Path $repo 'XGENT-WDS'
$fetchedEnv = $null
$stagedKey = $null
$serverUpdated = $false
$serverBackupPath = $null

try {
    # Prefer an explicitly selected XIDER config root, then the historical
    # sibling config directory, then a sidecar already in this checkout.
    $envCandidates = @()
    if ($EnvRoot) { $envCandidates += (Join-Path $EnvRoot 'XGENT-WDS\.env') }
    $envCandidates += @(
        (Join-Path $root 'XGENT-WDS\.env'),
        (Join-Path $agentDir '.env')
    )
    $sourceEnv = $envCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

    if (-not $sourceEnv) {
        if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
            throw "Не найден XGENT-WDS\.env и SSH-ключ $KeyPath. Укажи XIDER_ENV_ROOT или KeyPath; VPS не изменён."
        }
        $fetchedEnv = Join-Path $env:TEMP ('xider-agent-env-' + [guid]::NewGuid().ToString('N') + '.env')
        $stagedKey = Join-Path $env:TEMP ('xider-key-' + [guid]::NewGuid().ToString('N') + '.key')
        Copy-Item -LiteralPath $KeyPath -Destination $stagedKey -Force
        & icacls.exe $stagedKey /inheritance:r | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить ACL временного SSH-ключа.' }
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $grant = '{0}:(F)' -f $currentUser
        & icacls.exe $stagedKey /grant:r $grant | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось выдать доступ к временному SSH-ключу текущему пользователю.' }

        Write-Host "Локального XGENT-WDS\.env нет; безопасно запрашиваю только настройки агента у $ServerIp."
        $remoteCommand = "sudo -n sh -c 'grep -E `"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=`" /etc/xider/bot.env'"
        $sshOutput = @(& ssh.exe -i $stagedKey -o BatchMode=yes -o IdentitiesOnly=yes `
            -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 `
            "ubuntu@$ServerIp" $remoteCommand 2>$null)
        $sshExit = $LASTEXITCODE
        if ($sshExit -ne 0) {
            throw "Не удалось получить конфигурацию агента по SSH (код $sshExit). Проверь ключ/доступ или укажи XIDER_ENV_ROOT; VPS не изменён."
        }
        [IO.File]::WriteAllLines($fetchedEnv, [string[]]$sshOutput, [System.Text.UTF8Encoding]::new($false))
        $sourceEnv = $fetchedEnv
    }

    # Parse values only in memory. Never echo .env contents or include them in
    # exception text, build output, source archives, or remote deploy bundles.
    $settings = @{}
    $mqttPrefixCount = 0
    foreach ($line in [IO.File]::ReadAllLines($sourceEnv)) {
        if ($line -match '^\s*(?:export\s+)?(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?<value>.*)$') {
            $name = $Matches['name']
            $value = $Matches['value'].Trim()
            if ($name -eq 'MQTT_PREFIX') { $mqttPrefixCount++ }
            if ($value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                 ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $settings[$name] = $value
        }
    }
    if ($mqttPrefixCount -gt 1) {
        throw 'В конфигурации MQTT_PREFIX указан несколько раз. VPS не изменён.'
    }

    # Keep the installer aligned with config.py on all three components.
    # Missing means the shared default; an explicitly blank value still fails
    # the required-setting check below instead of silently misrouting MQTT.
    if (-not $settings.ContainsKey('MQTT_PREFIX')) {
        $normalizedLines = @([IO.File]::ReadAllLines($sourceEnv)) + 'MQTT_PREFIX=xgent/v1'
        if (-not $fetchedEnv) {
            $fetchedEnv = Join-Path $env:TEMP ('xider-agent-env-' + [guid]::NewGuid().ToString('N') + '.env')
        }
        [IO.File]::WriteAllLines($fetchedEnv, [string[]]$normalizedLines, [System.Text.UTF8Encoding]::new($false))
        $sourceEnv = $fetchedEnv
        $settings['MQTT_PREFIX'] = 'xgent/v1'
    }

    foreach ($required in @(
        'SHARED_KEY', 'MQTT_BROKER', 'MQTT_PORT', 'MQTT_PREFIX',
        'MQTT_TLS', 'MQTT_USERNAME', 'MQTT_PASSWORD', 'ENCRYPT_PAYLOAD'
    )) {
        if (-not $settings.ContainsKey($required) -or [string]::IsNullOrWhiteSpace([string]$settings[$required])) {
            throw "В конфигурации агента отсутствует обязательный параметр $required. VPS не изменён."
        }
    }
    if ($settings['SHARED_KEY'] -eq 'XGENT-2026-shared-secret') {
        throw 'В конфигурации агента указан публичный тестовый SHARED_KEY. VPS не изменён.'
    }
    if ($settings['MQTT_TLS'].ToLowerInvariant() -notin @('true', '1', 'yes')) {
        throw 'Для установки требуется MQTT_TLS=true. VPS не изменён.'
    }
    if ($settings['ENCRYPT_PAYLOAD'].ToLowerInvariant() -notin @('true', '1', 'yes')) {
        throw 'Для установки требуется ENCRYPT_PAYLOAD=true. VPS не изменён.'
    }
    if ($settings['MQTT_PREFIX'] -notmatch '^[A-Za-z0-9._/-]+$') {
        throw 'MQTT_PREFIX имеет неподдерживаемый формат. VPS не изменён.'
    }
    $mqttPort = 0
    if (-not [int]::TryParse([string]$settings['MQTT_PORT'], [ref]$mqttPort) -or $mqttPort -lt 1 -or $mqttPort -gt 65535) {
        throw 'MQTT_PORT должен быть числом от 1 до 65535. VPS не изменён.'
    }
    if ($PreflightOnly) {
        Write-Host '[OK] Конфигурация агента проверена: TLS, шифрование и обязательные поля есть. Ничего не устанавливалось и не отправлялось на VPS.'
        return
    }
    if (-not $stagedKey -and -not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
        throw "SSH-ключ не найден: $KeyPath. Сборка и VPS не изменены."
    }
    $effectiveKey = if ($stagedKey) { $stagedKey } else { $KeyPath }

    $agentEnv = Join-Path $agentDir '.env'
    New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
    if ([IO.Path]::GetFullPath($sourceEnv) -ine [IO.Path]::GetFullPath($agentEnv)) {
        Copy-Item -LiteralPath $sourceEnv -Destination $agentEnv -Force
    }
    & icacls.exe $agentEnv /inheritance:r | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить ACL конфигурации агента.' }
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $grant = '{0}:(F)' -f $currentUser
    & icacls.exe $agentEnv /grant:r $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось выдать доступ к конфигурации агента текущему пользователю.' }

    Write-Host '=== 1/4: собрать Windows-агент, не трогая VPS ==='
    & cmd.exe /d /c ('"{0}"' -f (Join-Path $agentDir 'build_exe.bat'))
    if ($LASTEXITCODE -ne 0) { throw 'Сборка Windows-агента не прошла; VPS не изменён.' }

    Write-Host '=== 2/4: проверить регистрацию Agent + Guard Keeper без изменений ==='
    & (Join-Path $agentDir 'install_agent.ps1') -AgentDir $agentDir -PreflightOnly

    Write-Host '=== 3/4: обновить XIDER bot на VPS ==='
    $deployOutput = @(& (Join-Path $PSScriptRoot 'upload-and-install.ps1') -ServerIp $ServerIp -KeyPath $effectiveKey)
    $deployExit = $LASTEXITCODE
    $deployOutput | ForEach-Object { Write-Host $_ }
    if ($deployExit -ne 0) { throw 'VPS deployment failed.' }
    $serverUpdated = $true
    $backupLine = $deployOutput | Where-Object { [string]$_ -match '^XIDER_SERVER_BACKUP=' } | Select-Object -First 1
    if ($backupLine -and ([string]$backupLine -match '^XIDER_SERVER_BACKUP=(?<path>/var/backups/xider/xider-[A-Za-z0-9._-]+\.tar\.gz)$')) {
        $serverBackupPath = $Matches['path']
    } else {
        throw 'VPS update succeeded but did not return its rollback snapshot path.'
    }

    Write-Host '=== 4/4: зарегистрировать Windows agent + Guard Keeper ==='
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $agentDir 'install_agent.ps1') -AgentDir $agentDir
    if ($LASTEXITCODE -ne 0) { throw 'Windows agent installation failed.' }

    Write-Host '[OK] Bot VPS and Windows background agent setup finished.'
} catch {
    $setupFailure = $_.Exception.Message
    if ($serverUpdated -and $serverBackupPath) {
        Write-Warning 'Windows Agent/Guardian setup failed after the VPS update; restoring the exact previous server snapshot.'
        try {
            & (Join-Path $PSScriptRoot 'upload-and-install.ps1') -ServerIp $ServerIp `
                -KeyPath $effectiveKey -RollbackOnly -BackupPath $serverBackupPath
            if ($LASTEXITCODE -ne 0) { throw 'Rollback command returned a failure code.' }
            Write-Warning '[OK] VPS source and service were returned to the pre-deploy snapshot.'
        } catch {
            Write-Warning ("Automatic VPS rollback did not complete: {0}" -f $_.Exception.Message)
        }
    }
    throw $setupFailure
} finally {
    if ($fetchedEnv -and (Test-Path -LiteralPath $fetchedEnv)) {
        Remove-Item -LiteralPath $fetchedEnv -Force -ErrorAction SilentlyContinue
    }
    if ($stagedKey -and (Test-Path -LiteralPath $stagedKey)) {
        Remove-Item -LiteralPath $stagedKey -Force -ErrorAction SilentlyContinue
    }
}
