$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Windows bootstrap contains a PowerShell parse error.' }
$functions = @{}
foreach ($name in @('Write-XiderInstallReport', 'Get-XiderActiveInstallVersion', 'Get-XiderVerifiedActiveHealth')) {
    $function = $ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst]
    }, $true) | Where-Object { $_.Name -eq $name } | Select-Object -First 1
    if (-not $function) { throw "Windows bootstrap function is missing: $name" }
    $functions[$name] = $function
    Invoke-Expression $function.Extent.Text
}

function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    $processId = [int]([regex]::Match($Filter, '\d+').Value)
    return [pscustomobject]@{ ProcessId = $processId; Name = 'pythonw.exe'; CommandLine = 'xgent_wds.py'; ExecutablePath = '' }
}
function Test-XiderManagedProcess { param($Process, $AgentDirectory) return $true }
function Get-ScheduledTask { param($TaskName, $ErrorAction) return [pscustomobject]@{ State = 'Running' } }
function Get-Process { param($Id, $ErrorAction) return [pscustomobject]@{ ProcessName = 'pythonw' } }

$reportRoot = Join-Path $env:TEMP ('xider-install-report-' + [guid]::NewGuid().ToString('N'))
$installReportPath = Join-Path $reportRoot 'install-report.json'
$installReportId = 'install-test-' + [guid]::NewGuid().ToString('N')
try {
    $health = @{
        agent = @{ pid = $PID; connected = $true; version = '4.3.8' }
        guardian = @{ pid = $PID; connected = $true; version = '4.3.8' }
    }
    Write-XiderInstallReport -Outcome succeeded -RequestedVersion '4.3.8' `
        -HealthStates $health -Required
    $report = Get-Content -LiteralPath $installReportPath -Raw | ConvertFrom-Json
    if ($report.outcome -ne 'succeeded' -or $report.requested_version -ne '4.3.8' -or
        $report.components.agent.status -ne 'installed' -or
        $report.components.guardian.status -ne 'installed') {
        throw 'Windows report did not preserve the verified Agent and Guardian result.'
    }
    if ($report.components.agent.process.pid -ne $PID -or
        $report.components.agent.bot_response.status -ne 'not_verified' -or
        $report.components.safari.status -ne 'not_applicable' -or
        $report.components.siri.status -ne 'not_applicable') {
        throw 'Windows report has an incorrect process, bot-response, or platform result.'
    }

    $agentDirectory = Join-Path $reportRoot 'git-ver\XGENT-WDS'
    $healthDirectory = Join-Path $reportRoot 'health'
    New-Item -ItemType Directory -Path $agentDirectory, $healthDirectory -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $agentDirectory 'config.py'), 'VERSION = "4.3.8"', [Text.UTF8Encoding]::new($false))
    $activeTimestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    foreach ($component in @('agent', 'guardian')) {
        $pidValue = if ($component -eq 'agent') { 101 } else { 102 }
        $health = @{ component = $component; version = '4.3.8'; connected = $true; pid = $pidValue; updated_at = $activeTimestamp }
        $health | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $healthDirectory "$component-health.json") -Encoding utf8
    }
    if ((Get-XiderActiveInstallVersion -AgentDirectory $agentDirectory) -ne '4.3.8') {
        throw 'Windows active source version could not be recovered for the final report.'
    }
    $active = Get-XiderVerifiedActiveHealth -AgentDirectory $agentDirectory -HealthDirectory $healthDirectory `
        -ExpectedVersion '4.3.8' -StartedAt ($activeTimestamp - 2) -TimeoutSeconds 1
    if (-not $active.Verified -or $active.HealthStates.Count -ne 2) {
        throw 'Windows rollback health verifier did not accept the fresh active Agent and Guardian.'
    }
    Write-XiderInstallReport -Outcome failed -RequestedVersion '4.3.9' `
        -ErrorMessage 'new version failed; previous version restored' -HealthStates $active.HealthStates -Required
    $restored = Get-Content -LiteralPath $installReportPath -Raw | ConvertFrom-Json
    if ($restored.outcome -ne 'failed' -or $restored.requested_version -ne '4.3.9' -or
        $restored.components.agent.version -ne '4.3.8' -or
        $restored.components.agent.status -ne 'failed' -or
        $restored.components.agent.process.running -ne $true) {
        throw 'Windows rollback report did not describe the active previous version and failed requested version.'
    }

    Write-XiderInstallReport -Outcome partial -RequestedVersion '4.3.8' `
        -ErrorMessage 'Guardian health was not verified' `
        -HealthStates @{ agent = @{ pid = $PID; connected = $true; version = '4.3.8' } } -Required
    $partial = Get-Content -LiteralPath $installReportPath -Raw | ConvertFrom-Json
    if ($partial.outcome -ne 'partial' -or $partial.components.agent.status -ne 'installed' -or
        $partial.components.guardian.status -ne 'unknown' -or
        $partial.components.agent.process.pid -ne $PID) {
        throw 'Partial Windows report did not derive each component from its verified active state.'
    }

    Write-XiderInstallReport -Outcome failed -RequestedVersion '4.3.8' `
        -ErrorMessage 'source archive EOF' -HealthStates $null -Required
    $failed = Get-Content -LiteralPath $installReportPath -Raw | ConvertFrom-Json
    if ($failed.outcome -ne 'failed' -or $failed.components.agent.error -ne 'source archive EOF') {
        throw 'A failed install report did not retain the original failure with a null health table.'
    }
    if (Get-ChildItem -LiteralPath $reportRoot -Filter '*.tmp' -ErrorAction SilentlyContinue) {
        throw 'Windows report left a temporary file behind.'
    }
    Write-Host 'Windows install-report fixture passed.'
} finally {
    if ($reportRoot -like (Join-Path $env:TEMP 'xider-install-report-*')) {
        Remove-Item -LiteralPath $reportRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
