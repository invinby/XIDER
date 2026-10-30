$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'The Windows agent bootstrap could not be parsed.' }
$definition = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-XiderExpectedGuardianExecutable'
}, $true)
if (-not $definition) { throw 'The bootstrap has no testable Guardian executable selector.' }
. ([scriptblock]::Create($definition.Extent.Text))

$agentDirectory = 'C:\XIDER\git-ver\XGENT-WDS'
$developmentPath = Get-XiderExpectedGuardianExecutable -AgentDirectory $agentDirectory
if ($developmentPath -cne (Join-Path $agentDirectory 'venv\Scripts\pythonw.exe')) {
    throw "Development Guardian health check expected '$developmentPath'; installer registers pythonw.exe."
}

$releasePath = Get-XiderExpectedGuardianExecutable -AgentDirectory $agentDirectory -ReleaseTag 'v4.3.3'
if ($releasePath -cne (Join-Path $agentDirectory 'XIDER-Guard-Keeper.exe')) {
    throw "Release Guardian health check selected unexpected executable '$releasePath'."
}

Write-Host 'Windows Guardian health-check executable selector passed.'
