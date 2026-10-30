$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$d=Join-Path $env:TEMP ('xider-launch-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $d|Out-Null
try {
  $p=Join-Path $d 'install.ps1'; $s=Join-Path $d 'install.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'
  Write-Host 'XIDER: checking the signature verifier...'
  $sshCommand=Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue
  if(-not $sshCommand){
    $featureScript=Join-Path $d 'install-openssh.ps1'
    $featureBody="`$ErrorActionPreference='Stop'`nAdd-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' | Out-Null`nif((Get-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0').State -ne 'Installed'){exit 1}`n"
    [IO.File]::WriteAllText($featureScript,$featureBody,[Text.UTF8Encoding]::new($false))
    $principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
      & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $featureScript
      if($LASTEXITCODE -ne 0){throw 'Windows could not install the OpenSSH Client optional feature.'}
    } else {
      $feature=Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$featureScript`"")
      if($feature.ExitCode -ne 0){throw 'Windows could not install the OpenSSH Client optional feature.'}
    }
    $sshPath=Join-Path $env:WINDIR 'System32\OpenSSH\ssh-keygen.exe'
    if(Test-Path -LiteralPath $sshPath -PathType Leaf){$sshCommand=[pscustomobject]@{Source=$sshPath}}
    else{$sshCommand=Get-Command ssh-keygen.exe -ErrorAction SilentlyContinue}
    if(-not $sshCommand){throw 'OpenSSH Client is still unavailable. Restart PowerShell and retry the XIDER command.'}
  }
  $ssh=$sshCommand.Source
  $tag=$env:XIDER_INSTALL_TAG
  if($tag -and $tag -notmatch '^v\d{1,6}\.\d{1,6}\.\d{1,6}$'){throw 'XIDER release tag is invalid.'}
  $installerBase='https://github.com/invinby/XIDER/releases/download/v4.3.5'
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
  Write-Host 'XIDER: requesting administrator approval (UAC)...'
  $logRoot=Join-Path $env:LOCALAPPDATA 'XIDER\logs'; New-Item -ItemType Directory -Path $logRoot -Force|Out-Null
  $log=Join-Path $logRoot ('install-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
  $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$p`"",'-LogPath',"`"$log`"")
  $installer=Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $args
  if($installer.ExitCode -ne 0){ Write-Host ('XIDER install log: '+$log) -ForegroundColor Yellow; if(Test-Path -LiteralPath $log){Get-Content -LiteralPath $log -Tail 80}; throw ('XIDER Windows installer failed with exit code '+$installer.ExitCode) }
} catch { Write-Host ('XIDER launcher failed: '+$_.Exception.Message) -ForegroundColor Red; throw } finally { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
