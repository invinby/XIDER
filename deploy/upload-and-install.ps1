[CmdletBinding()]
param(
    [string]$ServerIp = '141.145.152.174',
    [string]$KeyPath,
    [string]$KnownHostsPath,
    [string]$SigningKeyPath,
    [switch]$RollbackOnly,
    [string]$BackupPath
)

$ErrorActionPreference = 'Stop'
if (-not $KeyPath) {
    $KeyPath = if ($env:XIDER_SSH_KEY) { $env:XIDER_SSH_KEY } else { Join-Path $env:USERPROFILE '.ssh\xider' }
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$bundle = Join-Path $env:TEMP ("xider-source-{0}.zip" -f ([guid]::NewGuid().ToString('N')))
$manifest = Join-Path $env:TEMP ("xider-manifest-{0}.json" -f ([guid]::NewGuid().ToString('N')))
$stage = Join-Path $env:TEMP ("xider-stage-{0}" -f ([guid]::NewGuid().ToString('N')))
$remoteBundle = "xider-source-$([guid]::NewGuid().ToString('N')).zip"
$remoteManifest = "xider-manifest-$([guid]::NewGuid().ToString('N')).json"
$remoteUpdater = "xider-update-$([guid]::NewGuid().ToString('N')).sh"
$remoteExtractor = "xider-extract-$([guid]::NewGuid().ToString('N')).py"
$remoteVerifier = "xider-verify-$([guid]::NewGuid().ToString('N')).py"
$remoteSignature = "xider-signature-$([guid]::NewGuid().ToString('N')).py"
$remoteOps = "xider-ops-$([guid]::NewGuid().ToString('N')).sh"
if (-not $KnownHostsPath) {
    $KnownHostsPath = if ($env:XIDER_SSH_KNOWN_HOSTS) {
        $env:XIDER_SSH_KNOWN_HOSTS
    } else {
        Join-Path $env:USERPROFILE '.ssh\known_hosts'
    }
}
$sshOpts = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', "UserKnownHostsFile=$KnownHostsPath", '-o', 'IdentitiesOnly=yes', '-i', $KeyPath)

function Invoke-XiderRemoteCommand {
    param(
        [string[]]$Options,
        [string]$Destination,
        [string]$RemoteCommand
    )
    # PowerShell 5 maps native stderr to ErrorRecords. systemd can emit a
    # harmless daemon-reload warning during a successful restart; capture it
    # without letting $ErrorActionPreference='Stop' abort the local workflow.
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $capturedOutput = @(& ssh @Options $Destination $RemoteCommand 2>&1)
        $capturedExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedErrorActionPreference
    }
    return [pscustomobject]@{
        Output = $capturedOutput
        ExitCode = $capturedExitCode
    }
}

