[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\Downloads\ssh-key-2026-09-24 (2).key"
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$root = (Resolve-Path (Join-Path $repo '..')).Path
$agentDir = Join-Path $repo 'XGENT-WDS'
$sourceEnv = Join-Path $root 'XGENT-WDS\.env'

Write-Host '=== 1/3: deploy XIDER bot to xider VPS ==='
& (Join-Path $PSScriptRoot 'upload-and-install.ps1') -ServerIp $ServerIp -KeyPath $KeyPath
if ($LASTEXITCODE -ne 0) { throw 'VPS deployment failed.' }

Write-Host '=== 2/3: prepare Windows agent sidecar config ==='
if (-not (Test-Path -LiteralPath $sourceEnv)) { throw "Missing runtime env: $sourceEnv" }
Copy-Item -LiteralPath $sourceEnv -Destination (Join-Path $agentDir '.env') -Force
if (Test-Path -LiteralPath (Join-Path $agentDir 'XGENT-WDS.exe')) {
    schtasks /End /TN 'XIDER Agent' >$null 2>&1
}

Write-Host '=== 3/3: build and register hidden Windows agent ==='
& cmd.exe /d /c (Join-Path $agentDir 'build_exe.bat')
if ($LASTEXITCODE -ne 0) { throw 'Windows agent build failed.' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $agentDir 'install_agent.ps1') -AgentDir $agentDir

Write-Host '[OK] Bot VPS and Windows background agent setup finished.'
