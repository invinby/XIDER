[CmdletBinding()]
param(
    [string]$RuntimeDir = (Join-Path (Split-Path $PSScriptRoot -Parent) 'TG-BOT-SERVER')
)

$ErrorActionPreference = 'Stop'
$RuntimeDir = (Resolve-Path -LiteralPath $RuntimeDir).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path (Split-Path $RuntimeDir -Parent) "backups\runtime-reset-$stamp"
New-Item -ItemType Directory -Path $backup -Force | Out-Null

$files = @('known_devices.json', 'bot_settings.json', 'access_control.json', 'admin_audit.jsonl', 'audit.log')
foreach ($name in $files) {
    $source = Join-Path $RuntimeDir $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $backup $name) -Force
    }
}

Set-Content -LiteralPath (Join-Path $RuntimeDir 'known_devices.json') -Value '{}' -Encoding utf8
@{
    notify_online = $true
    notify_offline = $true
    notify_battery_low = $true
    quiet_from = ''
    quiet_to = ''
    report_hour = ''
    admins = @()
    blocked_ids = @()
    ui_style = 'technical'
    require_device_approval = $true
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeDir 'bot_settings.json') -Encoding utf8
'{"users": {}}' | Set-Content -LiteralPath (Join-Path $RuntimeDir 'access_control.json') -Encoding utf8
Set-Content -LiteralPath (Join-Path $RuntimeDir 'admin_audit.jsonl') -Value '' -Encoding utf8
Set-Content -LiteralPath (Join-Path $RuntimeDir 'audit.log') -Value '' -Encoding utf8

Write-Host "Runtime state cleared. Backup: $backup"
Write-Host 'No .env file, SSH key, bot token or broker credential was changed.'
