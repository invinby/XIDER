[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\.ssh\xider",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$Branch = 'main',
    [string]$SourceArchive
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
$failed = $null
$activated = $false
$setupSucceeded = $false

try {
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    if ($SourceArchive) {
        Write-Host '[1/5] Проверяю локальный архив XIDER...'
        Copy-Item -LiteralPath $SourceArchive -Destination $zip -ErrorAction Stop
    } else {
        Write-Host "[1/5] Скачиваю архив XIDER из ветки $Branch..."
        Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing -TimeoutSec 90
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $downloaded = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }
    foreach ($requiredFile in @('deploy\setup-all.ps1', 'XGENT-WDS\install_agent.ps1')) {
        if (-not (Test-Path -LiteralPath (Join-Path $downloaded.FullName $requiredFile) -PathType Leaf)) {
            throw "В архиве нет $requiredFile; существующая установка не изменена."
        }
    }

    Write-Host '[2/5] Ищу и сохраняю текущую конфигурацию агента...'
    $envCandidates = @()
    if ($EnvRoot) { $envCandidates += (Join-Path (Join-Path $EnvRoot 'XGENT-WDS') '.env') }
    $envCandidates += @(
        (Join-Path (Split-Path $repo -Parent) 'XGENT-WDS\.env'),
        (Join-Path $repo 'XGENT-WDS\.env'),
        "$env:USERPROFILE\Desktop\XIDER\git-ver\XGENT-WDS\.env",
        "$env:USERPROFILE\Desktop\XIDER\XGENT-WDS\.env"
    )
    $agentEnvSource = $envCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

    # A configuration discovered inside the active checkout would move with
    # that directory below. Stage it first so the update cannot orphan or lose
    # the source path during activation.
    if ($agentEnvSource -and (Test-Path -LiteralPath $agentEnvSource -PathType Leaf)) {
        $preservedEnv = Join-Path $extract 'agent.env'
        Copy-Item -LiteralPath $agentEnvSource -Destination $preservedEnv -Force
        $agentEnvSource = $preservedEnv
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    if (Test-Path -LiteralPath $repo) {
        Write-Host '[3/5] Переключаю checkout; предыдущая версия останется резервной копией...'
        $backup = Join-Path $InstallRoot ('git-ver.previous.' + (Get-Date -Format 'yyyyMMddHHmmss'))
        Move-Item -LiteralPath $repo -Destination $backup
    } else {
        Write-Host '[3/5] Подготавливаю первый checkout...'
    }
    try {
        Move-Item -LiteralPath $downloaded.FullName -Destination $repo
        $activated = $true
    } catch {
        if ($backup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $repo)) {
            Move-Item -LiteralPath $backup -Destination $repo
        }
        throw
    }

    $installRoot = Split-Path $repo -Parent
    $installAgentDir = Join-Path $installRoot 'XGENT-WDS'
    New-Item -ItemType Directory -Path $installAgentDir -Force | Out-Null
    $installAgentEnv = Join-Path $installAgentDir '.env'
    if ($agentEnvSource -and (Test-Path -LiteralPath $agentEnvSource) -and
        -not (Test-Path -LiteralPath $installAgentEnv)) {
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
    Write-Host '[4/5] Запускаю общую проверку и установку XIDER...'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'deploy\setup-all.ps1') -ServerIp $ServerIp -KeyPath $effectiveKey
    if ($LASTEXITCODE -ne 0) { throw 'XIDER setup failed.' }
    Write-Host '[5/5] Установка завершилась без ошибки.'
    $setupSucceeded = $true
    Write-Host "XIDER готов. Резервная копия старого checkout: $backup"
} finally {
    if ($activated -and -not $setupSucceeded -and $backup) {
        if (Test-Path -LiteralPath $repo) {
            try {
                $failed = Join-Path $InstallRoot ('git-ver.failed.' + (Get-Date -Format 'yyyyMMddHHmmss'))
                Move-Item -LiteralPath $repo -Destination $failed
                $failedEnv = Join-Path $failed 'XGENT-WDS\.env'
                if (Test-Path -LiteralPath $failedEnv) {
                    try { Remove-Item -LiteralPath $failedEnv -Force }
                    catch { Write-Warning ("Не удалось удалить копию .env из неудачной версии: {0}" -f $_.Exception.Message) }
                }
            } catch {
                Write-Warning ("Не удалось изолировать неудачную версию: {0}" -f $_.Exception.Message)
            }
        }
        if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $repo)) {
            try {
                Move-Item -LiteralPath $backup -Destination $repo
                Write-Warning 'Bootstrap не завершился; прежний checkout восстановлен.'
            } catch {
                Write-Warning ("Не удалось восстановить прежний checkout: {0}" -f $_.Exception.Message)
            }
        }
    } elseif ($activated -and -not $setupSucceeded -and (Test-Path -LiteralPath $repo)) {
        $failed = Join-Path $InstallRoot ('git-ver.failed.' + (Get-Date -Format 'yyyyMMddHHmmss'))
        try {
            Move-Item -LiteralPath $repo -Destination $failed
            $failedEnv = Join-Path $failed 'XGENT-WDS\.env'
            if (Test-Path -LiteralPath $failedEnv) { Remove-Item -LiteralPath $failedEnv -Force }
            Write-Warning "Первая установка не завершилась; неполная версия оставлена без .env в $failed"
        } catch {
            Write-Warning ("Не удалось изолировать неполную первую установку: {0}" -f $_.Exception.Message)
        }
    }
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
    if ($stagedKey) {
        Remove-Item -LiteralPath $stagedKey -Force -ErrorAction SilentlyContinue
    }
}
