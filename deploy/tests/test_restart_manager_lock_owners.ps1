$ErrorActionPreference = 'Stop'

$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) {
    throw "Bootstrap PowerShell syntax is invalid: $($parseErrors[0].Message)"
}

$functionAst = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-XiderDirectoryLockOwners'
}, $true)
if (-not $functionAst) {
    throw 'Get-XiderDirectoryLockOwners was not found in bootstrap-agent.ps1.'
}
. ([scriptblock]::Create($functionAst.Extent.Text))

$fixture = Join-Path $env:TEMP ('xider-rm-fixture-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $fixture
$file = Join-Path $fixture 'held-open.bin'
$handle = $null
try {
    [IO.File]::WriteAllBytes($file, [byte[]](1, 2, 3))
    $handle = [IO.File]::Open($file, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)

    $owners = @(Get-XiderDirectoryLockOwners -DirectoryPath $fixture)
    if (-not ($owners | Where-Object { $_.ProcessId -eq $PID })) {
        throw 'Restart Manager did not report the PowerShell process holding the fixture file.'
    }

    Write-Output ("RESTART_MANAGER_FIXTURE=PASS pid={0}" -f $PID)
} finally {
    if ($handle) { $handle.Dispose() }
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        Remove-Item -LiteralPath $file -Force
    }
    if (Test-Path -LiteralPath $fixture -PathType Container) {
        Remove-Item -LiteralPath $fixture -Force
    }
}
