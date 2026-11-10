$ErrorActionPreference = 'Stop'
$bootstrap = (Resolve-Path (Join-Path $PSScriptRoot '..\bootstrap-agent.ps1')).Path
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($bootstrap, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "Bootstrap parse failed: $($parseErrors[0].Message)" }
$functionAsts = @($ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @('Remove-XiderOldPreviousCheckouts', 'Remove-XiderOldFailedCheckouts', 'Remove-XiderOldStageCheckouts')
}, $true))
if ($functionAsts.Count -ne 3) { throw 'Checkout retention helpers are missing.' }
foreach ($functionAst in $functionAsts) { Invoke-Expression $functionAst.Extent.Text }

$root = Join-Path $env:TEMP ('svchost-retention-' + [guid]::NewGuid().ToString('N'))
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
        $agentDir = Join-Path $dir 'SvcHostWin'
        New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $dir 'rollback-marker.txt'), $name, $utf8)
        $envPath = Join-Path $agentDir '.env'
        [IO.File]::WriteAllText($envPath, 'fixture-only', $utf8)
        if ($name -ne $names[0]) {
            # Model old backups where the secret has an explicit read-only ACL
            # while its managed parent directory remains owner-controlled.
            $envAcl = Get-Acl -LiteralPath $envPath
            $envAcl.SetAccessRuleProtection($true, $false)
            $reader = [Security.Principal.WindowsIdentity]::GetCurrent().User
            $readOnly = [Security.AccessControl.FileSystemAccessRule]::new(
                $reader,
                [Security.AccessControl.FileSystemRights]::Read,
                [Security.AccessControl.AccessControlType]::Allow
            )
            $envAcl.SetAccessRule($readOnly)
            Set-Acl -LiteralPath $envPath -AclObject $envAcl
        }
    }
    $unrecognized = Join-Path $root 'git-ver.previous.owner-data'
    New-Item -ItemType Directory -Path $unrecognized -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $unrecognized 'keep.txt'), 'owner data', $utf8)
    $failedNames = @(
        'git-ver.failed.20261001000000.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'git-ver.failed.20261002000000.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
    )
    foreach ($name in $failedNames) {
        New-Item -ItemType Directory -Path (Join-Path $root $name) -Force | Out-Null
    }
    $oldStage = Join-Path $root 'git-ver.stage.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $freshStage = Join-Path $root 'git-ver.stage.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
    $unknownStage = Join-Path $root 'git-ver.stage.owner-data'
    foreach ($path in @($oldStage, $freshStage, $unknownStage)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    [IO.Directory]::SetLastWriteTimeUtc($oldStage, [DateTime]::UtcNow.AddHours(-48))

    Remove-XiderOldPreviousCheckouts -InstallRoot $root -CurrentBackup (Join-Path $root $names[0]) -Keep 1
    Remove-XiderOldFailedCheckouts -InstallRoot $root -Keep 1
    Remove-XiderOldStageCheckouts -InstallRoot $root -MaxAgeHours 24

    $managed = @(Get-ChildItem -LiteralPath $root -Directory -Force |
        Where-Object { $_.Name -match '^git-ver\.previous\.\d{14}\.[0-9a-fA-F]{32}$' } |
        Sort-Object -Property Name -Descending)
    if ($managed.Count -ne 1 -or $managed[0].Name -ne $names[0]) {
        throw "Expected only the current managed rollback checkout, found: $($managed.Name -join ', ')"
    }
    $newestOnlyRoot = Join-Path $root 'newest-only-fixture'
    New-Item -ItemType Directory -Path $newestOnlyRoot -Force | Out-Null
    foreach ($name in @($names[1], $names[2])) {
        New-Item -ItemType Directory -Path (Join-Path $newestOnlyRoot $name) -Force | Out-Null
    }
    Remove-XiderOldPreviousCheckouts -InstallRoot $newestOnlyRoot -CurrentBackup (Join-Path $newestOnlyRoot 'missing-current') -Keep 1
    $newestOnly = @(Get-ChildItem -LiteralPath $newestOnlyRoot -Directory -Force)
    if ($newestOnly.Count -ne 1 -or $newestOnly[0].Name -ne $names[2]) {
        throw 'Retention without a current backup did not keep only the newest managed checkout.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $unrecognized 'keep.txt'))) {
        throw 'An unrecognized sibling directory was modified.'
    }
    $failedRemaining = @(Get-ChildItem -LiteralPath $root -Directory -Force |
        Where-Object { $_.Name -match '^git-ver\.failed\.' })
    if ($failedRemaining.Count -ne 1 -or $failedRemaining[0].Name -ne $failedNames[1]) {
        throw 'Failed-checkout retention did not keep exactly the newest managed diagnostic copy.'
    }
    if (Test-Path -LiteralPath $oldStage) { throw 'An expired generated stage directory was not removed.' }
    if (-not (Test-Path -LiteralPath $freshStage)) { throw 'A recent stage directory was removed.' }
    if (-not (Test-Path -LiteralPath $unknownStage)) { throw 'An unrecognized stage directory was modified.' }
    Write-Host 'Windows checkout retention helper passed.'
}
finally {
    if ((Test-Path -LiteralPath $root) -and
        [IO.Path]::GetFullPath($root).StartsWith([IO.Path]::GetFullPath($env:TEMP), [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path $root -Leaf) -like 'svchost-retention-*') {
        Get-ChildItem -LiteralPath $root -Filter '.env' -File -Force -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object {
                $acl = Get-Acl -LiteralPath $_.FullName
                $acl.SetAccessRuleProtection($false, $true)
                Set-Acl -LiteralPath $_.FullName -AclObject $acl
            }
        Remove-Item -LiteralPath $root -Recurse -Force
    }
}
