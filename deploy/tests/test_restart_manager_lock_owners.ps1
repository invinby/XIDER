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
$holderScript = Join-Path $fixture 'hold-open.ps1'
$readyPath = Join-Path $fixture 'holder-ready.txt'
$holder = $null
try {
    [IO.File]::WriteAllBytes($file, [byte[]](1, 2, 3))
    @'
param([string]$Path, [string]$ReadyPath)
$stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
try {
    [IO.File]::WriteAllText($ReadyPath, [string]$PID)
    Start-Sleep -Seconds 30
} finally {
    $stream.Dispose()
}
'@ | Set-Content -LiteralPath $holderScript -Encoding UTF8
    $holder = Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -WindowStyle Hidden -PassThru -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $holderScript, $file, $readyPath
        )
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not (Test-Path -LiteralPath $readyPath -PathType Leaf) -and
        [DateTime]::UtcNow -lt $deadline -and -not $holder.HasExited) {
        Start-Sleep -Milliseconds 100
        $holder.Refresh()
    }
    if (-not (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        throw 'The fixture process did not acquire the test file lock.'
    }
    $holderId = [int](Get-Content -LiteralPath $readyPath -Raw)

    $owners = @(Get-XiderDirectoryLockOwners -DirectoryPath $fixture)
    if (-not ($owners | Where-Object { $_.ProcessId -eq $holderId })) {
        throw "Restart Manager did not report the external PowerShell process holding the fixture file (PID $holderId)."
    }

    Write-Output ("RESTART_MANAGER_FIXTURE=PASS pid={0}" -f $holderId)
} finally {
    if ($holder -and -not $holder.HasExited) {
        Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
        $holder.WaitForExit()
    }
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        Remove-Item -LiteralPath $file -Force
    }
    if (Test-Path -LiteralPath $holderScript -PathType Leaf) {
        Remove-Item -LiteralPath $holderScript -Force
    }
    if (Test-Path -LiteralPath $readyPath -PathType Leaf) {
        Remove-Item -LiteralPath $readyPath -Force
    }
    if (Test-Path -LiteralPath $fixture -PathType Container) {
        Remove-Item -LiteralPath $fixture -Force
    }
}
