"""Windows display control command handlers."""

from __future__ import annotations

import re


def screenshot(
    payload: dict, *, device_id, screenshot_session_available, bounded_screen_grab,
    io_module, base64_module, max_screenshot_bytes, timeout_errors,
    publish_response, logger,
) -> None:
    """Capture one screenshot and publish bounded, distinguishable diagnostics."""
    result = {
        "type": "screenshot", "device_id": device_id, "platform": "Windows",
        "state": "command_failed", "detail": "Capture failed", "image": None,
    }
    img = None
    try:
        if not screenshot_session_available():
            result.update(state="session_unavailable", detail="No accessible interactive input desktop")
        else:
            img = bounded_screen_grab()
            result.update(state="file_unreadable", detail="Capture did not return a readable image")
            if img is not None:
                width, height = img.size
                if width <= 0 or height <= 0:
                    result.update(state="file_empty", detail="Capture has empty dimensions")
                elif width * height > 40_000_000:
                    result.update(state="file_oversized", detail="Raw capture exceeds 40 megapixels")
                else:
                    img.thumbnail((1600, 1200))
                    with io_module.BytesIO() as buf:
                        with img.convert("RGB") as rgb:
                            rgb.save(buf, format="JPEG", quality=80, optimize=True)
                        data = buf.getvalue()
                    if not data:
                        result.update(state="file_empty", detail="Encoded image is empty")
                    elif len(data) > max_screenshot_bytes:
                        result.update(state="file_oversized", detail="Encoded image exceeds 30 MiB")
                    else:
                        result.update(
                            state="success",
                            detail="One capture produced a valid image; no persistent permission claim",
                            image=base64_module.b64encode(data).decode("ascii"),
                        )
    except ImportError:
        result.update(state="tool_missing", detail="Pillow ImageGrab is unavailable")
    except PermissionError:
        result.update(state="permission_denied", detail="Screen capture reported access denied; no UAC auto-consent")
    except timeout_errors:
        result.update(state="timeout", detail="Screen capture timed out")
    except Exception as exc:
        result.update(detail=f"{type(exc).__name__}: {str(exc)[:500]}")
    finally:
        if img is not None:
            img.close()
    try:
        delivered = publish_response("screenshot", result)
    except Exception:
        delivered = False
    if delivered is False:
        failure = {**result, "state": "transport_failed", "detail": "MQTT result delivery failed", "image": None}
        try:
            publish_response("screenshot", failure)
        except Exception:
            logger.warning("Screenshot transport diagnostic could not be delivered")


def wallpaper_set(
    payload: dict, *, device_id, config_dir, ctypes_module, os_module,
    tempfile_module, base64_module, random_module, request_module,
    open_file, publish_response, logger,
) -> None:
    """Set the desktop wallpaper from supplied bytes, a URL, or the legacy random source."""
    url = (payload.get("url") or "").strip()
    b64 = (payload.get("b64") or "").strip()
    random_meme = payload.get("random_meme", False)

    try:
        backup_path = config_dir / "backup_wallpaper.txt"
        if not backup_path.exists():
            current_buffer = ctypes_module.create_unicode_buffer(512)
            ctypes_module.windll.user32.SystemParametersInfoW(0x0073, 512, current_buffer, 0)
            if current_buffer.value and os_module.path.exists(current_buffer.value):
                backup_path.write_text(current_buffer.value, encoding="utf-8")
    except Exception as exc:
        logger.warning("Не удалось сохранить бэкап обоев: %s", exc)

    image_path = os_module.path.join(tempfile_module.gettempdir(), "xgent_wallpaper.jpg")
    ok, error = False, None
    meme_urls = [
        "https://i.imgflip.com/4/30b1gx.jpg",
        "https://i.imgflip.com/4/1g8my4.jpg",
        "https://i.imgflip.com/4/1bij.jpg",
        "https://i.imgflip.com/4/26am.jpg",
        "https://i.imgflip.com/4/1ur9b0.jpg",
        "https://cataas.com/cat?width=1920&height=1080",
        "https://picsum.photos/1920/1080",
    ]

    try:
        if b64:
            raw_bytes = base64_module.b64decode(b64)
            with open_file(image_path, "wb") as stream:
                stream.write(raw_bytes)
            ctypes_module.windll.user32.SystemParametersInfoW(0x0014, 0, image_path, 0x3)
            ok = True
            logger.info("Обои установлены из переданного фото (размер %d байт)", len(raw_bytes))
        elif random_meme or not url:
            target_url = random_module.choice(meme_urls)
            request = request_module.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
            with request_module.urlopen(request, timeout=12) as response, open_file(image_path, "wb") as output:
                output.write(response.read())
            ctypes_module.windll.user32.SystemParametersInfoW(0x0014, 0, image_path, 0x3)
            ok = True
            logger.info("Обои установлены из случайного мема: %s", target_url)
        else:
            request = request_module.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with request_module.urlopen(request, timeout=12) as response, open_file(image_path, "wb") as output:
                output.write(response.read())
            ctypes_module.windll.user32.SystemParametersInfoW(0x0014, 0, image_path, 0x3)
            ok = True
            logger.info("Обои установлены по URL: %s", url)
    except Exception as exc:
        ok, error = False, str(exc)

    publish_response("wallpaper_set", {
        "type": "wallpaper_set", "device_id": device_id, "ok": ok, "error": error,
    })
    text = "🖼 Обои успешно обновлены на рабочем столе!" if ok else f"⚠️ Ошибка установки обоев: {error}"
    publish_response("output", {"type": "wallpaper_set", "device_id": device_id, "ok": ok, "text": text})


