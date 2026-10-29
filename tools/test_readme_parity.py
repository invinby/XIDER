"""README parity checks and regression fixtures; run as a script or with pytest."""
from pathlib import Path


def check_readme_pair(english: str, russian: str) -> list[str]:
    """Return parity and format problems for the root bilingual README pair."""
    import re

    errors: list[str] = []
    sections = {
        "purpose": (r"purpose|overview", r"назначение|о проекте"),
        "architecture": (r"architecture", r"архитектура|из чего состоит"),
        "components": (r"components", r"компоненты"),
        "os support": (r"os support|supported operating systems", r"поддержка ос|поддерживаемые ос"),
        "install/update/rollback": (r"install(?:ation)?(?:, update and rollback)?|quickstart guide", r"установка(?:, обновление и откат)?|обновление и выбор версии"),
        "permissions/security": (r"permissions and security|security(?: architecture)?", r"разрешения и безопасность|роли и границы возможностей"),
        "diagnostics": (r"diagnostics|checks and readiness status", r"диагностика|проверки и статус готовности"),
        "limitations": (r"limitations|known limitations", r"ограничения|границы возможностей"),
        "license": (r"license", r"лицензия"),
        "status": (r"status|readiness status", r"статус|статус готовности"),
    }

    def headings(text: str) -> list[str]:
        return [m.group(1).strip().lower() for m in re.finditer(r"(?m)^##\s+(.+?)\s*$", text)]

    en_headings, ru_headings = headings(english), headings(russian)
    for label, (en_alias, ru_alias) in sections.items():
        if not any(re.search(rf"\b(?:{en_alias})\b", h) for h in en_headings):
            errors.append(f"missing required English section: {label}")
        if not any(re.search(rf"(?:{ru_alias})", h) for h in ru_headings):
            errors.append(f"missing required Russian section: {label}")

    fence_counts = []
    mermaid_counts = []
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for language, text in (("English", english), ("Russian", russian)):
        fences = re.findall(r"(?m)^\s*```", text)
        if len(fences) % 2:
            errors.append(f"{language} README has an unmatched code fence")
        fence_counts.append(len(fences))
        mermaid_counts.append(len(re.findall(r"(?im)^\s*```mermaid\s*$", text)))
        for destination in link_pattern.findall(text):
            destination = destination.strip().split()[0].strip("<>")
            if destination.startswith("#"):
                continue
            if re.match(r"(?i)^https?://", destination):
                if not destination.lower().startswith("https://"):
                    errors.append(f"{language} README external links must use HTTPS: {destination}")
                continue
            if re.match(r"(?i)^(mailto:|tel:)", destination):
                continue
            target = Path(__file__).resolve().parents[1] / destination.split("#", 1)[0]
            if not target.is_file():
                errors.append(f"{language} README has a broken local link: {destination}")

    if mermaid_counts[0] == 0 or mermaid_counts[1] == 0:
        errors.append("both READMEs must contain a Mermaid architecture block")
    elif mermaid_counts[0] != mermaid_counts[1]:
        errors.append("README pair has a Mermaid block-count mismatch")
    if fence_counts[0] != fence_counts[1]:
        errors.append("README pair has a code-fence count mismatch")

    # These markers cover a deliberate set of source-backed facts. They are
    # regression anchors, not a proof of full natural-language equivalence.
    semantic_facts = {
        "product and version": (r"(?=.*XIDER)(?=.*4\.3\.[0-9]+)", r"(?=.*XIDER)(?=.*4\.3\.[0-9]+)"),
        "Windows and macOS support": (r"Windows.{0,40}macOS|macOS.{0,40}Windows", r"Windows.{0,40}macOS|macOS.{0,40}Windows"),
        "Telegram interface": (r"Telegram", r"Telegram"),
        "MQTT transport": (r"MQTT", r"MQTT"),
        "bot and both agents": (r"TG-BOT-SERVER.{0,120}XGENT-WDS.{0,80}XGENT-MCS|TG-BOT-SERVER.{0,120}XGENT-MCS.{0,80}XGENT-WDS", r"TG-BOT-SERVER.{0,120}XGENT-WDS.{0,80}XGENT-MCS|TG-BOT-SERVER.{0,120}XGENT-MCS.{0,80}XGENT-WDS"),
        "license": (r"MIT", r"MIT"),
        "canonical release notes": (r"https://github\.com/invinby/XIDER/releases", r"https://github\.com/invinby/XIDER/releases"),
        "supervisor limitation": (r"Guard Keeper.{0,120}(?:cannot|can't).{0,80}(?:offline|powered-off).{0,80}available", r"Guard Keeper.{0,120}не.{0,100}(?:без сети|выключен\w*).{0,80}доступн|Guard Keeper.{0,120}не.{0,100}доступн.{0,80}(?:без сети|выключен\w*)"),
        "X-LEX cannot change permissions": (r"X-LEX.{0,250}(?:not permissions|not change permissions)", r"X-LEX.{0,250}(?:не права|не меняет права)"),
        "source setup requirements": (r"(?=.*Python 3\.11)(?=.*Docker Compose)", r"(?=.*Python 3\.11)(?=.*Docker Compose)"),
        "signed update rollback": (r"(?=.*signed)(?=.*rollback)", r"(?=.*подписанн\w*)(?=.*откат)"),
        "conditional payload encryption": (r"ENCRYPT_PAYLOAD.{0,100}(?:conditional|not assume.{0,30}active)", r"ENCRYPT_PAYLOAD.{0,100}(?:включается настройкой|управляется параметр|условн|не считай его включённым)"),
        "offline tests are not live acceptance": (r"(?=.*fixtures)(?=.*offline)(?=.*do not connect)", r"(?=.*фикстур\w*)(?=.*офлайн)(?=.*не подключа)"),
    }
    for fact, (en_pattern, ru_pattern) in semantic_facts.items():
        if not re.search(en_pattern, english, re.IGNORECASE | re.DOTALL):
            errors.append(f"semantic fact missing from English README: {fact}")
        if not re.search(ru_pattern, russian, re.IGNORECASE | re.DOTALL):
            errors.append(f"semantic fact missing from Russian README: {fact}")

    heading_rows = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", english))

    def section_body(text: str, alias_pattern: str) -> str:
        rows = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", text))
        for index, row in enumerate(rows):
            if re.search(alias_pattern, row.group(1).strip().lower()):
                end = rows[index + 1].start() if index + 1 < len(rows) else len(text)
                return text[row.end():end]
        return ""

    # Critical claims are checked in the section where readers rely on them,
    # so a copied phrase elsewhere cannot mask a missing instruction.
    scoped_facts = {
        "purpose": {
            "owner-authorized device management": (r"XIDER.{0,100}(?:manag\w*|control\w*).{0,100}owner-authorized", r"XIDER.{0,130}управлен\w*.{0,120}(?:у владельца есть разрешение|разрешени\w*.{0,40}владельц)"),
        },
        "install/update/rollback": {
            "broker setup link": (r"broker/README\.md", r"broker/README\.md"),
            "bot env example copy": (r"TG-BOT-SERVER/\.env\.example.{0,100}TG-BOT-SERVER/\.env", r"TG-BOT-SERVER/\.env\.example.{0,100}TG-BOT-SERVER/\.env"),
            "matching shared secret for all components": (r"(?:configure|set).{0,40}bot and both agents.{0,80}same.{0,30}SHARED_KEY", r"настрой.{0,40}бота и оба агента.{0,80}одинаков\w*.{0,30}SHARED_KEY"),
            "required broker TLS credentials and payload encryption": (r"(?=.*MQTT_TLS=true)(?=.*MQTT_USERNAME)(?=.*MQTT_PASSWORD)(?=.*ENCRYPT_PAYLOAD=true)", r"(?=.*MQTT_TLS=true)(?=.*MQTT_USERNAME)(?=.*MQTT_PASSWORD)(?=.*ENCRYPT_PAYLOAD=true)"),
            "bot credentials": (r"(?=.*BOT_TOKEN)(?=.*ADMIN_ID)", r"(?=.*BOT_TOKEN)(?=.*ADMIN_ID)"),
            "Windows per-user visible Agent and Guardian tasks": (r"Windows installer.{0,150}visible per-user Scheduled Tasks.{0,80}Agent.{0,40}Guardian", r"установщик Windows.{0,150}видим\w*.{0,80}Scheduled Tasks.{0,100}Agent.{0,40}Guardian"),
            "Windows env ACL behavior": (r"Windows installer.{0,400}current Windows account.{0,100}read.{0,100}\.env.{0,200}(?:disabl\w*|remov\w*).{0,50}inherit\w*.{0,200}pre-existing explicit ACL.{0,30}entries may remain", r"установщик Windows.{0,400}текущ.{0,60}уч.{0,40}запис.{0,80}чтен.{0,100}\.env.{0,200}отключ.{0,60}наслед.{0,200}(?:ранее заданн|явн).{0,100}ACL.{0,60}могут сохраниться"),
            "macOS checkout and runtime": (r"macOS (?:installer|bootstrap).{0,180}checkout.{0,100}Python runtime", r"macOS.{0,180}checkout.{0,100}Python runtime"),
            "macOS per-user LaunchAgents": (r"macOS (?:installer|bootstrap).{0,180}per-user LaunchAgents", r"macOS.{0,180}(?:пользовательск\w* LaunchAgents|LaunchAgents.{0,80}пользовател\w*)"),
            "macOS env and previous-version rollback preservation": (r"macOS (?:installer|bootstrap).{0,220}\.env.{0,100}previous (?:version|checkout).{0,60}rollback", r"macOS.{0,220}\.env.{0,100}(?:прежн\w* (?:верси\w*|checkout)|предыдущ\w* checkout).{0,60}откат"),
        },
        "limitations": {
            "IP location is approximate, not GPS": (r"IP-based location.{0,80}approximate.{0,30}not GPS", r"геолокац\w* по IP.{0,80}приблизительн\w*.{0,40}не является GPS"),
        },
    }
    for section_name, facts in scoped_facts.items():
        en_alias, ru_alias = sections[section_name]
        bodies = (section_body(english, en_alias), section_body(russian, ru_alias))
        for fact, (en_pattern, ru_pattern) in facts.items():
            if not re.search(en_pattern, bodies[0], re.IGNORECASE | re.DOTALL):
                errors.append(f"semantic fact missing from English {section_name} section: {fact}")
            if not re.search(ru_pattern, bodies[1], re.IGNORECASE | re.DOTALL):
                errors.append(f"semantic fact missing from Russian {section_name} section: {fact}")

    # Detect a claim that one language says was accepted while the other says
    # remains unverified. Positive status requires evidence in both documents.
    positive = re.compile(r"(?i)(?:live|device|hardware|production|физическ\w* устройств\w*|при[её]мк\w*).{0,100}(?:verified|validated|accepted|tested successfully|проверен\w*|подтвержден\w*|принят\w*)")
    negative = re.compile(r"(?i)(?:live|device|hardware|production|физическ\w* устройств\w*|при[её]мк\w*).{0,100}(?:unverified|not (?:yet )?(?:verified|validated|tested|accepted)|не подтвержден\w*|не проверен\w*|не прош[её]л\w* при[её]мк\w*)|(?:unverified|not (?:yet )?(?:verified|validated|tested|accepted)|не подтвержден\w*|не проверен\w*|не прош[её]л\w* при[её]мк\w*).{0,100}(?:live|device|hardware|production|физическ\w* устройств\w*|при[её]мк\w*)")
    def status_state(text: str) -> tuple[bool, bool]:
        negated = list(negative.finditer(text))
        affirmative = False
        for match in positive.finditer(text):
            if not any(item.start() <= match.start() and match.end() <= item.end() for item in negated):
                affirmative = True
                break
        return affirmative, bool(negated)

    states = [status_state(text) for text in (english, russian)]
    if any(pos and neg for pos, neg in states) or states[0] != states[1]:
        errors.append("live/device acceptance status differs by language or is contradictory")
    return errors


