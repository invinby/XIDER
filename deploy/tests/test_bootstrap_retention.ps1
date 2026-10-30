$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "Bootstrap parse failed: $($parseErrors[0].Message)" }
$functionAst = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Remove-XiderOldPreviousCheckouts'
}, $true)
if (-not $functionAst) { throw 'Checkout retention helper is missing.' }
Invoke-Expression $functionAst.Extent.Text

$root = Join-Path $env:TEMP ('xider-retention-' + [guid]::NewGuid().ToString('N'))
$utf8 = New-Object System.Text.UTF8Encoding($false)
try {
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $names = @(
        'git-ver.previous.20261001000000.11111111111111111111111111111111',
        'git-ver.previous.20261002000000.22222222222222222222222222222222',
        'git-ver.previous.20261003000000.33333333333333333333333333333333',
        'git-ver.previous.20261004000000.44444444444444444444444444444444'
    )
    foreach ($name in $names) {
        $dir = Join-Path $root $name
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $dir 'rollback-marker.txt'), $name, $utf8)
    }
    $unrecognized = Join-Path $root 'git-ver.previous.owner-data'
    New-Item -ItemType Directory -Path $unrecognized -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $unrecognized 'keep.txt'), 'owner data', $utf8)

    Remove-XiderOldPreviousCheckouts -InstallRoot $root -CurrentBackup (Join-Path $root $names[3]) -Keep 2

    $managed = @(Get-ChildItem -LiteralPath $root -Directory -Force |
        Where-Object { $_.Name -match '^git-ver\.previous\.\d{14}\.[0-9a-fA-F]{32}$' } |
        Sort-Object -Property Name -Descending)
    if ($managed.Count -ne 2 -or $managed[0].Name -ne $names[3] -or $managed[1].Name -ne $names[2]) {
        throw "Expected the two newest managed rollback checkouts, found: $($managed.Name -join ', ')"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $unrecognized 'keep.txt'))) {
        throw 'An unrecognized sibling directory was modified.'
    }
    Write-Host 'Windows checkout retention helper passed.'
}
finally {
    if ((Test-Path -LiteralPath $root) -and
        [IO.Path]::GetFullPath($root).StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $root -Leaf) -like 'xider-retention-*') {
        Remove-Item -LiteralPath $root -Recurse -Force
    }
}
