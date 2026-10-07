[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$ServerHost = '16.16.200.207',
    [string]$ServerUser = 'ubuntu',
    [string]$Branch = 'main',
    [string]$Ref = $env:XIDER_REF,
    [ValidateRange(1, 120)][int]$HealthTimeoutSeconds = 30,
    [switch]$PreflightOnly,
    [string]$SourceArchive
)

$ErrorActionPreference = 'Stop'
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
$ProgressPreference = 'SilentlyContinue'
$extract = Join-Path $env:TEMP ('xider-agent-' + [guid]::NewGuid().ToString('N'))
$zip = Join-Path $extract 'source.zip'
$unpack = Join-Path $extract 'unpacked'
$repo = Join-Path $InstallRoot 'git-ver'
$agent = Join-Path $repo 'XGENT-WDS'
$stage = $null
$backup = $null
$failed = $null
$oldTasks = @{}
$tasksStopped = $false
$activationStarted = $false
$taskInstallAttempted = $false

function Test-XiderManagedProcess {
    param(
        [Parameter(Mandatory)]$Process,
        [Parameter(Mandatory)][string]$AgentDirectory
    )

    $agentRoot = [IO.Path]::GetFullPath($AgentDirectory).TrimEnd([char[]]@([char]92, [char]47))
    $managedExecutable = Join-Path $agentRoot 'XGENT-WDS.exe'
    if ($Process.ExecutablePath) {
        try {
            $processExecutable = [IO.Path]::GetFullPath([string]$Process.ExecutablePath)
            if ([string]::Equals(
                $processExecutable,
                $managedExecutable,
                [StringComparison]::OrdinalIgnoreCase
            )) { return $true }
        } catch { }
    }

    if ([string]$Process.Name -notin @('python.exe', 'pythonw.exe')) { return $false }
    $commandLine = [string]$Process.CommandLine
    foreach ($scriptName in @('xgent_wds.py', 'xider_guardian_wds.py')) {
        $scriptPath = Join-Path $agentRoot $scriptName
        $scriptArgument = '(?i)(?:^|\s|")' + [regex]::Escape($scriptPath) + '(?:"|\s|$)'
        if ($commandLine -match $scriptArgument) { return $true }
    }
    return $false
}

function Get-XiderManagedProcesses {
    param([Parameter(Mandatory)][string]$AgentDirectory)

    foreach ($process in Get-CimInstance -ClassName Win32_Process -ErrorAction Stop) {
        if (Test-XiderManagedProcess -Process $process -AgentDirectory $AgentDirectory) {
            [pscustomobject]@{
                ProcessId = [int]$process.ProcessId
                Name = [string]$process.Name
                ExecutablePath = [string]$process.ExecutablePath
                CommandLine = [string]$process.CommandLine
                CreationDate = [string]$process.CreationDate
            }
        }
    }
}

