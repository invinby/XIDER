[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath,
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$Branch = 'main',
    [string]$Ref = $env:XIDER_REF,
    [string]$SourceArchive
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (-not $KeyPath) {
    $KeyPath = if ($env:XIDER_SSH_KEY) { $env:XIDER_SSH_KEY } else { Join-Path $env:USERPROFILE '.ssh\xider' }
}
if ($Branch -notmatch '^[A-Za-z0-9._/-]+$' -or $Branch.Split('/') -contains '..') {
    throw 'Недопустимое имя ветки XIDER_BRANCH.'
}
if ($Ref -and $Ref -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
    throw 'XIDER_REF должен быть полным 40- или 64-символьным commit ID.'
}
$repoUrl = if ($Ref) {
    "https://github.com/invinby/XIDER/archive/$Ref.zip"
} else {
    "https://github.com/invinby/XIDER/archive/refs/heads/$Branch.zip"
}
$zip = Join-Path $env:TEMP ('xider-main-' + [guid]::NewGuid().ToString('N') + '.zip')
$extract = Join-Path $env:TEMP ('xider-bootstrap-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $InstallRoot 'git-ver'
$stagedKey = $null
$backup = $null
$failed = $null
$activated = $false
$setupSucceeded = $false

function Expand-XiderArchiveSafely {
    param(
        [Parameter(Mandatory)][string]$ArchivePath,
        [Parameter(Mandatory)][string]$DestinationPath,
        [long]$MaxUncompressedBytes = 1073741824,
        [int]$MaxEntries = 20000
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destination = [IO.Path]::GetFullPath($DestinationPath).TrimEnd('\')
    $destinationPrefix = $destination + '\'
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        if ($archive.Entries.Count -gt $MaxEntries) {
            throw "Архив содержит больше $MaxEntries элементов."
        }

        $seen = @{}
        $files = @{}
        $implicitDirectories = @{}
        [long]$totalUncompressedBytes = 0
        foreach ($entry in $archive.Entries) {
            # Some Windows ZIP writers serialize path separators as backslashes.
            # Normalize before traversal checks so both forms share one path model.
            $name = ([string]$entry.FullName).Replace('\', '/')
            if ([string]::IsNullOrWhiteSpace($name) -or
                $name.StartsWith('/') -or $name -match '^[A-Za-z]:' -or
                $name -match '(^|/)\.\.(/|$)') {
                throw "Небезопасный путь в ZIP-архиве: $name"
            }

            $relativeName = $name.TrimEnd('/')
            if (-not $relativeName) { throw 'В ZIP-архиве найден пустой путь.' }
            foreach ($part in $relativeName.Split('/')) {
                if (-not $part -or $part -eq '.' -or $part -eq '..' -or
                    $part.EndsWith('.') -or $part.EndsWith(' ') -or
                    $part -match '[:<>"|?*]' -or
                    $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
                    throw "Небезопасный компонент пути в ZIP-архиве: $name"
                }
            }

            $unixType = ($entry.ExternalAttributes -shr 16) -band 0xF000
            if ($unixType -eq 0xA000) { throw "Символическая ссылка в ZIP-архиве запрещена: $name" }
            if ($unixType -ne 0 -and $unixType -ne 0x4000 -and $unixType -ne 0x8000) {
                throw "Специальный файл в ZIP-архиве запрещён: $name"
            }

            $target = [IO.Path]::GetFullPath([IO.Path]::Combine(
                $destination,
                $relativeName.Replace('/', [IO.Path]::DirectorySeparatorChar)
            ))
            if (-not $target.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Путь ZIP-архива выходит за staging-каталог: $name"
            }
            if ($seen.ContainsKey($target)) { throw "Повторяющийся путь в ZIP-архиве: $name" }

            $parent = [IO.Path]::GetDirectoryName($target)
            while ($parent -and $parent.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                if ($files.ContainsKey($parent)) { throw "Файл ZIP-архива используется как каталог: $name" }
                $implicitDirectories[$parent] = $true
                $nextParent = [IO.Path]::GetDirectoryName($parent)
                if ($nextParent -eq $parent) { break }
                $parent = $nextParent
            }

            $isDirectory = $name.EndsWith('/') -or $unixType -eq 0x4000
            if (-not $isDirectory -and $implicitDirectories.ContainsKey($target)) {
                throw "Файл ZIP-архива конфликтует с вложенными путями: $name"
            }
            if (-not $isDirectory) { $files[$target] = $true }
            $seen[$target] = $true

            $totalUncompressedBytes += [long]$entry.Length
            if ($totalUncompressedBytes -gt $MaxUncompressedBytes) {
                throw "Архив распаковывается больше чем в $MaxUncompressedBytes байт."
            }
        }
    }
    finally {
        $archive.Dispose()
    }

    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $DestinationPath -Force
}

try {
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    if ($SourceArchive) {
        Write-Host '[1/5] Проверяю локальный архив XIDER...'
        Copy-Item -LiteralPath $SourceArchive -Destination $zip -ErrorAction Stop
    } else {
        if ($Ref) { Write-Host "[1/5] Скачиваю архив XIDER из закреплённого commit $Ref..." }
        else { Write-Host "[1/5] Скачиваю архив XIDER из ветки $Branch..." }
        Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing -TimeoutSec 90
    }
    Expand-XiderArchiveSafely -ArchivePath $zip -DestinationPath $extract
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