EN = """## Purpose
XIDER manages owner-authorized Windows and macOS devices through Telegram. Commands are routed over MQTT.
## Architecture
```mermaid
flowchart TD
 A --> B
```
## Components
## OS support
## Install, update and rollback
Copy `TG-BOT-SERVER/.env.example` to `.env` before launch. Configure bot and both agents with the same `SHARED_KEY`, `MQTT_TLS=true`, broker `MQTT_USERNAME` and `MQTT_PASSWORD`, and `ENCRYPT_PAYLOAD=true`; the bot also needs `BOT_TOKEN` and `ADMIN_ID`. See [broker setup](broker/README.md).

Run `cp TG-BOT-SERVER/.env.example TG-BOT-SERVER/.env` before launch. Configure bot and both agents with the same `SHARED_KEY`, `MQTT_TLS=true`, broker `MQTT_USERNAME` and `MQTT_PASSWORD`, and `ENCRYPT_PAYLOAD=true`; the bot also needs `BOT_TOKEN` and `ADMIN_ID`. See [broker setup](broker/README.md).

The Windows installer creates visible per-user Scheduled Tasks named Agent and Guardian. It grants the current Windows account read access to `.env` and disables inherited ACL entries; pre-existing explicit ACL entries may remain. The macOS installer prepares the checkout and Python runtime, creates per-user LaunchAgents, and preserves `.env` and the previous version for rollback.
## Permissions and security
## Diagnostics
## Limitations
IP-based location is approximate, not GPS.
## License
## Status
XIDER 4.3.4 for Windows and macOS uses Telegram and MQTT. TG-BOT-SERVER, XGENT-WDS and XGENT-MCS. Guard Keeper cannot make an offline computer available. X-LEX changes wording, not permissions. Python 3.11 and Docker Compose are required. Signed updates support rollback. ENCRYPT_PAYLOAD encryption is conditional. Offline fixtures do not connect to Telegram, MQTT or a VPS. MIT. Release: https://github.com/invinby/XIDER/releases.
Live acceptance remains unverified.
"""
RU = """## Назначение
XIDER — исходный код системы управления устройствами Windows и macOS, на которые у владельца есть разрешение. Команды передаются через Telegram и MQTT.
## Архитектура
```mermaid
flowchart TD
 A --> B
```
## Компоненты
## Поддержка ОС
## Установка, обновление и откат
Перед запуском скопируй `TG-BOT-SERVER/.env.example` в `.env`. Настрой бота и оба агента на одинаковый `SHARED_KEY`, `MQTT_TLS=true`, broker `MQTT_USERNAME` и `MQTT_PASSWORD`, а также `ENCRYPT_PAYLOAD=true`; боту дополнительно нужны `BOT_TOKEN` и `ADMIN_ID`. Смотри [настройку брокера](broker/README.md).

Перед запуском выполни `cp TG-BOT-SERVER/.env.example TG-BOT-SERVER/.env`. Настрой бота и оба агента на одинаковый `SHARED_KEY`, `MQTT_TLS=true`, broker `MQTT_USERNAME` и `MQTT_PASSWORD`, а также `ENCRYPT_PAYLOAD=true`; боту дополнительно нужны `BOT_TOKEN` и `ADMIN_ID`. Смотри [настройку брокера](broker/README.md).

Установщик Windows создаёт видимые задачи Scheduled Tasks с именами Agent и Guardian для текущего пользователя. Он выдаёт текущей учётной записи Windows чтение `.env` и отключает наследуемые записи ACL; ранее заданные явные записи ACL могут сохраниться. Установщик macOS подготавливает checkout и Python runtime, создаёт пользовательские LaunchAgents и сохраняет `.env` и прежнюю версию для отката.
## Разрешения и безопасность
## Диагностика
## Ограничения
Геолокация по IP приблизительна и не является GPS.
## Лицензия
## Статус
XIDER 4.3.4 для Windows и macOS использует Telegram и MQTT. TG-BOT-SERVER, XGENT-WDS и XGENT-MCS. Guard Keeper не сделает компьютер без сети доступным. X-LEX меняет текст, но не права. Нужны Python 3.11 и Docker Compose. Подписанные обновления поддерживают откат. Шифрование ENCRYPT_PAYLOAD включается настройкой. Офлайн-фикстуры не подключаются к Telegram, MQTT или VPS. MIT. Релизы: https://github.com/invinby/XIDER/releases.
Приёмка на реальных устройствах не подтверждена.
"""


