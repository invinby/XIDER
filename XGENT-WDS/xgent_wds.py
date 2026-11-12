"""Клиент XGENT для Windows (Asus).

Подключается к публичному MQTT-брокеру, слушает команды бота
и выполняет их на этой машине. Прозрачная версия: в системном трее
всегда виден значок с пунктом «Остановить XGENT».
"""

import msvcrt
import os
from pathlib import Path
import sys
import threading
from xg_core import runtime as _runtime

# Release build probe runs before importing device services or loading .env.
if len(sys.argv) > 1 and sys.argv[1] == "--command-approval-bundle-smoke":
    import command_approval_bundle_smoke  # noqa: F401 - exits after writing its report




_NUITKA_RUNTIME = bool("__compiled__" in globals() and not getattr(sys, "frozen", False))
_BUNDLED_RUNTIME = bool(getattr(sys, "frozen", False) or _NUITKA_RUNTIME)




















































_bootstrap_lock_file = None
EARLY_UPDATE_STATE = "none"




_RUNTIME_FUNCTIONS = _runtime.bind_runtime_functions(globals())
globals().update(_RUNTIME_FUNCTIONS)


if __name__ == "__main__" and not _BUNDLED_RUNTIME:
    if not _claim_bootstrap_lock():
        sys.stderr.write("FATAL: XGENT-WDS уже запущен; второй экземпляр остановлен.\n")
        raise SystemExit(1)
    _repo_root = Path(__file__).resolve().parent.parent
    _release_module_dir = str(_repo_root / "XGENT-MCS")
    if _release_module_dir not in sys.path:
        sys.path.append(_release_module_dir)

if not _BUNDLED_RUNTIME:
    _repo_root = Path(__file__).resolve().parent.parent
    _release_module_dir = str(_repo_root / "XGENT-MCS")
    if _release_module_dir not in sys.path:
        sys.path.append(_release_module_dir)
from xg_core import legacy_wiring as _legacy_wiring
_legacy_wiring.install(globals())



from release_signature import trusted_release_keys_ready, verify_manifest_signature
if not _BUNDLED_RUNTIME:
    from update_package import (
        UPDATE_STATE_NAME,
        download_verified_source_archive,
        extract_agent_files,
        install_agent_files,
        mark_agent_update_healthy,
        prepare_agent_update_start,
        rollback_unhealthy_agent,
        read_agent_update_result,
        update_operation_id,
    )

    if __name__ == "__main__":
        EARLY_UPDATE_STATE = prepare_agent_update_start(
            Path.home() / ".xgent" / UPDATE_STATE_NAME,
            Path(__file__).resolve().parent,
        )
        if EARLY_UPDATE_STATE in {"recovered", "rolled_back"}:
            message = (
                "XIDER-WDS: установка прервалась; восстановлены прежние файлы.\n"
                if EARLY_UPDATE_STATE == "recovered"
                else "XIDER-WDS: новая версия не прошла проверку; возвращена прежняя.\n"
            )
            sys.stderr.write(message)
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])














# Команды, которые умеет выполнять этот клиент. Используются и для
# маршрутизации, и для отчёта о возможностях (capabilities).



# =====================================================================
#  Чистые функции форматирования (тестируются без MQTT/оборудования)
# =====================================================================























# ---------- волна 2: диагностика и удалённые операции ----------









# ---------- хелперы: экран, сеть, диски, софт (чистые, тестируемые) ----------



# --- Night Light: reverse-engineered CloudStore blob (nightlight-cli, MIT) ---







# =====================================================================
#  Иконка трея и клиент
# =====================================================================



from xg_core import client as _client_runtime
XgentClient = _client_runtime.bind_runtime_class(globals())









_COMMAND_MODULE_LOADER = None






















# Preserve class-level access used by legacy callers and command contract checks.
for _method_name, _handler in _device_interaction.bind_class_methods(globals()).items():
    setattr(XgentClient, _method_name, _handler)








if __name__ == "__main__":
    main()