def contact_sheet(
    payload: dict, *, publish_response, device_id, capture_contact_sheet,
    screenshot_session_available, base64_module, datetime_module, logger,
) -> None:
    result = {
        "type": "display_contact_sheet", "schema": "xider.display-contact-sheet.v1",
        "feature_id": "D02-F06", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": "invalid_request", "source": "windows_enum_display_monitors",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(),
        "data": None, "image": None,
    }
    selected = payload.get("selected_displays")
    topology_id = payload.get("topology_id")
    if not screenshot_session_available():
        result["state"] = "session_unavailable"
    elif isinstance(topology_id, str) and len(topology_id) == 16 and isinstance(selected, list):
        try:
            image, displays = capture_contact_sheet(selected, topology_id)
            result.update(state="success", data={"displays": displays}, image=base64_module.b64encode(image).decode("ascii"))
        except ValueError as exc:
            result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
        except PermissionError:
            result["state"] = "denied"
        except (ImportError, OSError):
            result["state"] = "unsupported"
        except Exception:
            logger.exception("Windows display contact sheet failed")
            result["state"] = "unknown"
    publish_response("display_contact_sheet", result)


def single_display_screenshot(
    payload: dict, *, publish_response, device_id, capture_single_display,
    screenshot_session_available, base64_module, datetime_module, logger,
) -> None:
    result = {
        "type": "display_screenshot", "schema": "xider.display-screenshot.v1",
        "feature_id": "D02-F01", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": "invalid_request", "source": "windows_enum_display_monitors",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(),
        "data": None, "image": None,
    }
    display_id = payload.get("display_id")
    topology_id = payload.get("topology_id")
    if not screenshot_session_available():
        result["state"] = "session_unavailable"
    elif isinstance(display_id, str) and isinstance(topology_id, str) and len(topology_id) == 16:
        try:
            image, manifest = capture_single_display(display_id, topology_id)
            result.update(state="success", data=manifest, image=base64_module.b64encode(image).decode("ascii"))
        except ValueError as exc:
            result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
        except PermissionError:
            result["state"] = "denied"
        except (ImportError, OSError):
            result["state"] = "unsupported"
        except Exception:
            logger.exception("Windows single display screenshot failed")
            result["state"] = "unknown"
    publish_response("display_screenshot", result)


def region_screenshot(
    payload: dict, *, publish_response, device_id, capture_display_region,
    screenshot_session_available, base64_module, datetime_module, logger,
) -> None:
    result = {
        "type": "display_region_screenshot", "schema": "xider.display-region-screenshot.v1",
        "feature_id": "D02-F03", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": "invalid_request", "source": "windows_enum_display_monitors",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(),
        "data": None, "image": None,
    }
    display_id, topology_id, region = payload.get("display_id"), payload.get("topology_id"), payload.get("region")
    if not screenshot_session_available():
        result["state"] = "session_unavailable"
    elif isinstance(display_id, str) and isinstance(topology_id, str) and len(topology_id) == 16:
        try:
            image, manifest = capture_display_region(display_id, topology_id, region)
            result.update(state="success", data=manifest, image=base64_module.b64encode(image).decode("ascii"))
        except ValueError as exc:
            result["state"] = "stale_catalog" if "topology" in str(exc).lower() else "invalid_request"
        except PermissionError:
            result["state"] = "denied"
        except (ImportError, OSError):
            result["state"] = "unsupported"
        except Exception:
            logger.exception("Windows selected display region capture failed")
            result["state"] = "unknown"
    publish_response("display_region_screenshot", result)


