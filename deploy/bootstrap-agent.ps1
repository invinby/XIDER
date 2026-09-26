[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$ServerHost = '141.145.152.174',
    [string]$ServerUser = 'ubuntu'
)

$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/invinby/XIDER/archive/refs/heads/main.zip'
$zip = Join-Path $env:TEMP 'xider-agent-main.zip'
$extract = Join-Path $env:TEMP ('xider-agent-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $InstallRoot 'git-ver'
$agent = Join-Path $repo 'XGENT-WDS'

try {
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $downloaded = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }

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

    if (Test-Path -LiteralPath $repo) { Remove-Item -LiteralPath $repo -Recurse -Force }
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Move-Item -LiteralPath $downloaded.FullName -Destination $repo
    Copy-Item -LiteralPath $envSource -Destination (Join-Path $agent '.env') -Force

    Push-Location $agent
    if (-not (Test-Path -LiteralPath '.\venv\Scripts\python.exe')) {
        $pythonLauncher = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
        if ($pythonLauncher) {
            & $pythonLauncher -3 -m venv venv
        } else {
            & (Get-Command python.exe -ErrorAction Stop).Source -m venv venv
        }
    }
    & .\venv\Scripts\python.exe -m pip install -q -r requirements.txt
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_agent.ps1 -AgentDir (Get-Location).Path
    if ($LASTEXITCODE -ne 0) { throw 'Установка Windows-агента/Guardian завершилась ошибкой.' }
    Pop-Location
    Write-Host '[OK] XIDER Windows Agent + Guardian установлены и запущены.'
}
finally {
    Pop-Location -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
}
