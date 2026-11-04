$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$d=Join-Path $env:TEMP ('xider-launch-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $d|Out-Null
try {
  $p=Join-Path $d 'install.ps1'; $s=Join-Path $d 'install.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'
  Write-Host 'XIDER: checking the signature verifier...'
  $sshCommand=Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue
  if(-not $sshCommand){$sshPath=Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'; if(Test-Path -LiteralPath $sshPath -PathType Leaf){$sshCommand=[pscustomobject]@{Source=$sshPath}}}
  if(-not $sshCommand){throw 'OpenSSH Client (ssh-keygen.exe) is required to verify the signed XIDER installer. Enable it in Windows Optional Features, then rerun the same command.'}
  $ssh=$sshCommand.Source
  $tag=$env:XIDER_INSTALL_TAG
  if($tag -and $tag -notmatch '^v\d{1,6}\.\d{1,6}\.\d{1,6}$'){throw 'XIDER release tag is invalid.'}
  $installerBase='https://github.com/invinby/XIDER/releases/download/v4.3.29'
  if($tag){$installerBase="https://github.com/invinby/XIDER/releases/download/$tag"}
  Write-Host 'XIDER: downloading the selected signed installer...'
  iwr -UseBasicParsing -TimeoutSec 90 -Uri "$installerBase/XIDER-install-windows.ps1" -OutFile $p
  iwr -UseBasicParsing -TimeoutSec 90 -Uri "$installerBase/XIDER-install-windows.ps1.sig" -OutFile $s
  [IO.File]::WriteAllBytes($a,[Convert]::FromBase64String('eGlkZXItcmVsZWFzZSBzc2gtZWQyNTUxOSBBQUFBQzNOemFDMWxaREkxTlRFNUFBQUFJR2NCSmNFZXpSRXNhSzJHeks5UFFGbExNSytobVBwV2M5UmdSRzY1SFhYQwo='))
  $f=[IO.File]::OpenRead($p); $sha=[Security.Cryptography.SHA256]::Create()
  try{$hash=[BitConverter]::ToString($sha.ComputeHash($f)).Replace('-','').ToLowerInvariant()} finally{$f.Dispose();$sha.Dispose()}
  [IO.File]::WriteAllBytes($m,[Text.Encoding]::ASCII.GetBytes("XIDER-INSTALLER-SHA256`nwindows`n"+$hash+"`n"))
  $c=Join-Path $d 'verify.cmd'
  $verify='@echo off'+[Environment]::NewLine+'pushd "%~dp0"'+[Environment]::NewLine+'"'+$ssh+'" -Y verify -f "allowed_signers" -I xider-release -n xider-installer@xider.link -s "install.sig" < "message"'+[Environment]::NewLine+'exit /b %errorlevel%'
  [IO.File]::WriteAllText($c,$verify,[Text.Encoding]::ASCII)
  $psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$env:ComSpec; $psi.Arguments='/d /s /c ""'+$c+'""'; $psi.UseShellExecute=$false
  $v=New-Object Diagnostics.Process; $v.StartInfo=$psi; [void]$v.Start(); $v.WaitForExit()
  if($v.ExitCode -ne 0){throw 'XIDER installer signature verification failed'}
  $logRoot=Join-Path $env:LOCALAPPDATA 'XIDER\logs'; New-Item -ItemType Directory -Path $logRoot -Force|Out-Null
  Get-ChildItem -LiteralPath $logRoot -File -Filter 'install-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -Skip 9 | ForEach-Object { if($_.Name -match '^install-\d{8}-\d{6}\.log$' -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)){Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue} }
  $log=Join-Path $logRoot ('install-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
  $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$p,'-LogPath',$log)
  & powershell.exe @args
  $installerExitCode=$LASTEXITCODE
  if($installerExitCode -ne 0){ Write-Host ('XIDER install log: '+$log) -ForegroundColor Yellow; if(Test-Path -LiteralPath $log){Get-Content -LiteralPath $log -Tail 80}; throw ('XIDER Windows installer failed with exit code '+$installerExitCode) }
} catch { Write-Host ('XIDER launcher failed: '+$_.Exception.Message) -ForegroundColor Red; throw } finally { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
