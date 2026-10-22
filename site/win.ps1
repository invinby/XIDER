$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Write-Host 'XIDER: downloading the latest signed Windows installer...'
$d=Join-Path $env:TEMP ('xider-launch-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $d|Out-Null
try {
  $p=Join-Path $d 'install.ps1'; $s=Join-Path $d 'install.sig'; $a=Join-Path $d 'allowed_signers'; $m=Join-Path $d 'message'
  iwr -UseBasicParsing -TimeoutSec 90 -Uri 'https://github.com/invinby/XIDER/releases/latest/download/XIDER-install-windows.ps1' -OutFile $p
  iwr -UseBasicParsing -TimeoutSec 90 -Uri 'https://github.com/invinby/XIDER/releases/latest/download/XIDER-install-windows.ps1.sig' -OutFile $s
  [IO.File]::WriteAllBytes($a,[Convert]::FromBase64String('eGlkZXItcmVsZWFzZSBzc2gtZWQyNTUxOSBBQUFBQzNOemFDMWxaREkxTlRFNUFBQUFJR2NCSmNFZXpSRXNhSzJHeks5UFFGbExNSytobVBwV2M5UmdSRzY1SFhYQwo='))
  $f=[IO.File]::OpenRead($p); $sha=[Security.Cryptography.SHA256]::Create()
  try{$hash=[BitConverter]::ToString($sha.ComputeHash($f)).Replace('-','').ToLowerInvariant()} finally{$f.Dispose();$sha.Dispose()}
  $ssh=(Get-Command ssh-keygen.exe -ErrorAction Stop).Source
  [IO.File]::WriteAllBytes($m,[Text.Encoding]::ASCII.GetBytes("XIDER-INSTALLER-SHA256`nwindows`n"+$hash+"`n"))
  $c=Join-Path $d 'verify.cmd'
  $verify='@echo off'+[Environment]::NewLine+'pushd "%~dp0"'+[Environment]::NewLine+'"'+$ssh+'" -Y verify -f "allowed_signers" -I xider-release -n xider-installer@xider.link -s "install.sig" < "message"'+[Environment]::NewLine+'exit /b %errorlevel%'
  [IO.File]::WriteAllText($c,$verify,[Text.Encoding]::ASCII)
  $psi=New-Object Diagnostics.ProcessStartInfo; $psi.FileName=$env:ComSpec; $psi.Arguments='/d /s /c ""'+$c+'""'; $psi.UseShellExecute=$false
  $v=New-Object Diagnostics.Process; $v.StartInfo=$psi; [void]$v.Start(); $v.WaitForExit()
  if($v.ExitCode -ne 0){throw 'XIDER installer signature verification failed'}
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p
  if($LASTEXITCODE -ne 0){throw 'XIDER Windows installer failed'}
} finally { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
