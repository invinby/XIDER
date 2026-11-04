$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "Bootstrap parse failed: $($parseErrors[0].Message)" }
$functionAst = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Move-XiderLegacyDesktopCheckoutToRollback'
}, $true)
if (-not $functionAst) { throw 'Legacy Desktop migration helper is missing.' }
. ([scriptblock]::Create($functionAst.Extent.Text))

$fixture = Join-Path $env:TEMP ('xider-legacy-location-' + [guid]::NewGuid().ToString('N'))
$profile = Join-Path $fixture 'profile'
$legacyRoot = Join-Path $profile 'Desktop\XIDER'
$legacyCheckout = Join-Path $legacyRoot 'git-ver'
$installRoot = Join-Path $profile 'AppData\Local\XIDER'
$activeCheckout = Join-Path $installRoot 'git-ver'
$previousProfile = $env:USERPROFILE
try {
    $env:USERPROFILE = $profile
    New-Item -ItemType Directory -Path (Join-Path $legacyCheckout 'XGENT-WDS') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $activeCheckout 'XGENT-WDS') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $legacyCheckout 'XGENT-WDS\xgent_wds.py'), 'old')
    [IO.File]::WriteAllText((Join-Path $activeCheckout 'XGENT-WDS\xgent_wds.py'), 'new')

    $moved = Move-XiderLegacyDesktopCheckoutToRollback -InstallRoot $installRoot
    if (-not $moved -or (Split-Path $moved -Parent) -ne $installRoot -or
        (Split-Path $moved -Leaf) -notmatch '^git-ver\.previous\.\d{14}\.[0-9a-fA-F]{32}$') {
        throw "Legacy checkout was not moved into a managed rollback slot: $moved"
    }
    if (Test-Path -LiteralPath $legacyCheckout) { throw 'The visible legacy checkout still exists after a successful move.' }
    if (Test-Path -LiteralPath $legacyRoot) { throw 'The empty visible legacy XIDER folder was not removed.' }
    if ([IO.File]::ReadAllText((Join-Path $moved 'XGENT-WDS\xgent_wds.py')) -ne 'old' -or
        [IO.File]::ReadAllText((Join-Path $activeCheckout 'XGENT-WDS\xgent_wds.py')) -ne 'new') {
        throw 'Migration changed either the rollback copy or the active install.'
    }

    $legacyRoot = Join-Path $profile 'Desktop\XIDER'
    $legacyComponent = Join-Path $legacyRoot 'XGENT-WDS'
    New-Item -ItemType Directory -Path $legacyComponent -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $legacyComponent 'xgent_wds.py'), 'old-again')
    [IO.File]::WriteAllText((Join-Path $legacyRoot 'owner-notes.txt'), 'keep')
    $movedWithUnknownData = Move-XiderLegacyDesktopCheckoutToRollback -InstallRoot $installRoot
    if (-not $movedWithUnknownData -or (Test-Path -LiteralPath $legacyComponent) -or
        -not (Test-Path -LiteralPath (Join-Path $movedWithUnknownData 'XGENT-WDS\xgent_wds.py')) -or
        -not (Test-Path -LiteralPath (Join-Path $legacyRoot 'owner-notes.txt'))) {
        throw 'Migration failed to preserve unrelated files in the visible legacy folder.'
    }
    Write-Host 'Windows legacy install location fixture passed.'
}
finally {
    if ($previousProfile) { $env:USERPROFILE = $previousProfile }
    if ((Test-Path -LiteralPath $fixture) -and
        $fixture.StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $fixture -Leaf) -like 'xider-legacy-location-*') {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
