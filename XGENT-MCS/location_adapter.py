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

def permission_capability(permission):
    """Passively read macOS permission state without prompting or using the resource."""
    try:
        if permission in {"camera", "microphone"}:
            import AVFoundation
            media_type = (AVFoundation.AVMediaTypeVideo if permission == "camera"
                          else AVFoundation.AVMediaTypeAudio)
            status = int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(media_type))
            state = {0: "not_determined", 1: "unavailable", 2: "denied", 3: "granted"}.get(status, "unknown")
            return state, "Read from AVFoundation authorization status; no capture or consent prompt was started."
        if permission == "screen_recording":
            import Quartz
            if Quartz.CGPreflightScreenCaptureAccess():
                return "granted", "CGPreflightScreenCaptureAccess reports Screen Recording permission; no capture was started."
            return "needs_live_test", "macOS does not expose a passive denied/not-determined distinction here; run an owner-approved capture test."
        if permission == "accessibility":
            import ApplicationServices
            trusted = bool(ApplicationServices.AXIsProcessTrustedWithOptions(
                {ApplicationServices.kAXTrustedCheckOptionPrompt: False}
            ))
            state = "granted" if trusted else "needs_live_test"
            return state, "Read from macOS TCC Accessibility trust status without prompting or controlling the interface."
        if permission == "location":
            return location_capability()
        if permission == "file_access":
            return "needs_live_test", (
                "TCC Files and Folders access is path-specific; only an owner-approved check for the selected path can confirm it."
            )
        return "unknown", "Unknown permission; no system access was attempted."
    except (ImportError, AttributeError, OSError):
        return "unavailable", "The local macOS TCC permission status API is unavailable; no permission was inferred."
    except Exception:
        return "unknown", "macOS did not return a readable permission status; no permission was inferred."


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

SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices"
_runtime = None

