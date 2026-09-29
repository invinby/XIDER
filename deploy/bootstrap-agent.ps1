[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$ServerHost = '141.145.152.174',
    [string]$ServerUser = 'ubuntu',
    [string]$Branch = 'main',
    [switch]$PreflightOnly,
    [string]$SourceArchive
)

$ErrorActionPreference = 'Stop'
if ($Branch -notmatch '^[A-Za-z0-9._/-]+$' -or $Branch.Split('/') -contains '..') {
    throw 'Недопустимое имя ветки XIDER_BRANCH.'
}
$repoUrl = "https://github.com/invinby/XIDER/archive/refs/heads/$Branch.zip"
$ProgressPreference = 'Continue'
$extract = Join-Path $env:TEMP ('xider-agent-' + [guid]::NewGuid().ToString('N'))
$zip = Join-Path $extract 'source.zip'
$unpack = Join-Path $extract 'unpacked'
$repo = Join-Path $InstallRoot 'git-ver'
$agent = Join-Path $repo 'XGENT-WDS'
$locationPushed = $false

try {
    if ($SourceArchive) { Write-Host '[1/5] Проверяю локальный архив XIDER...' }
    else { Write-Host "[1/5] Скачиваю XIDER из ветки $Branch..." }
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    if ($SourceArchive) {
        Copy-Item -LiteralPath $SourceArchive -Destination $zip -ErrorAction Stop
    } else {
        Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing -TimeoutSec 90
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
    $downloaded = Get-ChildItem -LiteralPath $unpack -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }
    foreach ($requiredFile in @(
        'XGENT-WDS\xgent_wds.py',
        'XGENT-WDS\xider_guardian_wds.py',
        'XGENT-WDS\install_agent.ps1',
        'XGENT-WDS\install_guardian.ps1',
        'XGENT-WDS\requirements.txt'
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $downloaded.FullName $requiredFile) -PathType Leaf)) {
            throw "В скачанном архиве нет $requiredFile. Рабочая установка не изменена."
        }
    }

    Write-Host '[2/5] Ищу локальный .env...'
    $envCandidates = @()
    # The active install is authoritative: an old Desktop checkout may use
    # different credentials and must not silently replace its configuration.
    $envCandidates += (Join-Path $agent '.env')
    if ($EnvRoot) { $envCandidates += (Join-Path $EnvRoot 'XGENT-WDS\.env') }
    $envCandidates += @(
        "$env:USERPROFILE\Desktop\XIDER\git-ver\XGENT-WDS\.env",
        "$env:USERPROFILE\Desktop\XIDER\XGENT-WDS\.env"
    )
    $envSource = $envCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    if (-not $envSource) {
        Write-Host "Локальный .env не найден. Получаю настройки с $ServerUser@$ServerHost; введи пароль SSH, если он будет запрошен."
        $fetched = Join-Path $extract 'agent.env'
        $remoteCommand = "sudo -n sh -c 'grep -E `"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=`" /etc/xider/bot.env'"
        & ssh.exe -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 `
            -o ServerAliveInterval=10 -o ServerAliveCountMax=2 `
            -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no `
            "$ServerUser@$ServerHost" $remoteCommand | Set-Content -LiteralPath $fetched -Encoding utf8
        $sshExit = $LASTEXITCODE
        if ($sshExit -ne 0) { throw "SSH не смог получить настройки (код $sshExit). VPS и файлы агента не изменены." }
        $allowed = Get-Content -LiteralPath $fetched | Where-Object {
            $_ -match '^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)='
        }
        if (-not ($allowed | Where-Object { $_ -match '^MQTT_PREFIX=.+$' })) {
            $allowed = @($allowed | Where-Object { $_ -notmatch '^MQTT_PREFIX=' })
            $allowed += 'MQTT_PREFIX=xgent/v1'
        }
        foreach ($required in @('SHARED_KEY', 'MQTT_BROKER', 'MQTT_PORT', 'MQTT_TLS', 'ENCRYPT_PAYLOAD')) {
            if (-not ($allowed | Where-Object { $_ -match "^${required}=.+$" })) {
                throw "На VPS отсутствует обязательный параметр $required. Файлы агента не изменены."
            }
        }
        $allowed | Set-Content -LiteralPath $fetched -Encoding utf8
        $envSource = $fetched
    }

    $requiredSettings = @('SHARED_KEY', 'MQTT_BROKER', 'MQTT_PORT', 'MQTT_TLS', 'ENCRYPT_PAYLOAD')
    $configLines = @(Get-Content -LiteralPath $envSource -ErrorAction Stop)
    foreach ($required in $requiredSettings) {
        $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($required) + '\s*=\s*(.*)$'
        $matchingLines = @($configLines | Where-Object { $_ -match $pattern })
        $value = if ($matchingLines.Count) {
            [regex]::Match($matchingLines[-1], $pattern).Groups[1].Value.Trim().Trim('"', "'").Trim()
        } else { '' }
        if (-not $value -or $value.StartsWith('#')) {
            throw "В конфигурации агента отсутствует $required. Рабочая установка не изменена."
        }
    }
    if ($PreflightOnly) {
        $sourceLabel = if ($SourceArchive) { 'Локальный архив' } else { "Архив ветки $Branch" }
        Write-Host "[OK] $sourceLabel и конфигурация агента проверены. Установка не запускалась."
        return
    }

    $targetEnv = Join-Path $agent '.env'
    if (Test-Path -LiteralPath $targetEnv) {
        # Защищённый ACL файл не копируем во временную папку и не перезаписываем.
        # Это устраняет отказ в доступе при повторной установке.
        Write-Host '[3/5] Существующий .env оставляю без изменений.'
    } else {
        $stagedEnv = Join-Path $extract 'agent-preserved.env'
        Copy-Item -LiteralPath $envSource -Destination $stagedEnv -Force
        $envSource = $stagedEnv
        Write-Host '[3/5] Новый .env подготовлен.'
    }
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $repo -Force | Out-Null
    Copy-Item -Path (Join-Path $downloaded.FullName '*') -Destination $repo -Recurse -Force
    if (-not (Test-Path -LiteralPath $targetEnv)) {
        Copy-Item -LiteralPath $envSource -Destination $targetEnv -Force
    }

    Push-Location $agent
    $locationPushed = $true
    if (-not (Test-Path -LiteralPath '.\venv\Scripts\python.exe')) {
        Write-Host '[4/5] Создаю виртуальное окружение и ставлю зависимости...'
        $pythonLauncher = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
        if ($pythonLauncher) {
            & $pythonLauncher -3 -m venv venv
        } else {
            & (Get-Command python.exe -ErrorAction Stop).Source -m venv venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать Python venv.' }
    } else {
        Write-Host '[4/5] Проверяю и обновляю зависимости в существующем venv...'
    }
    & .\venv\Scripts\python.exe -m pip install --disable-pip-version-check --no-input -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости Python.' }
    Write-Host '[5/5] Регистрирую Agent и Guardian...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_agent.ps1 -AgentDir (Get-Location).Path -PreferPython
    if ($LASTEXITCODE -ne 0) { throw 'Установка Windows-агента/Guardian завершилась ошибкой.' }
    Write-Host '[OK] XIDER Windows Agent + Guardian установлены и запущены.'
}
finally {
    if ($locationPushed) { Pop-Location }
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
}
