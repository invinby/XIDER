"""Local device interaction and control command handlers."""

from __future__ import annotations

from types import FunctionType, MethodType

METHOD_NAMES = {'_do_camera_quality', '_do_audio_output_switch', '_play_beep', '_do_microphone_balance', '_do_display_resolution', '_do_camera_catalog', '_do_microphone_catalog', '_do_screenshot', '_do_camera_series', '_show_message_box', '_do_audio_output_catalog', '_do_mic', '_do_webcam', '_do_media_history', '_do_screen_off', '_do_volume_set', '_do_window_catalog', '_do_clipboard', '_do_microphone_level', '_do_hotkey', '_do_power', '_do_display_night_light', '_do_clipboard_set', '_do_volume_toggle', '_do_display_brightness', '_do_wallpaper_set', '_do_notify', '_speak', '_do_window_screenshot', '_do_lock_screen', '_do_storage_smart', '_do_media_temp_cleanup', '_do_display_catalog', '_do_open_app', '_do_autorun_status', '_do_display_screenshot', '_notify_text', '_do_microphone_preview', '_do_standby_sleep', '_do_display_rotate', '_do_autorun_disable', '_do_type_text', '_do_autorun_enable', '_do_media_cancel', '_do_display_region_screenshot', '_do_display_contact_sheet', '_do_wake', '_do_screensaver_on', '_do_microphone_test', '_do_sound'}


def bind_handler_methods(client, runtime_globals):
    bound = {}
    for name in METHOD_NAMES:
        handler = globals()[name]
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def bind_class_methods(runtime_globals):
    """Expose compatibility methods on XgentClient for class-level callers."""
    bound = {}
    for name in METHOD_NAMES:
        handler = globals()[name]
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = runtime_handler
    return bound


def _do_notify(self, payload: dict) -> None:
    self._notify_text(payload.get("text", ""))


def _notify_text(self, text: str) -> None:
    if not text:
        return
    threading.Thread(target=self._show_message_box, args=(text,), daemon=True).start()


def _show_message_box(self, text: str) -> None:
    ctypes_windll_user32_message_box(text)


def _do_sound(self, payload: dict) -> None:
    text = payload.get("text", "")
    if not text.strip() or text.strip().lower() == "beep":
        self._play_beep()
    else:
        self._speak(text)


def _play_beep(self) -> None:
    winsound.MessageBeep(-1)


def _speak(self, text: str) -> None:
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
    except Exception:
        log.exception("pyttsx3 не сработал — использую системный сигнал")
        winsound.MessageBeep(-1)


def _do_screenshot(self, payload: dict) -> None:
    return _display_commands.screenshot(
        payload, device_id=DEVICE_ID, screenshot_session_available=_screenshot_session_available,
        bounded_screen_grab=_bounded_screen_grab, io_module=io, base64_module=base64,
        max_screenshot_bytes=MAX_SCREENSHOT_BYTES,
        timeout_errors=(TimeoutError, subprocess.TimeoutExpired),
        publish_response=self._publish_response, logger=log,
    )


def _do_media_temp_cleanup(self, payload: dict) -> None:
    report = media_temp.cleanup_stale_media_temp()
    result = {
        "type": "media_temp_cleanup", "schema": "xider.media-temp-cleanup.v1",
        "feature_id": "D02-F23", "device_id": DEVICE_ID,
        "id": payload.get("id"), "request_id": payload.get("id"), "state": report["state"],
        "source": "xider_owned_media_temp_v1",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "data": report,
    }
    self._publish_response("media_temp_cleanup", result)


def _do_media_history(self, payload: dict) -> None:
    import media_history
    return _media_commands.media_history(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        history_path=CONFIG_DIR / "media-history.json", read_events=media_history.read_events,
        datetime_module=datetime, logger=log,
    )


def _do_display_catalog(self, payload: dict) -> None:
    return _display_commands.catalog(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        get_display_catalog=display_contact_sheet.get_display_catalog, datetime_module=datetime,
    )


def _do_display_resolution(self, payload: dict) -> None:
    return _display_commands.resolution_report(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        get_resolution_report=display_contact_sheet.get_resolution_report,
        datetime_module=datetime, re_module=re, logger=log,
    )


def _do_display_contact_sheet(self, payload: dict) -> None:
    return _display_commands.contact_sheet(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        capture_contact_sheet=display_contact_sheet.capture_contact_sheet,
        screenshot_session_available=_screenshot_session_available,
        base64_module=base64, datetime_module=datetime, logger=log,
    )


def _do_display_screenshot(self, payload: dict) -> None:
    return _display_commands.single_display_screenshot(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        capture_single_display=display_contact_sheet.capture_single_display,
        screenshot_session_available=_screenshot_session_available,
        base64_module=base64, datetime_module=datetime, logger=log,
    )


def _do_display_region_screenshot(self, payload: dict) -> None:
    return _display_commands.region_screenshot(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        capture_display_region=display_contact_sheet.capture_display_region,
        screenshot_session_available=_screenshot_session_available,
        base64_module=base64, datetime_module=datetime, logger=log,
    )


