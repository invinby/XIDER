"""Bounded one-shot location results; native adapters never request remote consent."""
import datetime
import json
import math
import time
import urllib.request

def result(state, label, source="system", latitude=None, longitude=None, accuracy_m=None):
    return {"source": source, "state": state, "latitude": latitude, "longitude": longitude,
            "accuracy_m": accuracy_m, "label": label,
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}

def read_system_location(backend=None):
    try:
        backend = backend or native_backend()
        permission, enabled = backend.diagnose()
        guidance = "Откройте настройки на самом устройстве: " + SETTINGS
        if not enabled or permission == "unavailable":
            return result("unavailable", "Служба/датчик геолокации недоступны. " + guidance)
        if permission == "denied":
            return result("denied", "Доступ к системной геолокации запрещён. " + guidance)
        if permission != "granted":
            return result("needs_live_test", "Разрешение не подтверждено; нужен видимый локальный consent. " + guidance)
        latitude, longitude, accuracy = backend.read_once(8.0)
        if not all(type(v) in (float, int) and math.isfinite(v) for v in (latitude, longitude, accuracy)):
            return result("unknown", "Некорректный ответ системной геолокации. " + guidance)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and accuracy >= 0):
            return result("unknown", "Некорректный ответ системной геолокации. " + guidance)
        return result("granted", "Системная геолокация; точность определяется ОС, источник не обязательно GPS.",
                      latitude=latitude, longitude=longitude, accuracy_m=accuracy)
    except PermissionError:
        return result("denied", "ОС отказала в доступе. Настройки: " + SETTINGS)
    except TimeoutError:
        return result("needs_live_test", "Тайм-аут разового запроса. Настройки: " + SETTINGS)
    except (ImportError, OSError):
        return result("unavailable", "Системная геолокация недоступна. Настройки: " + SETTINGS)
    except Exception:
        # Native exceptions can contain private location details: never log them.
        return result("needs_live_test", "Нужна проверка на устройстве. Настройки: " + SETTINGS)

def read_ip_estimate():
    deadline = time.monotonic() + 6.0
    for url in ("https://ipapi.co/json/", "https://ipinfo.io/json"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "XIDER-Agent/3"})
            with urllib.request.urlopen(req, timeout=min(3.0, remaining)) as response:
                raw = response.read(16385)
            if len(raw) > 16384:
                continue
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("error") or data.get("status") == "fail":
                continue
            parts = [str(data[key])[:100] for key in ("city", "region", "country_name")
                     if isinstance(data.get(key), str) and data[key]]
            if not parts:
                continue
            return result("granted", ", ".join(parts), source="ip_estimate")
        except (OSError, ValueError):
            continue
    return result("unavailable", "Оценка города/региона по IP недоступна.", source="ip_estimate")

def location_capability():
    """Read permission metadata only: never request consent or a position."""
    try:
        permission, enabled = native_backend().diagnose()
        state = ("unavailable" if not enabled or permission == "unavailable" else
                 permission if permission in ("granted", "denied") else "needs_live_test")
    except (ImportError, OSError):
        state = "unavailable"
    except Exception:
        state = "needs_live_test"
    return state, ("source=system; uncertainty=unknown until explicit one-shot read. "
                   "Optional source=ip_estimate is city/region only; uncertainty=unknown. "
                   "Owner-local settings: " + SETTINGS)


def location_text(value):
    if value.get("source") == "comparison":
        system = value.get("system_location", {})
        estimate = value.get("ip_estimate", {})
        system_text = location_text(system) if isinstance(system, dict) else "Системная локация: неизвестно"
        estimate_text = location_text(estimate) if isinstance(estimate, dict) else "IP estimate: недоступно"
        return "Сравнение источников (разовый запрос; координаты не сохраняются):\n" + system_text + "\n" + estimate_text
    if value["source"] == "ip_estimate":
        return "Приблизительная оценка города/региона по IP, не GPS; точность неизвестна.\n" + value["label"]
    if value["state"] == "granted":
        return (value["label"] + "\n" + str(value["latitude"]) + ", " + str(value["longitude"])
                + "\nПогрешность: " + str(value["accuracy_m"]) + " м")
    return value["label"]

def compare_location_results(system_value, ip_value):
    """Build a transient, source-labelled comparison with no provider IP/coordinates."""
    system_keys = ("source", "state", "latitude", "longitude", "accuracy_m", "label", "checked_at")
    ip_keys = ("source", "state", "label", "checked_at")
    system = {key: system_value.get(key) for key in system_keys if key in system_value}
    estimate = {key: ip_value.get(key) for key in ip_keys if key in ip_value}
    estimate.update(latitude=None, longitude=None, accuracy_m=None)
    state = system.get("state")
    if state not in {"granted", "denied", "not_determined", "unavailable", "needs_live_test", "unknown"}:
        state = "unknown"
    comparison = result(state, "Сравнение системного источника и оценки города по IP.", source="comparison")
    comparison.update(system_location=system, ip_estimate=estimate)
    return comparison

SETTINGS = "ms-settings:privacy-location"

class WindowsLocationBackend:
    def __init__(self):
        import sys
        if sys.platform != "win32":
            raise OSError("Windows required")
        from winrt.windows.devices.geolocation import Geolocator
        from winrt.windows.security.authorization.appcapabilityaccess import AppCapability
        self.capability = AppCapability.create("location")
        self.locator = Geolocator()

    def diagnose(self):
        # No RequestAccessAsync here: MQTT workers have no foreground UI thread.
        status = int(self.locator.location_status)
        access = int(self.capability.check_access())
        # AppCapabilityAccessStatus: DeniedBySystem=0, NotDeclaredByApp=1,
        # DeniedByUser=2, UserPromptRequired=3, Allowed=4.
        if access in (0, 2):
            return "denied", True
        if access != 4:
            return "not_determined", True
        if status in (0, 1, 2, 4):
            return "granted", True
        if status == 3:
            return "granted", False
        if status == 5:
            return "unavailable", False
        return "not_determined", True

    def read_once(self, timeout):
        import asyncio
        import datetime
        async def read():
            try:
                pos = await asyncio.wait_for(self.locator.get_geoposition_async(
                    datetime.timedelta(seconds=0), datetime.timedelta(seconds=timeout)), timeout)
            except OSError as exc:
                if getattr(exc, "winerror", None) == -2147024891 or getattr(exc, "hresult", None) == -2147024891:
                    raise PermissionError() from None
                raise
            coord = pos.coordinate
            basic = coord.point.position
            return basic.latitude, basic.longitude, coord.accuracy
        return asyncio.run(read())

def native_backend():
    return WindowsLocationBackend()