def test_complete_pair_passes():
    assert check_readme_pair(EN, RU) == []


def test_missing_translation_section():
    assert any("components" in error for error in check_readme_pair(EN, RU.replace("## Компоненты", "## Другое")))


def test_broken_relative_link():
    assert any("link" in error for error in check_readme_pair(EN + "\n[missing](definitely-missing.md)", RU))


def test_unclosed_fence():
    assert any("fence" in error for error in check_readme_pair(EN + "\n```python\nprint(1)", RU))


def test_missing_mermaid_block():
    assert any("Mermaid" in error for error in check_readme_pair(EN, RU.replace("```mermaid\nflowchart TD\n A --> B\n```", "")))


def test_language_only_acceptance_claim():
    assert any("status" in error for error in check_readme_pair(EN.replace("Live acceptance remains unverified.", "Live acceptance is verified."), RU))


def test_positive_acceptance_claim_conflicts_with_negative_claim_in_same_language():
    contradictory = EN.replace(
        "Live acceptance remains unverified.",
        "Live acceptance remains unverified. Live acceptance is verified.",
    )
    assert any("status" in error for error in check_readme_pair(contradictory, RU))


def test_rejects_non_https_external_link():
    assert any("HTTPS" in error for error in check_readme_pair(EN + "\n[site](http://example.com)", RU))