def _do_window_catalog(self, payload: dict) -> None:
    return _display_commands.window_catalog(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        get_window_catalog=window_capture.get_window_catalog,
        screenshot_session_available=_screenshot_session_available,
        datetime_module=datetime, logger=log,
    )


def _do_window_screenshot(self, payload: dict) -> None:
    return _display_commands.window_screenshot(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        capture_window=window_capture.capture_window,
        screenshot_session_available=_screenshot_session_available,
        base64_module=base64, datetime_module=datetime, re_module=re, logger=log,
    )


def _do_camera_catalog(self, payload: dict) -> None:
    return _media_commands.camera_catalog(
        payload, list_cameras=camera_devices.list_cameras, device_id=DEVICE_ID,
        publish_response=self._publish_response, datetime_module=datetime, logger=log,
    )


def _do_webcam(self, payload: dict) -> None:
    """Сделать фото с веб-камеры и отправить base64 JPEG в MQTT."""
    return _media_commands.capture_webcam(
        payload, resolve_camera=camera_devices.resolve_camera,
        device_id=DEVICE_ID, publish_response=self._publish_response, logger=log,
    )


def _do_camera_series(self, payload: dict) -> None:
    return _media_commands.camera_series_snapshot(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        finish_request=MEDIA_REQUESTS.finish, datetime_module=datetime, logger=log,
    )


def _do_media_cancel(self, payload: dict) -> None:
    request_id = payload.get("request_id")
    state = MEDIA_REQUESTS.cancel(request_id)
    self._publish_response("media_cancel", {
        "type": "media_cancel_result", "schema": "xider.media-cancel.v1",
        "feature_id": "D02-F22", "device_id": DEVICE_ID,
        "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
        "state": state, "source": "agent_process_local",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    })


def _do_camera_quality(self, payload: dict) -> None:
    """Analyze one explicitly requested camera frame locally; never send pixels."""
    return _media_commands.camera_quality_snapshot(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        analysis_source=camera_quality.ANALYSIS_SOURCE,
        analyze_frame=camera_quality.analyze_frame, datetime_module=datetime, logger=log,
    )


def _do_clipboard(self, payload: dict) -> None:
    """Чтение буфера обмена (без записи)."""
    return _input_commands.clipboard(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        logger=log,
    )


def _do_clipboard_set(self, payload: dict) -> None:
    """Записать текст в буфер обмена."""
    return _input_commands.clipboard_set(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        logger=log,
    )


def _do_screen_off(self, payload: dict) -> None:
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
    log.info("Монитор выключен")


def _do_screensaver_on(self, payload: dict) -> None:
    ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF140, 0)
    log.info("Screensaver запущен")


def _do_volume_set(self, payload: dict) -> None:
    """Установить общую громкость 0-100 (pycaw)."""
    return _audio_commands.set_master_volume(
        payload, get_master_volume=self._get_master_volume,
        publish_response=self._publish_response, device_id=DEVICE_ID, logger=log,
    )


def _do_microphone_catalog(self, payload: dict) -> None:
    return _audio_commands.microphone_catalog(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        datetime_module=datetime, logger=log,
    )


def _do_microphone_level(self, payload: dict) -> None:
    return _audio_commands.microphone_level(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        datetime_module=datetime, logger=log,
    )


def _do_microphone_balance(self, payload: dict) -> None:
    return _audio_commands.microphone_balance(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        datetime_module=datetime, logger=log,
    )


def _do_audio_output_catalog(self, payload: dict) -> None:
    return _audio_commands.output_catalog(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        datetime_module=datetime, logger=log,
    )


def _do_audio_output_switch(self, payload: dict) -> None:
    return _audio_commands.switch_output(
        payload, publish_response=self._publish_response, device_id=DEVICE_ID,
        datetime_module=datetime, logger=log,
    )


def _do_microphone_test(self, payload: dict) -> None:
    return _media_commands.microphone_test(
        payload, device_id=DEVICE_ID,
        publish_response=self._publish_response, logger=log,
    )


def _do_microphone_preview(self, payload: dict) -> None:
    return _media_commands.microphone_preview(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        datetime_module=datetime, logger=log,
    )


def _do_mic(self, payload: dict) -> None:
    """Запись 5 секунд с микрофона по умолчанию, base64 ogg в MQTT."""
    return _media_commands.capture_microphone(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        media_temp_module=media_temp, logger=log,
    )


def _do_hotkey(self, payload: dict) -> None:
    return _input_commands.hotkey(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        map_hotkeys=map_hotkeys, ctypes_module=ctypes, threading_module=threading,
    )


def _do_wallpaper_set(self, payload: dict) -> None:
    """Установка обоев: по URL, из загруженного фото (b64) или случайного мема из сети."""
    import urllib.request
    return _display_commands.wallpaper_set(
        payload, device_id=DEVICE_ID, config_dir=CONFIG_DIR,
        ctypes_module=ctypes, os_module=os, tempfile_module=tempfile,
        base64_module=base64, random_module=random, request_module=urllib.request,
        open_file=open, publish_response=self._publish_response, logger=log,
    )