try {
    if (-not (Test-Path -LiteralPath $KeyPath)) { throw "SSH key not found: $KeyPath" }
    if (-not (Test-Path -LiteralPath $KnownHostsPath -PathType Leaf)) {
        throw "Pinned SSH known_hosts file not found: $KnownHostsPath. Verify the VPS host fingerprint first; the updater will not trust a first-seen host automatically."
    }

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

    if (-not $SigningKeyPath) {
        $SigningKeyPath = if ($env:XIDER_RELEASE_SIGNING_KEY) {
            $env:XIDER_RELEASE_SIGNING_KEY
        } else {
            Join-Path $env:LOCALAPPDATA 'XIDER\release-signing\release-signing-key.pem'
        }
    }
    if (-not (Test-Path -LiteralPath $SigningKeyPath -PathType Leaf)) {
        throw "Release signing key not found at the protected local key path. Create/provision it once; server update is not signed or uploaded."
    }
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Python launcher `py` is required to sign the server bundle.'
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
        'XGENT-MCS\release_signature.py',
        'XGENT-MCS\update_package.py',
        'deploy\update-server.sh',
        'deploy\safe_extract.py',
        'deploy\verify_server_bundle.py',
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

    $releaseLabel = "local-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))"
    & py -3.12 (Join-Path $repo 'tools\sign_server_bundle.py') `
        --bundle $bundle --manifest $manifest --key $SigningKeyPath --release $releaseLabel
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
        throw 'Could not create a signed release manifest; no server files were uploaded.'
    }

    Write-Host "[1/3] Checking SSH access to $ServerIp..."
    & ssh @sshOpts "ubuntu@$ServerIp" 'echo XIDER_SSH_OK' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'SSH check failed. Fix the key ACL and retry; the server was not changed.' }

    Write-Host '[2/3] Uploading source only; the live server .env is preserved...'
    & scp @sshOpts -- $bundle "ubuntu@${ServerIp}:/tmp/$remoteBundle"
    if ($LASTEXITCODE -ne 0) { throw 'Source upload failed; the server was not changed.' }
    & scp @sshOpts -- $manifest "ubuntu@${ServerIp}:/tmp/$remoteManifest"
    if ($LASTEXITCODE -ne 0) { throw 'Signed release manifest upload failed; the server was not changed.' }
    $bootstrapHelpers = @(
        @{ Local = 'deploy\update-server.sh'; Remote = $remoteUpdater },
        @{ Local = 'deploy\safe_extract.py'; Remote = $remoteExtractor },
        @{ Local = 'deploy\verify_server_bundle.py'; Remote = $remoteVerifier },
        @{ Local = 'XGENT-MCS\release_signature.py'; Remote = $remoteSignature },
        @{ Local = 'deploy\xider-server-ops.sh'; Remote = $remoteOps }
    )
    foreach ($helper in $bootstrapHelpers) {
        & scp @sshOpts -- (Join-Path $repo $helper.Local) "ubuntu@${ServerIp}:/tmp/$($helper.Remote)"
        if ($LASTEXITCODE -ne 0) { throw 'Root updater bootstrap files could not be uploaded; the bot service was not changed.' }
    }

    Write-Host '[3/3] Running the server backup / update / health-check / rollback workflow...'
    $remote = @'
set -eu
bundle="/tmp/__REMOTE_BUNDLE__"
manifest="/tmp/__REMOTE_MANIFEST__"
updater="/tmp/__REMOTE_UPDATER__"
extractor="/tmp/__REMOTE_EXTRACTOR__"
verifier="/tmp/__REMOTE_VERIFIER__"
signature="/tmp/__REMOTE_SIGNATURE__"
ops="/tmp/__REMOTE_OPS__"
incoming='/opt/xider/incoming/xider-source.zip'
incoming_manifest='/opt/xider/incoming/release-manifest.json'
trap 'sudo rm -f "$bundle" "$manifest" "$updater" "$extractor" "$verifier" "$signature" "$ops"' EXIT
sudo test -r /etc/xider/bot.env
sudo systemctl is-enabled --quiet xider-bot.service
sudo systemctl is-active --quiet xider-bot.service
sudo python3 -c 'import cryptography.hazmat.primitives.asymmetric.ed25519' || {
  echo 'System Python is missing python3-cryptography; run the signed server installer first.' >&2
  exit 4
}
# Git for Windows can deliver CRLF shell helpers over SCP. Normalize the two
# executable helpers before install so Linux does not look for "bash\r".
sudo sed -i 's/\r$//' "$updater" "$ops"
sudo bash -n "$updater"
sudo bash -n "$ops"
sudo install -d -o root -g root -m 0750 /usr/local/libexec/xider
sudo install -o root -g root -m 0750 "$updater" /usr/local/libexec/xider/update-server.sh
sudo install -o root -g root -m 0640 "$extractor" /usr/local/libexec/xider/safe_extract.py
sudo install -o root -g root -m 0640 "$verifier" /usr/local/libexec/xider/verify_server_bundle.py
sudo install -o root -g root -m 0640 "$signature" /usr/local/libexec/xider/release_signature.py
sudo install -o root -g root -m 0755 "$ops" /usr/local/sbin/xider-server-ops
sudo visudo -cf /etc/sudoers.d/xider-server-ops
sudo install -d -o root -g xider -m 0750 /opt/xider/incoming
sudo chown root:xider /opt/xider/incoming
sudo chmod 0750 /opt/xider/incoming
sudo install -o root -g xider -m 0640 "$bundle" "$incoming"
sudo install -o root -g xider -m 0640 "$manifest" "$incoming_manifest"
sudo /usr/local/sbin/xider-server-ops update
'@
    $remote = $remote.Replace("`r`n", "`n").Replace("`r", "`n")
    $remote = $remote.Replace('__REMOTE_BUNDLE__', $remoteBundle)
    $remote = $remote.Replace('__REMOTE_MANIFEST__', $remoteManifest)
    $remote = $remote.Replace('__REMOTE_UPDATER__', $remoteUpdater)
    $remote = $remote.Replace('__REMOTE_EXTRACTOR__', $remoteExtractor)
    $remote = $remote.Replace('__REMOTE_VERIFIER__', $remoteVerifier)
    $remote = $remote.Replace('__REMOTE_SIGNATURE__', $remoteSignature)
    $remote = $remote.Replace('__REMOTE_OPS__', $remoteOps)
    $updateResult = Invoke-XiderRemoteCommand -Options $sshOpts -Destination "ubuntu@$ServerIp" -RemoteCommand $remote
    $updateOutput = @($updateResult.Output)
    $updateExit = [int]$updateResult.ExitCode
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
    Remove-Item -LiteralPath $manifest -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
