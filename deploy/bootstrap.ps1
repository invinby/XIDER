[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-09-24 (2).key",
    [string]$EnvRoot = $env:XIDER_ENV_ROOT,
    [string]$InstallRoot = "$env:LOCALAPPDATA\XIDER"
)

$ErrorActionPreference = 'Stop'
$repoUrl = 'https://github.com/invinby/XIDER/archive/refs/heads/main.zip'
$zip = Join-Path $env:TEMP 'xider-main.zip'
$extract = Join-Path $env:TEMP ('xider-bootstrap-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $InstallRoot 'git-ver'
$stagedKey = $null

try {
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    Invoke-WebRequest -Uri $repoUrl -OutFile $zip -UseBasicParsing
    if (Test-Path -LiteralPath $repo) { Remove-Item -LiteralPath $repo -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
    $downloaded = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
    if (-not $downloaded) { throw 'GitHub archive is empty.' }
    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Move-Item -LiteralPath $downloaded.FullName -Destination $repo

    if (-not $EnvRoot) {
        foreach ($candidateRoot in @((Split-Path $repo -Parent), "$env:USERPROFILE\Desktop\XIDER")) {
            $candidate = Join-Path $candidateRoot 'TG-BOT-SERVER'
            if (Test-Path -LiteralPath (Join-Path $candidate '.env')) {
                $EnvRoot = $candidateRoot
                break
            }
        }
    }
    $envSource = $null
    $agentEnvSource = $null
    if ($EnvRoot) {
        $envSource = Join-Path (Join-Path $EnvRoot 'TG-BOT-SERVER') '.env'
        $agentEnvSource = Join-Path (Join-Path $EnvRoot 'XGENT-WDS') '.env'
    }
    if (-not $EnvRoot -or -not (Test-Path -LiteralPath $envSource) -or -not (Test-Path -LiteralPath $agentEnvSource)) {
        Write-Host "XIDER скачан в $repo"
        Write-Host 'Нужен каталог с TG-BOT-SERVER\.env и XGENT-WDS\.env. Укажи его через -EnvRoot или XIDER_ENV_ROOT.'
        exit 3
    }
    $installRoot = Split-Path $repo -Parent
    $installBotDir = Join-Path $installRoot 'TG-BOT-SERVER'
    $installAgentDir = Join-Path $installRoot 'XGENT-WDS'
    New-Item -ItemType Directory -Path $installBotDir -Force | Out-Null
    New-Item -ItemType Directory -Path $installAgentDir -Force | Out-Null
    Copy-Item -LiteralPath $envSource -Destination (Join-Path $installBotDir '.env') -Force
    Copy-Item -LiteralPath $agentEnvSource -Destination (Join-Path $installAgentDir '.env') -Force
    # OpenSSH rejects keys that inherit the CodexSandboxUsers ACL. Stage a
    # temporary copy with read access for the current Windows user only.
    if (Test-Path -LiteralPath $KeyPath) {
        $stagedKey = Join-Path $env:TEMP ('xider-key-' + [guid]::NewGuid().ToString('N') + '.key')
        Copy-Item -LiteralPath $KeyPath -Destination $stagedKey -Force
        icacls.exe $stagedKey /inheritance:r | Out-Null
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        icacls.exe $stagedKey /grant:r "$($currentUser):(R)" | Out-Null
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'deploy\setup-all.ps1') -ServerIp $ServerIp -KeyPath $(if ($stagedKey) { $stagedKey } else { $KeyPath })
    if ($LASTEXITCODE -ne 0) { throw 'XIDER setup failed.' }
    Write-Host 'XIDER готов. Повторный запуск этой же команды обновит локальную копию и переустановит компоненты.'
}
finally {
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stagedKey -Force -ErrorAction SilentlyContinue
}