def catalog(payload: dict, *, publish_response, device_id, get_display_catalog, datetime_module) -> None:
    try:
        data = get_display_catalog()
        state = "success"
    except PermissionError:
        data, state = None, "denied"
    except (ImportError, OSError, ValueError):
        data, state = None, "unsupported"
    result = {
        "type": "display_catalog", "schema": "xider.display-catalog.v1",
        "feature_id": "D02-F06", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": state, "source": "windows_enum_display_monitors",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(),
        "data": data,
    }
    publish_response("display_catalog", result)


def window_catalog(payload: dict, *, publish_response, device_id, get_window_catalog, screenshot_session_available, datetime_module, logger) -> None:
    result = {
        "type": "window_catalog", "schema": "xider.window-catalog.v1",
        "feature_id": "D02-F02", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": "unsupported", "source": "windows_enum_windows",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(), "data": None,
    }
    if not screenshot_session_available():
        result["state"] = "session_unavailable"
    else:
        try:
            result.update(state="success", data=get_window_catalog())
        except PermissionError:
            result["state"] = "denied"
        except (ImportError, OSError):
            result["state"] = "unsupported"
        except ValueError as exc:
            result["state"] = "unsupported" if "no capturable" in str(exc).lower() else "unknown"
        except Exception:
            logger.exception("Windows window catalog failed")
            result["state"] = "unknown"
    publish_response("window_catalog", result)


def window_screenshot(
    payload: dict, *, publish_response, device_id, capture_window,
    screenshot_session_available, base64_module, datetime_module, re_module, logger,
) -> None:
    result = {
        "type": "window_screenshot", "schema": "xider.window-screenshot.v1",
        "feature_id": "D02-F02", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": "invalid_request", "source": "windows_print_window",
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(),
        "data": None, "image": None,
    }
    window_id, catalog_id = payload.get("window_id"), payload.get("catalog_id")
    if not screenshot_session_available():
        result["state"] = "session_unavailable"
    elif isinstance(window_id, str) and isinstance(catalog_id, str) and re_module.fullmatch(r"[0-9a-f]{16}", catalog_id):
        try:
            image, manifest = capture_window(window_id, catalog_id)
            result.update(state="success", data=manifest, image=base64_module.b64encode(image).decode("ascii"))
        except ValueError as exc:
            result["state"] = "stale_catalog" if "catalog" in str(exc).lower() else "invalid_request"
        except PermissionError:
            result["state"] = "denied"
        except (ImportError, OSError):
            result["state"] = "unsupported"
        except Exception:
            logger.exception("Windows selected-window capture failed")
            result["state"] = "unknown"
    publish_response("window_screenshot", result)


def resolution_report(
    payload: dict, *, publish_response, device_id, get_resolution_report,
    datetime_module, re_module, logger,
) -> None:
    display_id, topology_id = payload.get("display_id"), payload.get("topology_id")
    try:
        if (not isinstance(display_id, str) or not re_module.fullmatch(r"display-[1-9][0-9]?", display_id)
                or not isinstance(topology_id, str) or not re_module.fullmatch(r"[0-9a-f]{16}", topology_id)):
            data, state = None, "invalid_request"
        else:
            data = get_resolution_report(display_id, topology_id)
            state = "success"
    except PermissionError:
        data, state = None, "denied"
    except (ImportError, OSError, ValueError):
        data, state = None, "unsupported"
    except Exception:
        logger.exception("Windows display resolution query failed")
        data, state = None, "unknown"
    result = {
        "type": "display_resolution", "schema": "xider.display-resolution.v1",
        "feature_id": "D02-F10", "device_id": device_id,
        "id": payload.get("id"), "request_id": payload.get("id"),
        "state": state, "source": "windows_gdi_monitor_caps",
        "display_id": display_id if isinstance(display_id, str) else None,
        "checked_at": datetime_module.datetime.now(datetime_module.timezone.utc).isoformat(),
        "data": data,
    }
    publish_response("display_resolution", result)