class MacLocationRuntime:
    """Owned by the main Cocoa run loop; MQTT threads submit bounded requests."""
    def __init__(self):
        import queue
        import threading
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Main Cocoa thread required")
        import CoreLocation
        from Foundation import NSObject, NSRunLoop, NSDate
        self.core = CoreLocation
        self.runloop, self.date = NSRunLoop.currentRunLoop(), NSDate
        self.queue = queue.Queue()
        self.active = []
        class Delegate(NSObject):
            def locationManagerDidChangeAuthorization_(delegate, manager):
                job = getattr(delegate, "job", {})
                if job.get("cancelled"):
                    return
                status = int(manager.authorizationStatus())
                if job.get("kind") != "permission":
                    return
                if status in (3, 4):
                    job["state"] = "granted"
                    job["text"] = "macOS разрешила геолокацию."
                    job["done"].set()
                elif status in (1, 2):
                    job["state"] = "denied"
                    job["text"] = "В геолокации отказано на самом Mac."
                    job["done"].set()

            def locationManager_didChangeAuthorizationStatus_(delegate, manager, status):
                delegate.locationManagerDidChangeAuthorization_(manager)

            def locationManager_didUpdateLocations_(delegate, manager, locations):
                job = delegate.job
                if not job["cancelled"]:
                    location = locations[-1]
                    coord = location.coordinate()
                    job["value"] = (coord.latitude, coord.longitude, location.horizontalAccuracy())
                    job["done"].set()
                manager.stopUpdatingLocation()
            def locationManager_didFailWithError_(delegate, manager, error):
                job = delegate.job
                code = error.code()
                if code == 1:
                    job["error"] = PermissionError()
                elif code == 0:
                    # Core Location may fail to produce a fix "right now";
                    # that is transient, not proof location services are off.
                    job["error"] = TimeoutError()
                else:
                    job["error"] = OSError()
                job["done"].set()
                manager.stopUpdatingLocation()
        self.delegate_class = Delegate

    def _start_permission_request(self, job):
        """Start an OS-owned consent flow from the Cocoa thread, without using the resource."""
        permission = job["permission"]

        def finish(state, text):
            if not job["cancelled"]:
                job["state"] = state
                job["text"] = text
                job["done"].set()

        if permission in {"camera", "microphone"}:
            import AVFoundation
            media_type = (AVFoundation.AVMediaTypeVideo if permission == "camera"
                          else AVFoundation.AVMediaTypeAudio)
            status = int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(media_type))
            if status == 3:
                finish("granted", "macOS уже разрешила доступ.")
            elif status in {1, 2}:
                finish("denied", "macOS уже отклонила доступ; измени его в Системных настройках.")
            elif status == 0:
                AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    media_type,
                    lambda granted: finish(
                        "granted" if granted else "denied",
                        "macOS разрешила доступ." if granted else "В доступе отказано на самом Mac.",
                    ),
                )
            else:
                finish("unknown", "macOS вернула неизвестный статус разрешения.")
            return

        if permission == "screen_recording":
            import Quartz
            if Quartz.CGPreflightScreenCaptureAccess():
                finish("granted", "macOS уже разрешила запись экрана.")
            else:
                requested = bool(Quartz.CGRequestScreenCaptureAccess())
                finish("granted" if requested else "prompt_requested",
                       "macOS разрешила запись экрана." if requested else
                       "Системный запрос вызван. Если окно не появилось, выдай доступ в Системных настройках.")
            return

        if permission == "accessibility":
            import ApplicationServices
            trusted = bool(ApplicationServices.AXIsProcessTrustedWithOptions(
                {ApplicationServices.kAXTrustedCheckOptionPrompt: True}
            ))
            finish("granted" if trusted else "prompt_requested",
                   "macOS уже разрешила управление интерфейсом." if trusted else
                   "macOS показала системную подсказку; переключатель нужно подтвердить в Системных настройках.")
            return

        if permission == "location":
            manager = self.core.CLLocationManager.alloc().init()
            delegate = self.delegate_class.alloc().init()
            delegate.job = job
            manager.setDelegate_(delegate)
            self.active.append((manager, delegate, job))
            status = int(manager.authorizationStatus())
            if status in (3, 4):
                finish("granted", "macOS уже разрешила геолокацию.")
            elif status in (1, 2):
                finish("denied", "macOS уже отклонила геолокацию; измени доступ в Системных настройках.")
            else:
                manager.requestWhenInUseAuthorization()
            return

        finish("unsupported", "Для этого разрешения нет отдельного системного запроса; открой Системные настройки.")

    def pump(self):
        import queue
        import time
        while True:
            try:
                job = self.queue.get_nowait()
            except queue.Empty:
                break
            if job["cancelled"] or time.monotonic() >= job["deadline"]:
                continue
            if job.get("kind") == "permission":
                try:
                    self._start_permission_request(job)
                except Exception:
                    job["state"] = "unavailable"
                    job["text"] = "Системный запрос недоступен; открой соответствующий раздел Системных настроек."
                    job["done"].set()
                continue
            if not self.core.CLLocationManager.locationServicesEnabled():
                job["error"] = OSError()
                job["done"].set()
                continue
            if self.core.CLLocationManager.authorizationStatus() not in (3, 4):
                job["error"] = PermissionError()
                job["done"].set()
                continue
            try:
                manager = self.core.CLLocationManager.alloc().init()
                delegate = self.delegate_class.alloc().init()
                delegate.job = job
                manager.setDelegate_(delegate)
                self.active.append((manager, delegate, job))
                manager.requestLocation()  # exactly one fix, no startUpdatingLocation
            except Exception:
                job["error"] = OSError()
                job["done"].set()
        self.runloop.runUntilDate_(self.date.dateWithTimeIntervalSinceNow_(0.1))
        for manager, delegate, job in list(self.active):
            if job["done"].is_set() or time.monotonic() >= job["deadline"]:
                job["cancelled"] = True
                manager.stopUpdatingLocation()
                manager.setDelegate_(None)
                self.active.remove((manager, delegate, job))

    def read_once(self, timeout):
        import threading
        import time
        job = {"kind": "location", "done": threading.Event(), "deadline": time.monotonic() + timeout, "cancelled": False}
        self.queue.put(job)
        if not job["done"].wait(timeout):
            job["cancelled"] = True
            raise TimeoutError()
        if "error" in job:
            raise job["error"]
        return job["value"]

    def request_permission(self, permission, timeout):
        import threading
        import time
        job = {"kind": "permission", "permission": permission, "done": threading.Event(),
               "deadline": time.monotonic() + timeout, "cancelled": False}
        self.queue.put(job)
        if not job["done"].wait(timeout):
            job["cancelled"] = True
            return {"state": "prompt_requested", "text": "Запрос отправлен на Mac; проверь системное окно и ответь на нём."}
        return {"state": job.get("state", "unknown"), "text": job.get("text", "Состояние разрешения неизвестно.")}

