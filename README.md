# XIDER · TARPED 4.2.0 — управление устройствами через Telegram

[English](README.md) · [Русский](README.ru.md)

> Source version: 4.2.0. Published installable versions are listed in
> [GitHub Releases](https://github.com/invinby/XIDER/releases); a source version
> is not proof of a successful device installation. / Версия исходников: 4.2.0.
> Опубликованные пакеты — в Releases. План, ограничения и незавершённая приёмка:
> [TARPED](docs/TARPED.md), [подготовка Mac](docs/MAC-REMOTE-UPDATES.md).

## Быстрая установка / Quick installation

После публикации подписанного релиза и GitHub Pages / Requires published signed
release assets and the repository's GitHub Pages endpoint:

macOS:

```sh
curl -fsSL https://invinby.github.io/XIDER/mac | bash
```

Windows PowerShell:

```powershell
irm https://invinby.github.io/XIDER/win.ps1 | iex
```

Оба entrypoint проверяют подпись установщика, затем установщик проверяет
bootstrap, привязанный к точному тегу и commit. Требуются OpenSSH и Python;
при первой установке — доступ к VPS для конфигурации. Разрешения macOS выдаются
на самом устройстве. / Both entry points verify a publisher signature before
running a commit-pinned bootstrap. OS permissions still require local consent.

В Telegram ручная установка доступна владельцу: **Устройства → Установка · одна команда**,
затем выберите Windows или macOS и скопируйте одну строку в терминал устройства.
The same two copy-ready commands are available in the owner's device list in the bot.

<p align="center">
  <img src="XDicon.png" alt="XIDER Logo" width="128" height="128" />
</p>

<p align="center">
  <a href="https://github.com"><img src="https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue?logo=python" alt="Python Versions"></a>
  <a href="https://github.com"><img src="https://img.shields.io/badge/Architecture-Event--Driven%20%2F%20MQTT-brightgreen" alt="Architecture"></a>
  <a href="https://github.com"><img src="https://img.shields.io/badge/Security-Zero--Trust%20%2F%20AES--256--GCM-red" alt="Security"></a>
  <a href="https://github.com"><img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-lightgrey" alt="Platforms"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow" alt="License"></a>
</p>

---

## 📖 Overview

**XIDER** is a high-performance, asynchronous remote endpoint administration and telemetry system. It enables secure, real-time device monitoring, diagnostics, and management through a Telegram Bot interface backed by a hardened, TLS-encrypted MQTT pub/sub message broker.

**Guard Keeper** is the platform supervisor for Windows and macOS. It reports
worker status and can start, stop, restart, or recover the agent from the
owner-only Telegram panel. Version 4.1.0 added signed macOS file recovery,
serialized source updates, and a lock-safe restart; 4.1.1 adds a copy-ready
one-command Windows/macOS installer picker in Telegram. The VPS updater verifies
signed source archives. Version 4.1.5 fixed long-note pagination in X-LEDGER
and moved note preparation off the bot event loop. Version 4.1.6 serializes
long-running device actions per Telegram card so two different buttons cannot
race to overwrite one operation's progress and result.
Version 4.1.7 waits for fresh MQTT confirmations from both Windows Agent and
Guardian before accepting an install; otherwise the staged update rolls back.
It also fixes a macOS status false-negative and bounds geolocation lookup to one
response on both platforms.
Deliberate stop/uninstall remains effective; Guard Keeper does
not bypass local OS controls or access camera, microphone, screen, or location
without the required OS permission.

Designed with a **Zero-Trust** security architecture, XIDER treats the network transport as untrusted: all commands and telemetry are authenticated with **HMAC-SHA256**, protected against replay attacks via **nonce/timestamp deduplication**, and optionally encrypted end-to-end with **AES-256-GCM**.

---

## 🏛 System Architecture

```mermaid
graph TD
    User([Telegram User / Admin]) <-->|Telegram Bot API (HTTPS)| BotServer[TG-BOT-SERVER (aiogram 3)]
    
    subgraph Secure Messaging Mesh
        BotServer <-->|TLS 1.3| Broker[Mosquitto MQTT Broker]
    end

    subgraph Endpoints [Managed Devices]
        Broker <-->|HMAC-SHA256 + AES-256-GCM| WDS[XGENT-WDS<br/>Windows Endpoint Agent]
        Broker <-->|HMAC-SHA256 + AES-256-GCM| MCS[XGENT-MCS<br/>macOS Endpoint Agent]
        Broker <-->|HMAC-SHA256 + AES-256-GCM| Guardian[Guard Keeper<br/>Windows/macOS Supervisor]
    end

    classDef server fill:#2b3a42,stroke:#4f5d75,stroke-width:2px,color:#fff;
    classDef broker fill:#1f4068,stroke:#162447,stroke-width:2px,color:#fff;
    classDef agent fill:#0f4c75,stroke:#3282b8,stroke-width:2px,color:#fff;
    classDef guardian fill:#5b3b8a,stroke:#9b72cf,stroke-width:2px,color:#fff;
    class BotServer server;
    class Broker broker;
    class WDS,MCS agent;
    class Guardian guardian;
```

---

## 🛡️ Security Architecture

Detailed specification is available in [SECURITY.md](SECURITY.md).

* **Zero-Trust Payload Signing**: Every command is signed with HMAC-SHA256 (`SHARED_KEY`). Even if an adversary compromises the broker, commands cannot be forged or tampered with.
* **Anti-Replay Attack Protection**: All messages contain an ISO timestamp and unique cryptographic nonce. Nonces are tracked in a circular cache; messages older than 120s are rejected.
* **End-to-End Payload Encryption**: AES-256-GCM authenticated encryption derived via HKDF-SHA256 prevents traffic snooping on all telemetry and file transfers.
* **MQTT Last Will and Testament (LWT)**: Endpoints register a cryptographically signed LWT message upon connect. If a machine crashes or loses network connectivity, the broker immediately broadcasts its offline status to the bot.
* **Role-Based Access Control (RBAC)**: `OWNER`, `USER`, `GUEST`, and `BLOCKED` roles; users receive per-device and per-action grants. The owner is fixed by `ADMIN_ID` and cannot be demoted or blocked through the user database.
* **Comprehensive Audit Trail**: Security-critical actions are logged with timestamp and user identity in `audit.log`.

---

## ⚡ Feature Matrix

This matrix describes source-declared capability paths, not a successful live
test on every OS version or device. A check mark does not confirm current
hardware permissions, MQTT reachability, or feature reliability. See the
[X-MAP acceptance checklist](docs/TARPED.md#tarped-acceptance-checklist) and
release notes for tested status. Media capture requires an explicit command
from an account authorized for that device/action and any required
operating-system permission.

| Feature Category | Capability | Windows (`XGENT-WDS`) | macOS (`XGENT-MCS`) |
| :--- | :--- | :---: | :---: |
| **Permissioned Media & Privacy** | Screen capture (all monitors) | ✅ | ✅ |
| | Webcam snapshot / video record | ✅ | ✅ |
| | Microphone audio capture | ✅ | ✅ |
| | Text-to-Speech (TTS) broadcast | ✅ | ✅ |
| | Master Volume control & Toggle Mute | ✅ | ✅ |
| **Hardware & Display** | Hardware Display Brightness | ✅ (WMI/VCP) | ✅ (AppleScript/CLI) |
| | Multi-monitor rotation (0/90/180/270°) | ✅ | ⚠️ |
| | Sleep / Display sleep / Night light | ✅ | ✅ |
| **Input & Automation** | Clipboard read / write | ✅ | ✅ |
| | Keystrokes & Hotkeys simulation | ✅ | ✅ |
| | Crazy cursor / Mouse swap prank | ✅ | ⚠️ |
| **System & Files** | Full Telemetry (CPU, RAM, Disks, Battery) | ✅ | ✅ |
| | Process list, termination, app launcher | ✅ | ✅ |
| | File browser & Download / Upload | ✅ | ✅ |
| | Command line (PowerShell / Bash) | ✅ | ✅ |
| | Desktop Wallpaper change & restore | ✅ | ✅ |
| | Wake-on-LAN (Magic Packet) | ✅ | ✅ |
| | Registered supervisor / autostart | ✅ (Task Scheduler) | ✅ (LaunchAgent) |

---

## 🚀 Quickstart Guide

### 1. Requirements
* Python 3.11+
* Docker & Docker Compose (for the MQTT broker, or existing Mosquitto)

### 2. Start the Private MQTT Broker
```bash
cd broker
docker compose up -d
```

### 3. Launch Telegram Bot Server
```bash
cd TG-BOT-SERVER
cp .env.example .env
# Edit .env and supply your BOT_TOKEN and SHARED_KEY
pip install -r requirements.txt
python bot.py
```

### 4. Deploy Endpoint Agents

#### Windows:
```bash
cd XGENT-WDS
cp .env.example .env
# Edit .env with your broker credentials and SHARED_KEY
pip install -r requirements.txt
python xgent_wds.py
# Or compile standalone EXE:
build_exe.bat
```

#### macOS:
```bash
cd XGENT-MCS
cp .env.example .env
# Edit .env with your broker credentials and SHARED_KEY
pip install -r requirements.txt
./start_agent.sh
# Optional visible supervisor and Telegram recovery controls:
./start_guardian.sh
# Or compile standalone binary:
./build_standalone.sh
```

---

## 🔄 CI/CD & Automated Cloud Builds

This repository includes GitHub Actions workflows:
* **CI Suite (`ci.yml`)**: Runs the bot, Windows-agent, macOS-agent, operations, and release-tool tests in isolated pytest processes across Python 3.11 and 3.12. These are source-level tests, not a live Mac/Windows/Telegram/MQTT test.
* **Release Builder (`build-agents.yml`)**: On git release tags (`v*`), automatically builds `XGENT-WDS.exe` on Windows runners and `XGENT-MCS` standalone binary on macOS runners, publishing them directly as release assets.

### Полная локальная проверка / Full local test run

After installing the bot and Windows-agent test dependencies, run the component
suites and the current OS's installer/rollback fixtures without module-name
collisions (`config.py` exists in multiple components):

```powershell
python tools/run_tests.py
```

The runner uses fake credentials and does not connect to Telegram, MQTT, or a
VPS. On Windows it also exercises PowerShell bootstrap/install/rollback fixtures;
on Linux it exercises the shell bootstrap and VPS-updater fixtures. GitHub CI
additionally checks PowerShell 7 and Windows PowerShell 5, plus Linux shell
syntax. These offline fixtures do not replace live Mac/Windows hardware,
Telegram/MQTT, or production VPS validation.

---

## ⚖️ Legal Disclaimer

This software is developed strictly for educational purposes, defensive security research, and authorized personal endpoint management. The authors assume no liability for misuse or damage caused by this software.

---

## Русская версия

Полная русская документация, установка, описание компонентов, выбор релиза и
границы подтверждённых возможностей находятся в [README.ru.md](README.ru.md).
