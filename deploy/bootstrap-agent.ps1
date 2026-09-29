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
$stage = $null
$backup = $null
$failed = $null
$oldTasks = @{}
$tasksStopped = $false
$activationStarted = $false
$taskInstallAttempted = $false

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

    Write-Host '[3/5] Подготавливаю новую версию рядом с рабочей...'
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    $resolvedRoot = (Resolve-Path -LiteralPath $InstallRoot).Path.TrimEnd('\')
    $stage = Join-Path $resolvedRoot ('git-ver.stage.' + [guid]::NewGuid().ToString('N'))
    $backup = Join-Path $resolvedRoot ('git-ver.previous.' + (Get-Date -Format 'yyyyMMddHHmmss') + '.' + [guid]::NewGuid().ToString('N'))
    $failed = Join-Path $resolvedRoot ('git-ver.failed.' + (Get-Date -Format 'yyyyMMddHHmmss') + '.' + [guid]::NewGuid().ToString('N'))
    foreach ($path in @($stage, $backup, $failed, $repo)) {
        $fullPath = [IO.Path]::GetFullPath($path)
        if (-not $fullPath.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Недопустимый путь установки: $fullPath"
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
    if (Test-Path -LiteralPath $repo) { Move-Item -LiteralPath $repo -Destination $backup }
    Move-Item -LiteralPath $stage -Destination $repo
    $activationStarted = $true
    $activePython = Join-Path $agent 'venv\Scripts\python.exe'
    & $activePython -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Новое окружение Python не работает после переключения; выполняю откат.' }
    $taskInstallAttempted = $true
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $agent 'install_agent.ps1') -AgentDir $agent -PreferPython
    if ($LASTEXITCODE -ne 0) { throw 'Установка Windows-агента/Guardian завершилась ошибкой.' }
    $healthy = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $agentTask = Get-ScheduledTask -TaskName 'XIDER Agent' -ErrorAction SilentlyContinue
        $guardianTask = Get-ScheduledTask -TaskName 'XIDER Guardian' -ErrorAction SilentlyContinue
        if ($agentTask -and $guardianTask -and $agentTask.State -eq 'Running' -and $guardianTask.State -eq 'Running') {
            $expectedAgent = Join-Path $agent 'venv\Scripts\pythonw.exe'
            $expectedGuardian = Join-Path $agent 'venv\Scripts\python.exe'
            $agentExecutable = [string]($agentTask.Actions | Select-Object -First 1 -ExpandProperty Execute)
            $guardianExecutable = [string]($guardianTask.Actions | Select-Object -First 1 -ExpandProperty Execute)
            if ([string]::Equals($agentExecutable, $expectedAgent, [StringComparison]::OrdinalIgnoreCase) -and
                [string]::Equals($guardianExecutable, $expectedGuardian, [StringComparison]::OrdinalIgnoreCase)) {
                $healthy = $true
                break
            }
        }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw 'Agent или Guardian не перешёл в Running; выполняю откат.' }
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
            Move-Item -LiteralPath $repo -Destination $failed
            $failedEnv = Join-Path $failed 'XGENT-WDS\.env'
            if (Test-Path -LiteralPath $failedEnv) { Remove-Item -LiteralPath $failedEnv -Force }
        } catch { $rollbackProblems += "Не удалось убрать неудачную версию: $($_.Exception.Message)" }
    }
    if ($backup -and (Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $repo)) {
        try { Move-Item -LiteralPath $backup -Destination $repo }
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