def signed_bundle_available():
    import sys
    import subprocess
    from pathlib import Path
    if sys.platform != "darwin":
        return False
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.extend(Path(sys.executable).resolve().parents)
    compiled = globals().get("__compiled__")
    containing_dir = getattr(compiled, "containing_dir", None)
    if isinstance(containing_dir, (str, Path)) and str(containing_dir):
        compiled_path = Path(containing_dir).resolve()
        candidates.append(compiled_path)
        candidates.extend(compiled_path.parents)
    app = next((path for path in candidates if path.suffix == ".app"), None)
    if app is None:
        return False
    import plistlib
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        if info.get("CFBundleIdentifier") != "com.xider.agent" or not info.get("NSLocationUsageDescription", "").strip():
            return False
        verified = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                                  capture_output=True, timeout=10)
        identity = subprocess.run(["codesign", "-dv", "--verbose=4", str(app)],
                                  capture_output=True, text=True, timeout=10)
        details = identity.stdout + identity.stderr
        return (verified.returncode == identity.returncode == 0 and "Signature=adhoc" not in details
                and ("Authority=Apple Development:" in details or "Authority=Developer ID Application:" in details)
                and "TeamIdentifier=not set" not in details)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False

def initialize_runtime():
    global _runtime
    if signed_bundle_available():
        try:
            _runtime = MacLocationRuntime()
        except (ImportError, OSError):
            _runtime = None
    return _runtime

class MacLocationBackend:
    def diagnose(self):
        if _runtime is None:
            return "unknown", True
        core = _runtime.core
        permission = {0: "not_determined", 1: "denied", 2: "denied", 3: "granted", 4: "granted"}.get(
            int(core.CLLocationManager.authorizationStatus()), "unknown")
        return permission, bool(core.CLLocationManager.locationServicesEnabled())

    def read_once(self, timeout):
        return _runtime.read_once(timeout)

    def request_permission(self, permission, timeout):
        if _runtime is None:
            return {"state": "unavailable", "text": "Нет активного Cocoa-сеанса для системного запроса."}
        return _runtime.request_permission(permission, timeout)

def native_backend():
    return MacLocationBackend()

def request_permission(permission, timeout=90):
    """Request consent without capturing media or reading a location."""
    return native_backend().request_permission(permission, timeout)

def visible_local_setup():
    """Explicit local flag shows a foreground explanation before OS consent."""
    if not signed_bundle_available():
        raise RuntimeError("Location setup requires the exact properly signed XIDER.app.")
    import time
    from AppKit import NSApplication, NSAlert, NSAlertFirstButtonReturn
    from Foundation import NSRunLoop, NSDate
    import CoreLocation
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)
    app.activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Разрешить разовую геолокацию XIDER?")
    alert.setInformativeText_("После вашего согласия ОС запросит разрешение. XIDER читает одну позицию только по явному запросу; фонового отслеживания нет.")
    alert.addButtonWithTitle_("Открыть системный запрос")
    alert.addButtonWithTitle_("Отмена")
    if alert.runModal() != NSAlertFirstButtonReturn:
        return
    manager = CoreLocation.CLLocationManager.alloc().init()
    manager.requestWhenInUseAuthorization()
    deadline = time.monotonic() + 120
    while manager.authorizationStatus() == 0 and time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
