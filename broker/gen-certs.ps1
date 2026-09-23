# Generates TLS certificates for the XIDER broker + client CA trust.
# Run ONCE from the broker folder (needs docker for container certs gen
# or openssl installed locally).
# Usage:  powershell -ExecutionPolicy Bypass -File .\gen-certs.ps1 [-ServerDns my-vps.example.com]

param(
    [string]$ServerDns = "localhost"
)
$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path certs | Out-Null

Write-Host "==> 1/4 CA key + cert"
& openssl genrsa -out certs\ca.key 4096
& openssl req -x509 -new -nodes -key certs\ca.key -sha256 -days 3650 `
    -out certs\ca.crt -subj "/CN=XIDER-CA"

Write-Host "==> 2/4 Server key + cert (DNS: $ServerDns)"
& openssl genrsa -out certs\server.key 4096
@"
subjectAltName=DNS:$ServerDns,IP:127.0.0.1
"@ | Set-Content certs\server.ext
& openssl req -new -key certs\server.key -out certs\server.csr -subj "/CN=$ServerDns"
& openssl x509 -req -in certs\server.csr -CA certs\ca.crt -CAkey certs\ca.key `
    -CAcreateserial -out certs\server.crt -days 3650 -sha256 -extfile certs\server.ext

Write-Host "==> 3/4 Cleanup"
Remove-Item certs\server.csr, certs\server.ext, certs\ca.srl -ErrorAction SilentlyContinue

Write-Host "==> 4/4 Broker user password"
Write-Host "Create broker password with:"
Write-Host "  docker run --rm -it -v ${PWD}/passwd:/passwd eclipse-mosquitto:2 mosquitto_passwd -c /passwd xider"
Write-Host ""
Write-Host "DONE. certs\ca.crt -> copy to each machine (client CA)."
Write-Host "IMPORTANT: keep certs\server.key and certs\ca.key SECRET."