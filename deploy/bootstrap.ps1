[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\.ssh\xider.key",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$Branch = 'main'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($Branch -notmatch '^[A-Za-z0-9._/-]+$' -or $Branch.Split('/') -contains '..') {
    throw 'Недопустимое имя ветки XIDER_BRANCH.'
}
$repoUrl = "https://github.com/invinby/XIDER/archive/refs/heads/$Branch.zip"
$zip = Join-Path $env:TEMP ('xider-main-' + [guid]::NewGuid().ToString('N') + '.zip')
$extract = Join-Path $env:TEMP ('xider-bootstrap-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $InstallRoot 'git-ver'
$stagedKey = $null
$backup = $null

try {
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing -TimeoutSec 90
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $downloaded = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }

    if (-not $EnvRoot) {
        foreach ($candidateRoot in @((Split-Path $repo -Parent), "$env:USERPROFILE\Desktop\XIDER")) {
            if ((Test-Path -LiteralPath (Join-Path $candidateRoot 'TG-BOT-SERVER\.env')) -and
                (Test-Path -LiteralPath (Join-Path $candidateRoot 'XGENT-WDS\.env'))) {
                $EnvRoot = $candidateRoot
                break
            }
        }
    }
    if (-not $EnvRoot) {
        throw 'Нужен каталог XIDER с TG-BOT-SERVER\.env и XGENT-WDS\.env. Укажи его через XIDER_ENV_ROOT.'
    }
    $envSource = Join-Path (Join-Path $EnvRoot 'TG-BOT-SERVER') '.env'
    $agentEnvSource = Join-Path (Join-Path $EnvRoot 'XGENT-WDS') '.env'
    if (-not (Test-Path -LiteralPath $envSource) -or -not (Test-Path -LiteralPath $agentEnvSource)) {
        throw "Не найдены оба файла конфигурации в $EnvRoot. Установка не изменена."
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    if (Test-Path -LiteralPath $repo) {
        $backup = Join-Path $InstallRoot ('git-ver.previous.' + (Get-Date -Format 'yyyyMMddHHmmss'))
        Move-Item -LiteralPath $repo -Destination $backup
    }
    try {
        Move-Item -LiteralPath $downloaded.FullName -Destination $repo
    } catch {
        if ($backup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $repo)) {
            Move-Item -LiteralPath $backup -Destination $repo
        }
        throw
    }

    $installRoot = Split-Path $repo -Parent
    $installBotDir = Join-Path $installRoot 'TG-BOT-SERVER'
    $installAgentDir = Join-Path $installRoot 'XGENT-WDS'
    New-Item -ItemType Directory -Path $installBotDir -Force | Out-Null
    New-Item -ItemType Directory -Path $installAgentDir -Force | Out-Null
    $installBotEnv = Join-Path $installBotDir '.env'
    $installAgentEnv = Join-Path $installAgentDir '.env'
    if (-not (Test-Path -LiteralPath $installBotEnv)) {
        Copy-Item -LiteralPath $envSource -Destination $installBotEnv
    }
    if (-not (Test-Path -LiteralPath $installAgentEnv)) {
        Copy-Item -LiteralPath $agentEnvSource -Destination $installAgentEnv
    }
    # OpenSSH rejects keys that inherit the CodexSandboxUsers ACL. Stage a
    # temporary copy with read access for the current Windows user only.
    if (Test-Path -LiteralPath $KeyPath) {
        $stagedKey = Join-Path $env:TEMP ('xider-key-' + [guid]::NewGuid().ToString('N') + '.key')
        Copy-Item -LiteralPath $KeyPath -Destination $stagedKey -Force
        icacls.exe $stagedKey /inheritance:r | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить ACL временного SSH-ключа.' }
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $grant = '{0}:(F)' -f $currentUser
        icacls.exe $stagedKey /grant:r $grant | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось выдать доступ к временному SSH-ключу текущему пользователю.' }
    }
    $effectiveKey = $KeyPath
    if ($stagedKey) {
        $effectiveKey = $stagedKey
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'deploy\setup-all.ps1') -ServerIp $ServerIp -KeyPath $effectiveKey
    if ($LASTEXITCODE -ne 0) { throw 'XIDER setup failed.' }
    Write-Host "XIDER готов. Резервная копия старого checkout: $backup"
} finally {
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
    if ($stagedKey) {
        Remove-Item -LiteralPath $stagedKey -Force -ErrorAction SilentlyContinue
    }
}