def test_repository_readmes_pass():
    root = Path(__file__).resolve().parents[1]
    assert check_readme_pair((root / "README.md").read_text(encoding="utf-8"), (root / "README.ru.md").read_text(encoding="utf-8")) == []


def test_canonical_release_link_is_consistent_by_language():
    changed = RU.replace("https://github.com/invinby/XIDER/releases", "https://github.com/invinby/XIDER")
    errors = check_readme_pair(EN, changed)
    assert any("semantic" in error for error in errors)


def test_removing_ip_location_limitation_is_reported():
    changed = EN.replace("IP-based location is approximate, not GPS.", "IP-based location is a precise GPS coordinate.")
    assert any("location" in error.lower() for error in check_readme_pair(changed, RU))


def test_platform_installer_fact_is_required():
    changed = EN.replace("visible per-user Scheduled Tasks", "hidden machine-wide services")
    assert any("Windows per-user visible Agent and Guardian tasks" in error for error in check_readme_pair(changed, RU))


def test_removing_negative_live_status_is_reported():
    changed = EN.replace("Live acceptance remains unverified.", "")
    assert any("live/device acceptance status differs" in error.lower() for error in check_readme_pair(changed, RU))


def test_broker_configuration_is_required_before_launch():
    changed = EN.replace("ENCRYPT_PAYLOAD=true", "ENCRYPT_PAYLOAD=false")
    assert any("required broker TLS credentials and payload encryption" in error for error in check_readme_pair(changed, RU))


def test_owner_authorized_management_purpose_cannot_be_reversed():
    changed = EN.replace("manages owner-authorized", "surveils unauthorized")
    assert any("owner-authorized device management" in error for error in check_readme_pair(changed, RU))


def test_guard_keeper_offline_limitation_requires_negative_claim():
    changed = EN.replace("cannot make an offline computer available", "can make an offline computer available")
    assert any("supervisor limitation" in error for error in check_readme_pair(changed, RU))


def test_windows_acl_documentation_does_not_overclaim_exclusive_access():
    changed = EN.replace("pre-existing explicit ACL entries may remain", "only the current account can read the file")
    assert any("Windows env ACL behavior" in error for error in check_readme_pair(changed, RU))
