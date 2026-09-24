[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-09-24 (2).key",
    [string]$EnvPath = "$PSScriptRoot\..\..\TG-BOT-SERVER\.env"
)

$ErrorActionPreference = 'Stop'
$envFile = (Resolve-Path $EnvPath).Path
$uploadEnv = Join-Path $env:TEMP ("xider-bot-{0}.env" -f ([guid]::NewGuid().ToString('N')))

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

    Write-Host "[1/4] Checking SSH access to $ServerIp..."
    & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath "ubuntu@$ServerIp" 'echo XIDER_SSH_OK' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'SSH check failed. Fix the key ACL and retry; the server was not changed.' }

    Write-Host '[2/4] Uploading the runtime env (values are not printed)...'
    & scp -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath -- $uploadEnv "ubuntu@${ServerIp}:/tmp/xider-bot.env"
    if ($LASTEXITCODE -ne 0) { throw 'SCP failed; the server was not changed.' }

    Write-Host '[3/4] Installing the service and dependencies...'
    $remote = @'
set -eu
sudo install -d -m 0750 /etc/xider
sudo install -m 0600 /tmp/xider-bot.env /etc/xider/bot.env
rm -f /tmp/xider-bot.env
if ! command -v git >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y git; fi
if [ ! -d /opt/xider/.git ]; then
  sudo git clone --depth 1 --branch main https://github.com/invinby/XIDER.git /opt/xider
fi
sudo bash /opt/xider/deploy/server-install.sh
'@
    & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes -i $KeyPath "ubuntu@$ServerIp" $remote | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Remote install failed; inspect journalctl -u xider-bot.' }

    Write-Host '[4/4] Done. The bot is enabled as xider-bot.service.'
    Write-Host "Logs: ssh -i `"$KeyPath`" ubuntu@$ServerIp 'sudo journalctl -u xider-bot -f'"
}
finally {
    Remove-Item -LiteralPath $uploadEnv -Force -ErrorAction SilentlyContinue
}