def set_brightness(
    payload: dict, *, clamp, subprocess_module, run_without_console,
    publish_response, device_id, logger,
) -> None:
    """Set monitor brightness and require device readback for confirmation."""
    level = clamp(payload.get("level"), 0, 100, 50)
    ps_flags = getattr(subprocess_module, "CREATE_NO_WINDOW", 0)

    def powershell(script: str):
        return run_without_console(subprocess_module.run,
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8, check=False,
            creationflags=ps_flags,
        )

    def parse_level(value: str) -> int | None:
        lines = str(value or "").strip().splitlines()
        if not lines or not re.fullmatch(r"\s*\d{1,3}\s*", lines[-1]):
            return None
        parsed = int(lines[-1])
        return parsed if 0 <= parsed <= 100 else None

    observed = None
    source = "WMI.WmiMonitorBrightness"
    failure_text = None
    try:
        setter = powershell(
            f"$m=Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods; "
            f"$r=$m.WmiSetBrightness(1, {level}); [Console]::Out.Write([string]$r.ReturnValue)"
        )
        raw_code = (setter.stdout or "").strip()
        if setter.returncode == 0 and re.fullmatch(r"\d+", raw_code) and int(raw_code) == 0:
            readback = powershell(
                "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
            )
            if readback.returncode == 0:
                observed = parse_level(readback.stdout)
        elif setter.returncode == 0 and re.fullmatch(r"\d+", raw_code):
            failure_text = f"WMI returned {raw_code}"
        else:
            failure_text = "WMI did not return a usable method result"
    except Exception as exc:
        failure_text = type(exc).__name__

    if observed == level:
        ok, state = True, "confirmed"
        text = f"🔆 Яркость {observed}%; значение подтверждено системой."
    else:
        fallback_error = None
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(level)
            raw_values = sbc.get_brightness()
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            parsed_values = [parse_level(str(value)) for value in values]
            valid = [value for value in parsed_values if value is not None]
            if valid and len(valid) == len(values) and all(value == level for value in valid):
                observed = valid[0] if len(valid) == 1 else valid
                source = "screen_brightness_control"
            elif valid:
                observed = valid[0] if len(valid) == 1 else valid
                source = "screen_brightness_control"
            else:
                fallback_error = "brightness readback unavailable"
        except Exception as exc:
            fallback_error = type(exc).__name__

        if observed == level or isinstance(observed, list) and observed and all(value == level for value in observed):
            ok, state = True, "confirmed"
            text = f"🔆 Яркость {level}%; значение подтверждено системой."
        elif observed is not None:
            ok, state = False, "failed"
            text = f"⚠️ Запрошено {level}%, но система сообщила другое значение ({observed}); изменение не подтверждено."
        else:
            ok, state = False, "unknown"
            text = "⚠️ Запрос яркости отправлен, но система не вернула проверяемый результат."
        if failure_text:
            logger.debug("WMI brightness request was not confirmed: %s", failure_text)
        if fallback_error:
            logger.debug("Brightness fallback unavailable: %s", fallback_error)

    result = {
        "type": "display_brightness", "device_id": device_id,
        "ok": ok, "state": state, "level": level, "current": observed,
        "source": source, "text": text,
    }
    publish_response("display_brightness", result)
    publish_response("output", result)


def set_night_light(
    payload: dict, *, state_subkey, device_id, publish_response,
    is_enabled, toggle_bytes, logger,
) -> None:
    """Write the CloudStore Night Light flag and report registry readback."""
    import winreg

    wanted = bool(payload.get("enabled", True))
    key = None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, state_subkey, 0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        )
        data, _ = winreg.QueryValueEx(key, "Data")
        current = is_enabled(data)
        if current != wanted:
            winreg.SetValueEx(key, "Data", 0, winreg.REG_BINARY, toggle_bytes(data))
        readback, _ = winreg.QueryValueEx(key, "Data")
        observed = is_enabled(readback)
        if observed is not wanted:
            ok, state = False, "unknown"
            text = "⚠️ Запрос Night Light записан, но повторное чтение реестра не подтвердило нужное состояние."
        else:
            ok, state = True, "success"
            state_text = "включён" if wanted else "выключен"
            text = (
                f"🌙 Значение Night Light в CloudStore {state_text}; "
                "визуальное изменение экрана не проверено."
            )
    except FileNotFoundError:
        ok, state = False, "unsupported"
        text = "⚠️ Ключ Night Light (CloudStore) не найден — функция недоступна на этой системе."
    except Exception as exc:
        ok, state = False, "failed"
        text = f"⚠️ Ошибка проверки/переключения Night Light: {type(exc).__name__}"
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except Exception:
                logger.debug("Night Light registry handle close failed", exc_info=True)
    publish_response("display_night_light", {
        "type": "display_night_light", "device_id": device_id,
        "ok": ok, "state": state, "visual_effect_confirmed": False,
        "enabled": wanted, "observed": wanted if ok else None,
        "source": "HKCU CloudStore Data readback", "text": text,
    })
