[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath = "$env:USERPROFILE\.ssh\xider",
    [switch]$RollbackOnly,
    [string]$BackupPath
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bundle = Join-Path $env:TEMP ("xider-source-{0}.zip" -f ([guid]::NewGuid().ToString('N')))
$stage = Join-Path $env:TEMP ("xider-stage-{0}" -f ([guid]::NewGuid().ToString('N')))
$remoteBundle = "xider-source-$([guid]::NewGuid().ToString('N')).zip"
$remoteUpdater = "xider-update-$([guid]::NewGuid().ToString('N')).sh"
$remoteExtractor = "xider-extract-$([guid]::NewGuid().ToString('N')).py"
$activeUpdater = "xider-update-active-$([guid]::NewGuid().ToString('N')).sh"
$activeExtractor = "xider-extract-active-$([guid]::NewGuid().ToString('N')).py"
$knownHosts = Join-Path $env:TEMP 'xider-known-hosts'
$sshOpts = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', "UserKnownHostsFile=$knownHosts", '-o', 'IdentitiesOnly=yes', '-i', $KeyPath)

try {
    if (-not (Test-Path -LiteralPath $KeyPath)) { throw "SSH key not found: $KeyPath" }

    if ($RollbackOnly) {
        if (-not $BackupPath -or $BackupPath -notmatch '^/var/backups/xider/xider-[A-Za-z0-9._-]+\.tar\.gz$') {
            throw 'RollbackOnly requires a verified /var/backups/xider/xider-*.tar.gz path.'
        }
        Write-Host "[rollback] Restoring the requested server snapshot on $ServerIp..."
        $rollbackCommand = "sudo /usr/local/sbin/xider-server-ops rollback '$BackupPath'"
        & ssh @sshOpts "ubuntu@$ServerIp" $rollbackCommand | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Server rollback failed; inspect the XIDER service and preserved backup.' }
        Write-Host '[OK] Server rollback completed.'
        return
    }

    # Archive the current working tree files. A normal checkout uses git ls-files;
    # the one-line bootstrap downloads a ZIP without .git, so fall back to a
    # recursive file list in that case. Never package live secrets or keys.
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $tracked = @()
    if (Test-Path -LiteralPath (Join-Path $repo '.git')) {
        $tracked = @(
            & git -c safe.directory=$repo -C $repo ls-files |
                Where-Object {
                    $_ -notmatch '(^|[\\/])\.env$' -and
                    $_ -notmatch '(^|[\\/])\.env\.' -and
                    $_ -notmatch '(^|[\\/])_secrets_embed\.py$' -and
                    $_ -notmatch '\.(key|pem|p12|pfx|crt)$'
                }
        )
    }
    if ($tracked.Count -eq 0) {
        $repoPrefix = $repo.TrimEnd('\') + '\'
        $tracked = @(
            Get-ChildItem -LiteralPath $repo -File -Recurse -Force |
                Where-Object {
                    $_.FullName -notmatch '\\.git\\' -and
                    $_.FullName -notmatch '\\(?:venv|\.venv|__pycache__|node_modules)\\' -and
                    $_.Name -notin @('.env', '_secrets_embed.py') -and
                    $_.Name -notmatch '^\.env\.' -and
                    $_.Extension -notin @('.key', '.pem', '.p12', '.pfx', '.crt')
                } |
                ForEach-Object { $_.FullName.Substring($repoPrefix.Length) }
        )
    }
    if ($tracked.Count -eq 0) { throw 'Could not enumerate source files.' }
    foreach ($relative in $tracked) {
        $source = Join-Path $repo $relative
        $target = Join-Path $stage $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
    # launcher.py is kept local and may be untracked in older checkouts.
    $launcher = Join-Path $repo 'TG-BOT-SERVER\launcher.py'
    if (Test-Path -LiteralPath $launcher) {
        Copy-Item -LiteralPath $launcher -Destination (Join-Path $stage 'TG-BOT-SERVER\launcher.py') -Force
    }
    # Keep operational files that may intentionally remain untracked while a
    # release is being prepared. Never include .env or private keys here.
    $extraFiles = @(
        'TG-BOT-SERVER\info_book.py',
        'TG-BOT-SERVER\release_catalog.py',
        'TG-BOT-SERVER\server_ops.py',
        'TG-BOT-SERVER\text_store.py',
        'TG-BOT-SERVER\ui_cards.py',
        'TG-BOT-SERVER\xlex.py',
        'deploy\update-server.sh',
        'deploy\safe_extract.py',
        'deploy\rotate-runtime-secrets.sh',
        'deploy\bootstrap.ps1',
        'deploy\bootstrap.sh',
        'broker\acl'
    )
    foreach ($relative in $extraFiles) {
        $source = Join-Path $repo $relative
        if (Test-Path -LiteralPath $source) {
            $target = Join-Path $stage $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $source -Destination $target -Force
        }
    }
    & tar.exe -a -c -f $bundle -C $stage .
    if ($LASTEXITCODE -ne 0) { throw 'Could not create a portable source archive.' }

    Write-Host "[1/3] Checking SSH access to $ServerIp..."
    & ssh @sshOpts "ubuntu@$ServerIp" 'echo XIDER_SSH_OK' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'SSH check failed. Fix the key ACL and retry; the server was not changed.' }

    Write-Host '[2/3] Uploading source only; the live server .env is preserved...'
    & scp @sshOpts -- $bundle "ubuntu@${ServerIp}:/tmp/$remoteBundle"
    if ($LASTEXITCODE -ne 0) { throw 'Source upload failed; the server was not changed.' }
    & scp @sshOpts -- (Join-Path $repo 'deploy\update-server.sh') "ubuntu@${ServerIp}:/tmp/$remoteUpdater"
    if ($LASTEXITCODE -ne 0) { throw 'Could not upload the verified updater; the installed service was not changed.' }
    & scp @sshOpts -- (Join-Path $repo 'deploy\safe_extract.py') "ubuntu@${ServerIp}:/tmp/$remoteExtractor"
    if ($LASTEXITCODE -ne 0) { throw 'Could not upload the archive validator; the installed service was not changed.' }

    Write-Host '[3/3] Running the server backup / update / health-check / rollback workflow...'
    $remote = @'
set -eu
bundle="/tmp/__REMOTE_BUNDLE__"
updater="/tmp/__REMOTE_UPDATER__"
extractor="/tmp/__REMOTE_EXTRACTOR__"
active_updater="/tmp/__ACTIVE_UPDATER__"
active_extractor="/tmp/__ACTIVE_EXTRACTOR__"
incoming='/opt/xider/incoming/xider-source.zip'
trap 'sudo rm -f "$bundle" "$updater" "$extractor" "$active_updater" "$active_extractor"' EXIT
sudo test -r /etc/xider/bot.env
sudo systemctl is-enabled --quiet xider-bot.service
sudo systemctl is-active --quiet xider-bot.service
sudo install -d -o root -g xider -m 0750 /opt/xider/incoming
sudo install -o root -g xider -m 0640 "$bundle" "$incoming"
sudo install -o root -g root -m 0700 "$updater" "$active_updater"
sudo install -o root -g root -m 0600 "$extractor" "$active_extractor"
sudo env XIDER_UPDATE_BUNDLE="$incoming" XIDER_EXTRACT_HELPER="$active_extractor" bash "$active_updater" update
'@
    $remote = $remote.Replace("`r`n", "`n").Replace("`r", "`n")
    $remote = $remote.Replace('__REMOTE_BUNDLE__', $remoteBundle)
    $remote = $remote.Replace('__REMOTE_UPDATER__', $remoteUpdater)
    $remote = $remote.Replace('__REMOTE_EXTRACTOR__', $remoteExtractor)
    $remote = $remote.Replace('__ACTIVE_UPDATER__', $activeUpdater)
    $remote = $remote.Replace('__ACTIVE_EXTRACTOR__', $activeExtractor)
    $updateOutput = @(& ssh @sshOpts "ubuntu@$ServerIp" $remote 2>&1)
    $updateExit = $LASTEXITCODE
    $updateOutput | Out-Host
    if ($updateExit -ne 0) { throw 'Safe server update failed; the updater should have restored the previous source. Inspect journalctl -u xider-bot.' }
    $updateText = ($updateOutput | ForEach-Object { [string]$_ }) -join "`n"
    $backupMatch = [regex]::Match($updateText, 'Backup:\s*(?<path>/var/backups/xider/xider-[A-Za-z0-9._-]+\.tar\.gz)')
    if (-not $backupMatch.Success) { throw 'Server update succeeded, but its rollback snapshot path was not reported.' }
    Write-Output ("XIDER_SERVER_BACKUP={0}" -f $backupMatch.Groups['path'].Value)

    Write-Host 'Done. The live .env was not uploaded or modified.'
    Write-Host "Logs: ssh -i `"$KeyPath`" ubuntu@$ServerIp 'sudo journalctl -u xider-bot -f'"
}
finally {
    Remove-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
