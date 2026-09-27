[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$ServerHost = '141.145.152.174',
    [string]$ServerUser = 'ubuntu'
)

$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/invinby/XIDER/archive/refs/heads/main.zip'
$extract = Join-Path $env:TEMP ('xider-agent-' + [guid]::NewGuid().ToString('N'))
$zip = Join-Path $extract 'source.zip'
$unpack = Join-Path $extract 'unpacked'
$repo = Join-Path $InstallRoot 'git-ver'
$agent = Join-Path $repo 'XGENT-WDS'

try {
    Write-Host '[1/5] Скачиваю последнюю версию XIDER...'
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing -TimeoutSec 90
    Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
    $downloaded = Get-ChildItem -LiteralPath $unpack -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }

    Write-Host '[2/5] Ищу локальный .env...'
    $envCandidates = @()
    if ($EnvRoot) { $envCandidates += (Join-Path $EnvRoot 'XGENT-WDS\.env') }
    $envCandidates += @(
        "$env:USERPROFILE\Desktop\XIDER\git-ver\XGENT-WDS\.env",
        "$env:USERPROFILE\Desktop\XIDER\XGENT-WDS\.env",
        (Join-Path $agent '.env')
    )
    $envSource = $envCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

    if (-not $envSource) {
        Write-Host "Локальный .env не найден. Получаю настройки с $ServerUser@$ServerHost; введи пароль SSH, если он будет запрошен."
        $fetched = Join-Path $extract 'agent.env'
        & ssh.exe -o StrictHostKeyChecking=accept-new -o PreferredAuthentications=password -o PubkeyAuthentication=no `
            "$ServerUser@$ServerHost" `
            "sudo -n sh -c 'grep -E \"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=\" /etc/xider/bot.env'" `
            | Out-File -LiteralPath $fetched -Encoding utf8
        if ((Get-Item -LiteralPath $fetched).Length -eq 0) { throw 'Не удалось получить настройки агента.' }
        $envSource = $fetched
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
    $venvCreated = $false
    if (-not (Test-Path -LiteralPath '.\venv\Scripts\python.exe')) {
        Write-Host '[4/5] Создаю виртуальное окружение и ставлю зависимости...'
        $pythonLauncher = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
        if ($pythonLauncher) {
            & $pythonLauncher -3 -m venv venv
        } else {
            & (Get-Command python.exe -ErrorAction Stop).Source -m venv venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать Python venv.' }
        $venvCreated = $true
    } else {
        Write-Host '[4/5] Существующее venv найдено, повторную установку пакетов пропускаю.'
    }
    if ($venvCreated) {
        & .\venv\Scripts\python.exe -m pip install --disable-pip-version-check --no-input -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости Python.' }
    }
    Write-Host '[5/5] Регистрирую Agent и Guardian...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_agent.ps1 -AgentDir (Get-Location).Path
    if ($LASTEXITCODE -ne 0) { throw 'Установка Windows-агента/Guardian завершилась ошибкой.' }
    Pop-Location
    Write-Host '[OK] XIDER Windows Agent + Guardian установлены и запущены.'
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
}