function Stop-XiderManagedProcesses {
    param([Parameter(Mandatory)][string]$AgentDirectory)

    $remaining = @()
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $remaining = @(Get-XiderManagedProcesses -AgentDirectory $AgentDirectory)
        if (-not $remaining.Count) { return }
        Start-Sleep -Seconds 1
    }
    foreach ($managedProcess in $remaining) {
        $processId = [int]$managedProcess.ProcessId
        if (-not $managedProcess.CreationDate) {
            throw "Не удалось подтвердить личность процесса XIDER PID $processId; каталог оставлен без изменений."
        }
        $currentProcess = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $processId" -ErrorAction Stop
        if (-not $currentProcess -or
            [string]$currentProcess.CreationDate -ne [string]$managedProcess.CreationDate -or
            -not (Test-XiderManagedProcess -Process $currentProcess -AgentDirectory $AgentDirectory)) {
            continue
        }
        try { Stop-Process -Id $processId -Force -ErrorAction Stop }
        catch {
            if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
                throw "Не удалось остановить процесс XIDER PID $processId перед заменой файлов."
            }
        }
    }
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        $remaining = @(Get-XiderManagedProcesses -AgentDirectory $AgentDirectory)
        if (-not $remaining.Count) { return }
        Start-Sleep -Seconds 1
    }
    $remainingIds = @($remaining | ForEach-Object { $_.ProcessId }) -join ', '
    throw "Процессы XIDER всё ещё используют каталог агента: $remainingIds"
}

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
    if ($SourceArchive) { Write-Host '[1/5] Проверяю локальный архив XIDER...' }
    elseif ($Ref) { Write-Host "[1/5] Скачиваю XIDER из закреплённого commit $Ref (тайм-аут 90 секунд)..." }
    else { Write-Host "[1/5] Скачиваю XIDER из ветки $Branch (тайм-аут 90 секунд)..." }
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    if ($SourceArchive) {
        Copy-Item -LiteralPath $SourceArchive -Destination $zip -ErrorAction Stop
    } else {
        Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing -TimeoutSec 90
    }
    Expand-XiderArchiveSafely -ArchivePath $zip -DestinationPath $unpack
    $downloaded = Get-ChildItem -LiteralPath $unpack -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }
    foreach ($requiredFile in @(
        'XGENT-WDS\xgent_wds.py',
        'XGENT-WDS\xider_guardian_wds.py',
        'XGENT-WDS\config.py',
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
        Write-Host "Локальный .env не найден. Пробую SSH-ключ/ssh-agent, затем пароль VPS при запросе: $ServerUser@$ServerHost."
        $fetched = Join-Path $extract 'agent.env'
        $remoteCommand = "sudo -n sh -c 'grep -E `"^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)=`" /etc/xider/bot.env'"
        $sshArguments = @(
            '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2'
        )
        $sshKey = $env:XIDER_SSH_KEY
        if (-not $sshKey) {
            $defaultSshKey = Join-Path $env:USERPROFILE '.ssh\xider'
            if (Test-Path -LiteralPath $defaultSshKey -PathType Leaf) { $sshKey = $defaultSshKey }
        }
        if ($sshKey) {
            if (-not (Test-Path -LiteralPath $sshKey -PathType Leaf)) {
                throw "SSH-ключ не найден: $sshKey. VPS и файлы агента не изменены."
            }
            $sshArguments += @('-i', $sshKey, '-o', 'IdentitiesOnly=yes')
        }
        & ssh.exe @sshArguments `
            "$ServerUser@$ServerHost" $remoteCommand | Set-Content -LiteralPath $fetched -Encoding utf8
        $sshExit = $LASTEXITCODE
        if ($sshExit -ne 0) { throw "SSH не смог получить настройки (код $sshExit). VPS и файлы агента не изменены." }
        $allowed = Get-Content -LiteralPath $fetched | Where-Object {
            $_ -match '^(SHARED_KEY|MQTT_BROKER|MQTT_PORT|MQTT_PREFIX|MQTT_TLS|MQTT_USERNAME|MQTT_PASSWORD|ENCRYPT_PAYLOAD)='
        }
        $prefixEntries = @($allowed | Where-Object { $_ -match '^MQTT_PREFIX=' })
        if ($prefixEntries.Count -gt 1) {
            throw 'На VPS параметр MQTT_PREFIX указан несколько раз. Файлы агента не изменены.'
        }
        if ($prefixEntries.Count -eq 0) {
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

    $requiredSettings = @(
        'SHARED_KEY', 'MQTT_BROKER', 'MQTT_PORT', 'MQTT_PREFIX',
        'MQTT_TLS', 'MQTT_USERNAME', 'MQTT_PASSWORD', 'ENCRYPT_PAYLOAD'
    )
    $configLines = @(Get-Content -LiteralPath $envSource -ErrorAction Stop)
    $prefixPattern = '^\s*(?:export\s+)?MQTT_PREFIX\s*=\s*(.*)$'
    $prefixLines = @($configLines | Where-Object { $_ -match $prefixPattern })
    if ($prefixLines.Count -gt 1) {
        throw 'В конфигурации MQTT_PREFIX указан несколько раз. Рабочая установка не изменена.'
    }
    if ($prefixLines.Count -eq 0) {
        $configLines += 'MQTT_PREFIX=xgent/v1'
        $normalizedEnv = Join-Path $extract 'agent.normalized.env'
        [IO.File]::WriteAllLines($normalizedEnv, [string[]]$configLines, [System.Text.UTF8Encoding]::new($false))
        $envSource = $normalizedEnv
    } else {
        $prefixValue = [regex]::Match([string]$prefixLines[0], $prefixPattern).Groups[1].Value.Trim().Trim('"', "'").Trim()
        if ($prefixValue -notmatch '^[A-Za-z0-9._/-]+$') {
            throw 'В конфигурации MQTT_PREFIX пустой или имеет неподдерживаемый формат. Рабочая установка не изменена.'
        }
    }
    $settings = @{}
    foreach ($required in $requiredSettings) {
        $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($required) + '\s*=\s*(.*)$'
        $matchingLines = @($configLines | Where-Object { $_ -match $pattern })
        if ($matchingLines.Count -ne 1) {
            throw "Параметр $required отсутствует или указан несколько раз. Рабочая установка не изменена."
        }
        $value = if ($matchingLines.Count) {
            [regex]::Match($matchingLines[-1], $pattern).Groups[1].Value.Trim().Trim('"', "'").Trim()
        } else { '' }
        if (-not $value -or $value.StartsWith('#')) {
            throw "В конфигурации агента отсутствует $required. Рабочая установка не изменена."
        }
        $settings[$required] = $value
    }
    if ($settings['MQTT_TLS'].ToLowerInvariant() -notin @('true', '1', 'yes')) {
        throw 'Для установки агента требуется MQTT_TLS=true. Рабочая установка не изменена.'
    }
    if ($settings['ENCRYPT_PAYLOAD'].ToLowerInvariant() -notin @('true', '1', 'yes')) {
        throw 'Для установки агента требуется ENCRYPT_PAYLOAD=true. Рабочая установка не изменена.'
    }
    $mqttPort = 0
    if (-not [int]::TryParse($settings['MQTT_PORT'], [ref]$mqttPort) -or $mqttPort -lt 1 -or $mqttPort -gt 65535) {
        throw 'MQTT_PORT должен быть числом от 1 до 65535. Рабочая установка не изменена.'
    }
    if ($PreflightOnly) {
        if ($SourceArchive) { $sourceLabel = 'Локальный архив' }
        elseif ($Ref) { $sourceLabel = "Архив commit $Ref" }
        else { $sourceLabel = "Архив ветки $Branch" }
        Write-Host "[OK] $sourceLabel и конфигурация агента проверены. Установка не запускалась."
        return
    }

    Write-Host '[3/5] Подготавливаю новую версию рядом с рабочей...'
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    # Keep the caller's canonical long path. Resolve-Path may return an 8.3
    # alias (for example RUNNER~1) while Join-Path/GetFullPath return its long
    # name, making string-based containment checks disagree on the same folder.
    $resolvedRoot = [IO.Path]::GetFullPath($InstallRoot)
    $rootOfVolume = [IO.Path]::GetPathRoot($resolvedRoot)
    if ($resolvedRoot.Length -gt $rootOfVolume.Length) {
        $resolvedRoot = $resolvedRoot.TrimEnd([char[]]@('\', '/'))
    }
    $stage = Join-Path $resolvedRoot ('git-ver.stage.' + [guid]::NewGuid().ToString('N'))
    $backup = Join-Path $resolvedRoot ('git-ver.previous.' + (Get-Date -Format 'yyyyMMddHHmmss') + '.' + [guid]::NewGuid().ToString('N'))
    $failed = Join-Path $resolvedRoot ('git-ver.failed.' + (Get-Date -Format 'yyyyMMddHHmmss') + '.' + [guid]::NewGuid().ToString('N'))
    foreach ($path in @($stage, $backup, $failed, $repo)) {
        $fullPath = [IO.Path]::GetFullPath($path)
        # These generated targets must be direct children of InstallRoot.
        # Comparing their canonical parent is explicit and avoids prefix matches.
        $fullParent = [IO.Path]::GetDirectoryName($fullPath)
        $parentRoot = [IO.Path]::GetPathRoot($fullParent)
        if ($fullParent.Length -gt $parentRoot.Length) {
            $fullParent = $fullParent.TrimEnd([char[]]@('\', '/'))
        }
        if (-not [string]::Equals($fullParent, $resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Недопустимый путь установки: $fullPath (parent=$fullParent; root=$resolvedRoot)"
        }
    }
    Copy-Item -LiteralPath $downloaded.FullName -Destination $stage -Recurse -Force
    $stageAgent = Join-Path $stage 'XGENT-WDS'
    $stageEnv = Join-Path $stageAgent '.env'
    # Copy instead of moving the original protected .env. The original stays
    # untouched inside the previous checkout until activation has succeeded.
    Copy-Item -LiteralPath $envSource -Destination $stageEnv -Force

    Write-Host '[4/5] Собираю отдельное окружение Python и ставлю зависимости...'
    $venv = Join-Path $stageAgent 'venv'
    $pythonLauncher = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
    if ($pythonLauncher) { & $pythonLauncher -3 -m venv $venv }
    else { & (Get-Command python.exe -ErrorAction Stop).Source -m venv $venv }
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать Python venv; рабочая версия не изменена.' }
    $stagePython = Join-Path $venv 'Scripts\python.exe'
    & $stagePython -m pip install --disable-pip-version-check --no-input -r (Join-Path $stageAgent 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости Python.' }
    & $stagePython -m py_compile (Join-Path $stageAgent 'xgent_wds.py') (Join-Path $stageAgent 'xider_guardian_wds.py')
    if ($LASTEXITCODE -ne 0) { throw 'Код агента не компилируется; рабочая версия не изменена.' }

    Write-Host '[5/5] Переключаю версию и регистрирую Agent + Guardian...'
    foreach ($taskName in @('XIDER Agent', 'XIDER Guardian')) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            $oldTasks[$taskName] = @{
                Xml = Export-ScheduledTask -TaskName $taskName
                WasRunning = ($task.State -eq 'Running')
            }
        }
    }
    # Stop the supervisor first so it cannot restart the worker during swap.
    foreach ($taskName in @('XIDER Guardian', 'XIDER Agent')) {
        if ($oldTasks.ContainsKey($taskName) -and $oldTasks[$taskName].WasRunning) {
            Stop-ScheduledTask -TaskName $taskName
            $tasksStopped = $true
        }
    }
    $stillRunning = @()
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $stillRunning = @($oldTasks.Keys | Where-Object {
            $oldTasks[$_].WasRunning -and (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue).State -eq 'Running'
        })
        if (-not $stillRunning.Count) { break }
        Start-Sleep -Seconds 1
    }
    if ($stillRunning.Count) { throw "Прежние задачи не остановились: $($stillRunning -join ', ')" }
    if (Test-Path -LiteralPath $repo) {
        Stop-XiderManagedProcesses -AgentDirectory (Join-Path $repo 'XGENT-WDS')
        [IO.Directory]::Move($repo, $backup)
    }
    [IO.Directory]::Move($stage, $repo)
    $activationStarted = $true
    $activePython = Join-Path $agent 'venv\Scripts\python.exe'
    & $activePython -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Новое окружение Python не работает после переключения; выполняю откат.' }
    $versionMatch = [regex]::Match(
        (Get-Content -LiteralPath (Join-Path $agent 'config.py') -Raw),
        '(?m)^VERSION\s*=\s*"(?<version>[^"]+)"'
    )
    if (-not $versionMatch.Success) { throw 'Не удалось определить версию агента для проверки здоровья; выполняю откат.' }
    $expectedAgentVersion = $versionMatch.Groups['version'].Value

    # Task Scheduler's Running state only proves that a task was launched.
    # Clear per-process markers so a previous install cannot satisfy this one.
    $healthDirectory = Join-Path $env:USERPROFILE '.xgent'
    foreach ($healthName in @('agent-health.json', 'guardian-health.json')) {
        $healthPath = Join-Path $healthDirectory $healthName
        if (Test-Path -LiteralPath $healthPath -PathType Leaf) {
            Remove-Item -LiteralPath $healthPath -Force -ErrorAction Stop
        }
    }
    $healthProbeStartedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $taskInstallAttempted = $true
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $agent 'install_agent.ps1') -AgentDir $agent -PreferPython
    if ($LASTEXITCODE -ne 0) { throw 'Установка Windows-агента/Guardian завершилась ошибкой.' }
    $healthy = $false
    $healthySamples = 0
    for ($attempt = 0; $attempt -lt $HealthTimeoutSeconds; $attempt++) {
        $agentTask = Get-ScheduledTask -TaskName 'XIDER Agent' -ErrorAction SilentlyContinue
        $guardianTask = Get-ScheduledTask -TaskName 'XIDER Guardian' -ErrorAction SilentlyContinue
        $componentsHealthy = $false
        if ($agentTask -and $guardianTask -and $agentTask.State -eq 'Running' -and $guardianTask.State -eq 'Running') {
            $expectedAgent = Join-Path $agent 'venv\Scripts\pythonw.exe'
            $expectedGuardian = Join-Path $agent 'venv\Scripts\python.exe'
            $agentExecutable = [string]($agentTask.Actions | Select-Object -First 1 -ExpandProperty Execute)
            $guardianExecutable = [string]($guardianTask.Actions | Select-Object -First 1 -ExpandProperty Execute)
            if ([string]::Equals($agentExecutable, $expectedAgent, [StringComparison]::OrdinalIgnoreCase) -and
                [string]::Equals($guardianExecutable, $expectedGuardian, [StringComparison]::OrdinalIgnoreCase)) {
                $healthStates = @{}
                foreach ($component in @('agent', 'guardian')) {
                    $healthPath = Join-Path $healthDirectory "$component-health.json"
                    if (-not (Test-Path -LiteralPath $healthPath -PathType Leaf)) { break }
                    try {
                        $health = Get-Content -LiteralPath $healthPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                        $updatedAt = 0.0
                        $pidValue = 0
                        $updatedAtText = ([double]$health.updated_at).ToString(
                            'R', [Globalization.CultureInfo]::InvariantCulture
                        )
                        $isFreshTimestamp = [double]::TryParse(
                            $updatedAtText,
                            [Globalization.NumberStyles]::Float,
                            [Globalization.CultureInfo]::InvariantCulture,
                            [ref]$updatedAt
                        )
                        $isValidPid = [int]::TryParse([string]$health.pid, [ref]$pidValue)
                        if ([string]$health.component -ne $component -or
                            [string]$health.version -ne $expectedAgentVersion -or $health.connected -ne $true -or
                            -not $isFreshTimestamp -or -not $isValidPid -or $pidValue -le 0 -or
                            $updatedAt -lt $healthProbeStartedAt -or
                            ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 - $updatedAt) -gt 15 -or
                            -not (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
                            break
                        }
                        $healthStates[$component] = $health
                    } catch { break }
                }
                if ($healthStates.Count -eq 2 -and
                    [int]$healthStates.agent.pid -ne [int]$healthStates.guardian.pid) {
                    $componentsHealthy = $true
                }
            }
        }
        if ($componentsHealthy) { $healthySamples++ } else { $healthySamples = 0 }
        if ($healthySamples -ge 2) {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw 'Agent и Guardian не подтвердили свежее MQTT-соединение; выполняю откат.' }
    Write-Host '[OK] XIDER Windows Agent + Guardian установлены и запущены.'
    if (Test-Path -LiteralPath $backup) { Write-Host "[BACKUP] Предыдущая версия сохранена: $backup" }
}
catch {
    $originalError = $_
    $rollbackProblems = @()
    if ($taskInstallAttempted) {
        foreach ($taskName in @('XIDER Guardian', 'XIDER Agent')) {
            try {
                $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
                if ($task -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $taskName }
            } catch { $rollbackProblems += "Не удалось остановить ${taskName}: $($_.Exception.Message)" }
        }
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            $stillRunning = @(@('XIDER Agent', 'XIDER Guardian') | Where-Object {
                (Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue).State -eq 'Running'
            })
            if (-not $stillRunning.Count) { break }
            Start-Sleep -Seconds 1
        }
        if ($stillRunning.Count) { $rollbackProblems += "Новые задачи не остановились: $($stillRunning -join ', ')" }
    }
    if ($activationStarted -and (Test-Path -LiteralPath $repo)) {
        try {
            Stop-XiderManagedProcesses -AgentDirectory (Join-Path $repo 'XGENT-WDS')
            [IO.Directory]::Move($repo, $failed)
            $failedEnv = Join-Path $failed 'XGENT-WDS\.env'
            if (Test-Path -LiteralPath $failedEnv) { Remove-Item -LiteralPath $failedEnv -Force }
        } catch { $rollbackProblems += "Не удалось убрать неудачную версию: $($_.Exception.Message)" }
    }
    if ($backup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $repo)) {
        try { [IO.Directory]::Move($backup, $repo) }
        catch { $rollbackProblems += "Не удалось вернуть предыдущие файлы: $($_.Exception.Message)" }
    }
    if ($taskInstallAttempted) {
        foreach ($taskName in @('XIDER Agent', 'XIDER Guardian')) {
            try {
                if ($oldTasks.ContainsKey($taskName)) {
                    Register-ScheduledTask -TaskName $taskName -Xml $oldTasks[$taskName].Xml -Force | Out-Null
                } elseif (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
                    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
                }
            } catch { $rollbackProblems += "Не удалось восстановить задачу ${taskName}: $($_.Exception.Message)" }
        }
    }
    if ($tasksStopped) {
        foreach ($taskName in @('XIDER Agent', 'XIDER Guardian')) {
            if ($oldTasks.ContainsKey($taskName) -and $oldTasks[$taskName].WasRunning) {
                try { Start-ScheduledTask -TaskName $taskName }
                catch { $rollbackProblems += "Не удалось перезапустить ${taskName}: $($_.Exception.Message)" }
            }
        }
    }
    $problemText = if ($rollbackProblems.Count) { ' Откат неполный: ' + ($rollbackProblems -join '; ') } else { '' }
    throw "Установка Windows-агента не завершена: $($originalError.Exception.Message).$problemText"
}
finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        $stageEnv = Join-Path $stage 'XGENT-WDS\.env'
        if (Test-Path -LiteralPath $stageEnv) { Remove-Item -LiteralPath $stageEnv -Force -ErrorAction SilentlyContinue }
        Write-Warning "Незавершённая подготовка оставлена без .env: $stage"
    }
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
}
