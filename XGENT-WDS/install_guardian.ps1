[CmdletBinding()]
param(
    [string]$AgentDir = $PSScriptRoot,
    [string]$AgentDataDir = (Join-Path $env:USERPROFILE '.xgent'),
    [string]$TaskName = 'XIDER Guardian',
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$AgentDir = (Resolve-Path $AgentDir).Path
$envPath = Join-Path $AgentDir '.env'
$guardianExe = Join-Path $AgentDir 'XIDER-Guard-Keeper.exe'
$guardian = Join-Path $AgentDir 'xider_guardian_wds.py'
$pythonw = Join-Path $AgentDir 'venv\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $envPath)) { throw "Missing $envPath" }
$packagedGuardian = Test-Path -LiteralPath $guardianExe -PathType Leaf
if (-not $packagedGuardian) {
    if (-not (Test-Path -LiteralPath $guardian -PathType Leaf)) { throw "Missing $guardian and $guardianExe" }
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        $systemPythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if (-not $systemPythonw) { throw 'A windowless Guardian Python runtime (pythonw.exe) is missing.' }
        $pythonw = $systemPythonw.Source
    }
}
foreach ($commandName in @(
    'New-ScheduledTaskAction', 'New-ScheduledTaskTrigger',
    'New-ScheduledTaskSettingsSet', 'New-ScheduledTaskPrincipal',
    'Get-ScheduledTask', 'Register-ScheduledTask', 'Start-ScheduledTask'
)) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required Windows Scheduled Tasks command is unavailable: $commandName"
    }
}
$guardianAction = if ($packagedGuardian) {
    New-ScheduledTaskAction -Execute $guardianExe -WorkingDirectory $AgentDir
} else {
    # python.exe opens a console window at every Guardian restart. Use the
    # paired windowless runtime so recovery remains silent without hiding the
    # visible task, logs, stop controls, or uninstall path.
    New-ScheduledTaskAction -Execute $pythonw -Argument ('"{0}"' -f $guardian) -WorkingDirectory $AgentDir
}
if ($PreflightOnly) {
    Write-Host '[OK] Packaged Guardian or windowless Python fallback and Task Scheduler commands are present. Nothing changed.'
    return
}

$ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$ownerGrant = '*{0}:(OI)(CI)F' -f $ownerSid
New-Item -ItemType Directory -Path $AgentDataDir -Force | Out-Null
foreach ($protectedPath in @($AgentDir, $AgentDataDir)) {
    icacls $protectedPath /inheritance:r /grant:r $ownerGrant /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed to secure XIDER files and logs under '$protectedPath'." }
}

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$taskAlreadyMatches = $false
if ($existingTask -and $existingTask.State -ne 'Disabled' -and $existingTask.Actions.Count -eq 1 -and $existingTask.Principal) {
    try {
    $expectedTaskPath = [IO.Path]::GetFullPath([string]$guardianAction.Execute)
    $existingTaskPath = [IO.Path]::GetFullPath([string]$existingTask.Actions[0].Execute)
    $expectedWorkingDirectory = [IO.Path]::GetFullPath([string]$AgentDir)
    $existingWorkingDirectory = [IO.Path]::GetFullPath([string]$existingTask.Actions[0].WorkingDirectory)
    $expectedArguments = ([string]$guardianAction.Argument).Trim()
    $existingArguments = ([string]$existingTask.Actions[0].Arguments).Trim()
    $taskUserId = ([string]$existingTask.Principal.UserId).Trim()
    $currentUserId = "$env:USERDOMAIN\$env:USERNAME"
    $sameTaskUser = $false
    if ($taskUserId) {
        try {
            $taskSid = ([Security.Principal.NTAccount]::new($taskUserId)).Translate([Security.Principal.SecurityIdentifier]).Value
            $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            $sameTaskUser = $taskSid -eq $currentSid
        } catch {
            $sameTaskUser = $taskUserId -ieq $currentUserId -or $taskUserId -ieq $env:USERNAME
        }
    }
    $samePrincipal = $sameTaskUser -and
        ([string]$existingTask.Principal.LogonType -in @('Interactive', 'InteractiveToken')) -and
        ([string]$existingTask.Principal.RunLevel -in @('Limited', 'LUA'))
    $taskAlreadyMatches = $samePrincipal -and
        $existingTaskPath.Equals($expectedTaskPath, [StringComparison]::OrdinalIgnoreCase) -and
        $existingWorkingDirectory.Equals($expectedWorkingDirectory, [StringComparison]::OrdinalIgnoreCase) -and
        $existingArguments -ceq $expectedArguments
    } catch {
        $taskAlreadyMatches = $false
    }
}
if ($taskAlreadyMatches) {
    Write-Host "[OK] $TaskName already points to this user and the current Guardian files; keeping its existing task definition."
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $guardianAction -Trigger $trigger -Settings $settings -Principal $principal -Description 'XIDER Windows Guard Keeper supervisor' -Force | Out-Null
}
Start-ScheduledTask -TaskName $TaskName
Write-Host "[OK] $TaskName installed and started."
