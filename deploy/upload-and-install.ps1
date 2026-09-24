[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-09-24 (2).key",
    [string]$EnvPath = '',
    [switch]$ResetRuntimeState
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $EnvPath) { $EnvPath = Join-Path (Split-Path $repo -Parent) 'TG-BOT-SERVER\.env' }
$envFile = (Resolve-Path $EnvPath).Path
$uploadEnv = Join-Path $env:TEMP ("xider-bot-{0}.env" -f ([guid]::NewGuid().ToString('N')))
$bundle = Join-Path $env:TEMP ("xider-source-{0}.zip" -f ([guid]::NewGuid().ToString('N')))
$resetLine = if ($ResetRuntimeState) { 'sudo bash /opt/xider/deploy/reset-runtime-state.sh' } else { ':' }

try {
    if (-not (Test-Path -LiteralPath $KeyPath)) { throw "SSH key not found: $KeyPath" }
    if (-not (Test-Path -LiteralPath $envFile)) { throw "Runtime env not found: $envFile" }

    $lines = @(Get-Content -LiteralPath $envFile)
    $encryption = $lines | Where-Object { $_ -match '^\s*ENCRYPT_PAYLOAD=' } | Select-Object -Last 1
    if ($encryption -and $encryption -notmatch '=\s*(1|true|yes)\s*$') {
        throw 'ENCRYPT_PAYLOAD is explicitly disabled in the runtime env; refusing an insecure deployment.'
    }
    if (-not $encryption) { $lines += 'ENCRYPT_PAYLOAD=true' }
    # Windows PowerShell 5.1 supports UTF8 (with BOM); the server installer
    # strips that optional BOM before validating the first variable.
    Set-Content -LiteralPath $uploadEnv -Value $lines -Encoding UTF8

    & tar.exe -a -c -f $bundle -C $repo `
        '--exclude=.git' `
        '--exclude=.pytest_cache' `
        '--exclude=__pycache__' `
        '--exclude=venv' `
        '--exclude=build' `
        '--exclude=dist' `
        '--exclude=.env' `
        '.'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create a portable source archive.' }

    Write-Host "[1/4] Checking SSH access to $ServerIp..."
    & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath "ubuntu@$ServerIp" 'echo XIDER_SSH_OK' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'SSH check failed. Fix the key ACL and retry; the server was not changed.' }

    Write-Host '[2/5] Uploading the runtime env (values are not printed)...'
    & scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath -- $uploadEnv "ubuntu@${ServerIp}:/tmp/xider-bot.env"
    if ($LASTEXITCODE -ne 0) { throw 'SCP failed; the server was not changed.' }

    Write-Host '[3/5] Uploading the current git-ver source bundle...'
    & scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath -- $bundle "ubuntu@${ServerIp}:/tmp/xider-source.zip"
    if ($LASTEXITCODE -ne 0) { throw 'Source upload failed; the server was not changed.' }

    Write-Host '[4/5] Installing the service and dependencies...'
    $remote = @'
set -eu
sudo install -d -m 0750 /etc/xider
sudo install -m 0600 /tmp/xider-bot.env /etc/xider/bot.env
rm -f /tmp/xider-bot.env
sudo apt-get update
sudo apt-get install -y unzip git
stage=$(sudo mktemp -d /opt/xider-stage.XXXXXX)
sudo unzip -q /tmp/xider-source.zip -d "$stage"
sudo install -d -m 0750 /opt/xider
sudo cp -a "$stage"/. /opt/xider/
sudo rm -rf "$stage" /tmp/xider-source.zip
__RESET_RUNTIME__
sudo env SKIP_REPO_SYNC=1 bash /opt/xider/deploy/server-install.sh
'@
    $remote = $remote.Replace('__RESET_RUNTIME__', $resetLine)
    & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath "ubuntu@$ServerIp" $remote | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Remote install failed; inspect journalctl -u xider-bot.' }

    Write-Host '[5/5] Done. The bot is enabled as xider-bot.service.'
    if ($ResetRuntimeState) { Write-Host 'Runtime state was backed up and reset; .env secrets were preserved.' }
    Write-Host "Logs: ssh -i `"$KeyPath`" ubuntu@$ServerIp 'sudo journalctl -u xider-bot -f'"
}
finally {
    Remove-Item -LiteralPath $uploadEnv -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
}