def _do_type_text(self, payload: dict) -> None:
    """Напечатать текст на удалённой машине (SendInput Unicode)."""
    return _input_commands.type_text(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        ctypes_module=ctypes, time_module=time, threading_module=threading,
    )


def _do_open_app(self, payload: dict) -> None:
    """Запустить программу или открыть файл. Не использует `shell=True`."""
    return _desktop_commands.open_app(
        payload, startfile=lambda app: os.startfile(app),
        subprocess_module=subprocess, logger=log,
    )


def _do_lock_screen(self, payload: dict) -> None:
    """Заблокировать экран Windows."""
    return _desktop_commands.lock_screen(
        payload, device_id=DEVICE_ID,
        lock_workstation=ctypes.windll.user32.LockWorkStation,
        publish_response=self._publish_response, logger=log,
    )


def _do_volume_toggle(self, payload: dict) -> None:
    """Mute/Unmute системного звука Windows через pycaw."""
    return _audio_commands.toggle_mute(
        get_master_volume=self._get_master_volume,
        publish_response=self._publish_response, device_id=DEVICE_ID, logger=log,
    )


def _do_power(self, payload: dict) -> None:
    """Выключить, перезагрузить или отправить машину в сон."""
    return _power_commands.power(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        logger=log, subprocess_module=subprocess, time_module=time, ctypes_module=ctypes,
        run_without_console=_run_without_console, threading_module=threading,
    )


def _do_display_brightness(self, payload: dict) -> None:
    """Установить яркость экрана 0-100 через WMI WmiSetBrightness."""
    return _display_commands.set_brightness(
        payload, clamp=_clamp, subprocess_module=subprocess,
        run_without_console=_run_without_console, publish_response=self._publish_response,
        device_id=DEVICE_ID, logger=log,
    )


def _do_display_night_light(self, payload: dict) -> None:
    """Вкл/выкл «Ночной свет» через CloudStore-реестр (winreg)."""
    return _display_commands.set_night_light(
        payload, state_subkey=NL_STATE_SUBKEY, device_id=DEVICE_ID,
        publish_response=self._publish_response,
        is_enabled=_night_light_enabled, toggle_bytes=_night_light_toggle_bytes,
        logger=log,
    )


def _do_display_rotate(self, payload: dict) -> None:
    """Повернуть экран: angle 0/90/180/270 через ChangeDisplaySettingsEx."""
    angle = payload.get("angle", 0)
    ok, text = _rotate_screen(angle)
    self._publish_response("display_rotate", {
        "type": "display_rotate", "device_id": DEVICE_ID,
        "ok": ok, "angle": angle, "text": text,
    })


def _do_storage_smart(self, payload: dict) -> None:
    """Здоровье дисков (SMART) через Get-PhysicalDisk/Get-StorageReliabilityCounter."""
    return _storage_commands.storage_smart(
        subprocess_module=subprocess, run_without_console=_run_without_console,
        publish_response=self._publish_response, device_id=DEVICE_ID,
    )


def _do_standby_sleep(self, payload: dict) -> None:
    """Перевод агента в спящий режим ожидания (Watchdog)."""
    self._standby = True
    self._publish_status()
    text = "💤 Агент переведён в спящий режим (Standby).\nБольшинство функций приостановлено. Для возобновления используйте кнопку «☀️ Пробудить агента»."
    self._publish_response("output", {
        "type": "standby_sleep", "device_id": DEVICE_ID, "ok": True, "text": text,
    })


def _do_wake(self, payload: dict) -> None:
    """Пробуждение агента из спящего режима."""
    self._standby = False
    self._publish_status()
    text = "☀️ Агент успешно пробуждён и готов к приёму всех команд!"
    self._publish_response("output", {
        "type": "wake", "device_id": DEVICE_ID, "ok": True, "text": text,
    })


def _do_autorun_status(self, payload: dict) -> None:
    """Проверка фактического Scheduled Task автозапуска."""
    return _startup_commands.autorun_status(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        config_dir=CONFIG_DIR, task_exists=_scheduled_task_exists, task_name=WINDOWS_TASK_NAME,
    )


def _do_autorun_enable(self, payload: dict) -> None:
    """Enable the pre-registered visible Scheduled Task without source helpers."""
    return _startup_commands.autorun_enable(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        task_exists=_scheduled_task_exists, task_name=WINDOWS_TASK_NAME,
        run_without_console=_run_without_console, run=subprocess.run,
        set_startup_enabled=set_guardian_startup_enabled,
    )


def _do_autorun_disable(self, payload: dict) -> None:
    """Отключить Scheduled Task и убрать старый Registry-вариант."""
    return _startup_commands.autorun_disable(
        payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        task_exists=_scheduled_task_exists, task_name=WINDOWS_TASK_NAME,
        run_without_console=_run_without_console, run=subprocess.run,
        winreg_module=winreg, set_startup_enabled=set_guardian_startup_enabled,
        logger=log,
    )
