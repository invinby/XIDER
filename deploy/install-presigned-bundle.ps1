[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$ServerAlias = 'xider'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
foreach ($path in @($BundlePath, $ManifestPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Release file not found: $path" }
}

$id = [guid]::NewGuid().ToString('N')
$remoteFiles = @{
    Bundle = "/tmp/svchost-source-$id.zip"
    Manifest = "/tmp/svchost-manifest-$id.json"
    Updater = "/tmp/svchost-update-$id.sh"
    Extractor = "/tmp/svchost-extract-$id.py"
    Verifier = "/tmp/svchost-verify-$id.py"
    Signature = "/tmp/svchost-signature-$id.py"
    Ops = "/tmp/svchost-ops-$id.sh"
}

Write-Host "[1/3] Checking SSH connection to $ServerAlias..."
& ssh $ServerAlias 'echo XIDER_SSH_OK'
if ($LASTEXITCODE -ne 0) { throw 'SSH check failed; VPS was not changed.' }

Write-Host '[2/3] Uploading the already signed source bundle and release verifier...'
& scp -- $BundlePath "${ServerAlias}:$($remoteFiles.Bundle)"
if ($LASTEXITCODE -ne 0) { throw 'Source upload failed; VPS was not changed.' }
& scp -- $ManifestPath "${ServerAlias}:$($remoteFiles.Manifest)"
if ($LASTEXITCODE -ne 0) { throw 'Manifest upload failed; VPS was not changed.' }
$helpers = @(
    @{ Local = 'deploy\update-server.sh'; Remote = $remoteFiles.Updater },
    @{ Local = 'deploy\safe_extract.py'; Remote = $remoteFiles.Extractor },
    @{ Local = 'deploy\verify_server_bundle.py'; Remote = $remoteFiles.Verifier },
    @{ Local = 'SvcHostMac\release_signature.py'; Remote = $remoteFiles.Signature },
    @{ Local = 'deploy\svchost-server-ops.sh'; Remote = $remoteFiles.Ops }
)
foreach ($helper in $helpers) {
    & scp -- (Join-Path $repo $helper.Local) "${ServerAlias}:$($helper.Remote)"
    if ($LASTEXITCODE -ne 0) { throw 'Could not upload signed updater helpers; live service was not changed.' }
}

Write-Host '[3/3] Verifying on the VPS, making a backup, applying the update, and checking service health...'
$remote = @'
set -eu
bundle='__BUNDLE__'; manifest='__MANIFEST__'; updater='__UPDATER__'; extractor='__EXTRACTOR__'; verifier='__VERIFIER__'; signature='__SIGNATURE__'; ops='__OPS__'
trap 'sudo rm -f "$bundle" "$manifest" "$updater" "$extractor" "$verifier" "$signature" "$ops"' EXIT
sudo test -r /etc/xider/bot.env
sudo systemctl is-enabled --quiet svchost-bot.service
sudo systemctl is-active --quiet svchost-bot.service
sudo python3 -c 'import cryptography.hazmat.primitives.asymmetric.ed25519'
sudo sed -i 's/\r$//' "$updater" "$ops"
sudo bash -n "$updater"; sudo bash -n "$ops"
sudo install -d -o root -g root -m 0750 /usr/local/libexec/xider
sudo install -o root -g root -m 0750 "$updater" /usr/local/libexec/xider/update-server.sh
sudo install -o root -g root -m 0640 "$extractor" /usr/local/libexec/xider/safe_extract.py
sudo install -o root -g root -m 0640 "$verifier" /usr/local/libexec/xider/verify_server_bundle.py
sudo install -o root -g root -m 0640 "$signature" /usr/local/libexec/xider/release_signature.py
sudo install -o root -g root -m 0755 "$ops" /usr/local/sbin/svchost-server-ops
sudo visudo -cf /etc/sudoers.d/svchost-server-ops
sudo install -d -o root -g xider -m 0750 /opt/xider/incoming
sudo chown root:xider /opt/xider/incoming; sudo chmod 0750 /opt/xider/incoming
sudo install -o root -g xider -m 0640 "$bundle" /opt/xider/incoming/svchost-source.zip
sudo install -o root -g xider -m 0640 "$manifest" /opt/xider/incoming/release-manifest.json
sudo /usr/local/sbin/svchost-server-ops update
'@
foreach ($pair in $remoteFiles.GetEnumerator()) {
    $remote = $remote.Replace("__$($pair.Key.ToUpperInvariant())__", $pair.Value)
}
$remote = $remote.Replace("`r`n", "`n").Replace("`r", "`n")
$result = @(& ssh $ServerAlias $remote 2>&1)
$exitCode = $LASTEXITCODE
$result | Out-Host
if ($exitCode -ne 0) { throw 'Signed VPS update failed; the server updater should have restored its backup. Check journalctl -u svchost-bot.' }
Write-Host '[OK] Signed release deployed. VPS updater reported the backup path above.'
Write-Host "Logs: ssh $ServerAlias 'sudo journalctl -u svchost-bot -f'"
