$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Windows bootstrap contains a PowerShell parse error.' }
$function = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Write-XiderInstallReport'
}, $true)
if (-not $function) { throw 'Windows install-report writer is missing.' }
Invoke-Expression $function.Extent.Text

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
    if ($report.outcome -ne 'succeeded' -or $report.components.agent.status -ne 'installed' -or
        $report.components.guardian.status -ne 'installed') {
        throw 'Windows report did not preserve the verified Agent and Guardian result.'
    }
    if ($report.components.agent.process.pid -ne $PID -or
        $report.components.agent.bot_response.status -ne 'not_verified' -or
        $report.components.safari.status -ne 'not_applicable' -or
        $report.components.siri.status -ne 'not_applicable') {
        throw 'Windows report has an incorrect process, bot-response, or platform result.'
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
