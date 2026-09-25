# XIDER private broker (TLS + auth + ACL)
# 1) Generate certs:      powershell -ExecutionPolicy Bypass -File .\gen-certs.ps1 -ServerDns <VPS-DOMAIN-OR-IP>
# 2) Set broker password: docker run --rm -it -v ${PWD}/passwd:/passwd eclipse-mosquitto:2 mosquitto_passwd -c /passwd xider
# 3) Open port:           ufw allow 8883/tcp   (on VPS)
# 4) Start:               docker compose up -d
# 5) In .env of bot AND both agents set:
#      MQTT_BROKER=<your-vps-domain-or-ip>
#      MQTT_PORT=8883
#      MQTT_TLS=true
#      MQTT_USERNAME=xider
#      MQTT_PASSWORD=<the password from step 2>
#      ENCRYPT_PAYLOAD=true
# 6) Restart bot + agents. Done: fully private + encrypted channel.
# 7) Keep `passwd`, `acl` and `certs/` outside Git. `acl` limits the account
#    to `xgent/v1/#`; add separate users/topics before onboarding other tenants.
