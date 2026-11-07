$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
function Get-XiderSha256Hex { param([Parameter(Mandatory)][string]$Path); $stream=[IO.File]::OpenRead($Path); $algorithm=[Security.Cryptography.SHA256]::Create(); try { [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() } finally { $stream.Dispose(); $algorithm.Dispose() } }
$d=Join-Path $env:TEMP ('xider-launch-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $d|Out-Null
try {
  $p=Join-Path $d 'install.ps1'; $s=Join-Path $d 'install.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'; $manifestPath=Join-Path $d 'release-manifest.json'
  Write-Host 'XIDER: checking the release and signature verifier...'
  $sshCommand=Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue
  if(-not $sshCommand){$sshPath=Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'; if(Test-Path -LiteralPath $sshPath -PathType Leaf){$sshCommand=[pscustomobject]@{Source=$sshPath}}}
  $tag=$env:XIDER_INSTALL_TAG
  if($tag -and $tag -notmatch '^v\d{1,6}\.\d{1,6}\.\d{1,6}$'){throw 'XIDER release tag is invalid.'}
  $installerBase='https://github.com/invinby/XIDER/releases/latest/download'
  $repo='invinby/XIDER'; $apiRoot='https://api.github.com/repos/'+$repo; $headers=@{Accept='application/vnd.github+json';'User-Agent'='XIDER-Windows-Installer'}
  if(-not $tag -and $installerBase -match '/releases/download/(v\d+\.\d+\.\d+)$'){$tag=$Matches[1]}
  if(-not $tag){$release=Invoke-RestMethod -UseBasicParsing -TimeoutSec 30 -Headers $headers -Uri ($apiRoot+'/releases/latest'); $tag=[string]$release.tag_name}
  if($tag -notmatch '^v\d{1,6}\.\d{1,6}\.\d{1,6}$'){throw 'GitHub returned an invalid XIDER release tag.'}
  $release=Invoke-RestMethod -UseBasicParsing -TimeoutSec 30 -Headers $headers -Uri ($apiRoot+'/releases/tags/'+$tag)
  if($release.draft -or [string]$release.tag_name -cne $tag){throw 'GitHub release tag does not match the selected XIDER version.'}
  $installerBase='https://github.com/'+$repo+'/releases/download/'+$tag
  Write-Host ('XIDER: downloading XIDER '+$tag+' from '+$repo+'...')
  Invoke-WebRequest -UseBasicParsing -TimeoutSec 90 -Uri "$installerBase/XIDER-install-windows.ps1" -OutFile $p
  $hash=Get-XiderSha256Hex -Path $p
  if($sshCommand){
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 90 -Uri "$installerBase/XIDER-install-windows.ps1.sig" -OutFile $s
    [IO.File]::WriteAllBytes($a,[Convert]::FromBase64String('eGlkZXItcmVsZWFzZSBzc2gtZWQyNTUxOSBBQUFBQzNOemFDMWxaREkxTlRFNUFBQUFJR2NCSmNFZXpSRXNhSzJHeks5UFFGbExNSytobVBwV2M5UmdSRzY1SFhYQwo='))
    [IO.File]::WriteAllBytes($m,[Text.Encoding]::ASCII.GetBytes("XIDER-INSTALLER-SHA256`nwindows`n"+$hash+"`n"))
    $ssh=$sshCommand.Source; $c=Join-Path $d 'verify.cmd'
    $verify='@echo off'+[Environment]::NewLine+'pushd "%~dp0"'+[Environment]::NewLine+'"'+$ssh+'" -Y verify -f "allowed_signers" -I xider-release -n xider-installer@xider.link -s "install.sig" < "message"'+[Environment]::NewLine+'exit /b %errorlevel%'
    [IO.File]::WriteAllText($c,$verify,[Text.Encoding]::ASCII)
    $psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$env:ComSpec; $psi.Arguments='/d /s /c ""'+$c+'""'; $psi.UseShellExecute=$false
    $v=New-Object Diagnostics.Process; $v.StartInfo=$psi; [void]$v.Start(); $v.WaitForExit(); if($v.ExitCode -ne 0){throw 'XIDER installer signature verification failed'}
    Write-Host 'XIDER: release signature verified.'
  } else {
    Write-Host 'XIDER: OpenSSH is absent; using GitHub HTTPS metadata and release-manifest SHA-256 checks (detached signature is not verified).' -ForegroundColor Yellow
    $manifestAsset=@($release.assets | Where-Object { $_.name -ceq 'release-manifest.json' }); $installerAsset=@($release.assets | Where-Object { $_.name -ceq 'XIDER-install-windows.ps1' })
    if($manifestAsset.Count -ne 1 -or $installerAsset.Count -ne 1 -or $manifestAsset[0].digest -notmatch '^sha256:[0-9a-fA-F]{64}$' -or $installerAsset[0].digest -notmatch '^sha256:[0-9a-fA-F]{64}$'){throw 'GitHub release metadata has no unique SHA-256 digest for the installer and manifest.'}
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 90 -Uri ($installerBase+'/release-manifest.json') -OutFile $manifestPath
    $manifestHash=Get-XiderSha256Hex -Path $manifestPath; $expectedManifestHash=([string]$manifestAsset[0].digest).Substring(7).ToLowerInvariant(); if($manifestHash -cne $expectedManifestHash){throw ('Release manifest SHA-256 mismatch (actual '+$manifestHash+', expected '+$expectedManifestHash+').')}
    $manifest=Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json; if($manifest.schema -ne 1 -or [string]$manifest.release -cne $tag -or -not $manifest.assets -or $manifest.signature.algorithm -cne 'ed25519' -or -not $manifest.signature.key_id -or -not $manifest.signature.value){throw 'Release manifest schema, signature envelope, or release tag is invalid.'}
    $entries=@($manifest.assets | Where-Object { $_.name -ceq 'XIDER-install-windows.ps1' -and $_.component -ceq 'installer' -and $_.platform -ceq 'windows' }); if($entries.Count -ne 1){throw 'Release manifest has no unique Windows installer entry.'}
    $actualSize=(Get-Item -LiteralPath $p).Length; $manifestDigest=([string]$entries[0].sha256).ToLowerInvariant(); $apiDigest=([string]$installerAsset[0].digest).Substring(7).ToLowerInvariant()
    if($manifestDigest -notmatch '^[0-9a-f]{64}$' -or $hash -cne $manifestDigest -or $hash -cne $apiDigest -or [long]$entries[0].size -ne $actualSize -or [long]$installerAsset[0].size -ne $actualSize){throw 'Installer failed release-manifest or SHA-256 validation.'}
  }
  $logRoot=Join-Path $env:LOCALAPPDATA 'XIDER\logs'; New-Item -ItemType Directory -Path $logRoot -Force|Out-Null
  Get-ChildItem -LiteralPath $logRoot -File -Filter 'install-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -Skip 9 | ForEach-Object { if($_.Name -match '^install-\d{8}-\d{6}\.log$' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)){Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue} }
  $log=Join-Path $logRoot ('install-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
  $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()); $isAdmin=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$p,'-LogPath',$log)
  if(-not $isAdmin){Write-Host 'XIDER: requesting administrator approval (UAC)...'}
  if($isAdmin){& powershell.exe @args; $installerExitCode=$LASTEXITCODE} else {
    $argumentLine='-NoProfile -ExecutionPolicy Bypass -File "{0}" -LogPath "{1}"' -f $p.Replace('"','""'),$log.Replace('"','""')
    $installerProcess=Start-Process -FilePath (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') -ArgumentList $argumentLine -Verb RunAs -Wait -PassThru; $installerExitCode=$installerProcess.ExitCode
  }
  if($installerExitCode -ne 0){ Write-Host ('XIDER install log: '+$log) -ForegroundColor Yellow; if(Test-Path -LiteralPath $log){Get-Content -LiteralPath $log -Tail 80}; throw ('XIDER Windows installer failed with exit code '+$installerExitCode) }
} catch { Write-Host ('XIDER launcher failed: '+$_.Exception.Message) -ForegroundColor Red; throw } finally { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
