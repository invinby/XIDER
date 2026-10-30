# XIDER build protection / Защита сборок XIDER

## Owner-requested build / Сборка по запросу владельца

1. In GitHub, open **Actions → Build & Release Standalone Agents → Run workflow** and choose the workflow's current branch so GitHub can load the form.
2. Enter a published `vMAJOR.MINOR.PATCH` tag in the `release_tag` field, then start the run. The workflow checks out and validates that exact tag and records its resolved commit.
3. For a manual run on a version tag, the workflow builds and verifies the packages, then uploads `XIDER-Protected-Build-Inventory`. It does **not** create or publish a GitHub Release. Publishing remains limited to a tag push.
4. Download the separate `XIDER-Components` artifact and its signed `protected-build-manifest.json`. The manifest binds the exact source tag and commit to archive hashes and hashes of every file inside each ZIP. The workflow verifies the Ed25519 signature against the public key pinned by the agents before uploading the report.

The report states `obfuscation.state=not_applied`. XIDER currently compiles Windows and macOS agent binaries with Nuitka, which removes plaintext Python source from those packages; compilation is not obfuscation. The source and server archives are explicitly classified as source bundles. No additional obfuscator is run or claimed. A manual build is a preview: the Windows executable has no Authenticode claim, and the macOS app is unsigned/not notarized. The signed inventory authenticates their exact bytes; it does not replace platform code signing. A signing key is read only from the GitHub `xider-release-signing` environment and is not bundled into client artifacts.

## Сборка по запросу владельца

1. В GitHub открой **Actions → Build & Release Standalone Agents → Run workflow** и выбери текущую ветку workflow, чтобы загрузить форму.
2. В поле `release_tag` укажи опубликованный тег `vMAJOR.MINOR.PATCH` и запусти workflow. Workflow проверит этот тег, соберёт именно его и запишет разрешённый commit.
3. При ручном запуске на теге workflow соберёт и проверит пакеты, затем загрузит `XIDER-Protected-Build-Inventory`. Он **не создаёт и не публикует GitHub Release**. Публикация доступна только при push тега.
4. Скачай отдельный artifact `XIDER-Components` и подписанный `protected-build-manifest.json`. Манифест привязывает точный тег и commit исходников к хешам архивов и каждого файла внутри ZIP. Перед загрузкой отчёта workflow проверяет подпись Ed25519 по открытому ключу, закреплённому в агентах.

В отчёте указано `obfuscation.state=not_applied`. Сейчас XIDER компилирует агенты Windows и macOS через Nuitka, убирая открытый Python-код из этих пакетов; компиляция не является обфускацией. Архивы исходников и сервера явно отмечаются как содержащие исходный код. Другой обфускатор не запускается и его применение не заявляется. Ручная сборка — preview: для Windows не заявляется Authenticode-подпись, приложение macOS не подписано и не notarized. Подписанный inventory подтверждает точные байты архивов, но не заменяет платформенную подпись кода. Ключ подписи берётся только из GitHub environment `xider-release-signing` и не попадает в клиентские пакеты.
