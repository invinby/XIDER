"""Explicitly requested, local prank command handlers."""

from __future__ import annotations

from types import FunctionType, MethodType


def bind_handler_methods(client, runtime_globals):
    """Bind moved legacy adapters to their original runtime namespace."""
    bound = {}
    for name, handler in globals().items():
        if not name.startswith("_do_prank_") or not isinstance(handler, FunctionType):
            continue
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def confetti_winner(
    payload: dict, *, device_id, publish_response, tempfile_module, temp_prefix,
    schedule_temp_cleanup, subprocess_module,
) -> None:
    try:
        html = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>WINNER!</title>
<style>body{background:#FFD700;color:#8B0000;font-family:sans-serif;text-align:center;padding-top:100px;overflow:hidden;}
h1{font-size:55px;animation:blink 0.5s infinite alternate;}@keyframes blink{from{opacity:1;}to{opacity:0.3;}}
p{font-size:28px;font-weight:bold;}
</style></head><body><h1>🎉 ПОЗДРАВЛЯЕМ! ВЫ ВЫИГРАЛИ 1,000,000 $ ! 🎉</h1>
<p>Вы стали миллионным посетителем этого компьютера!<br>Деньги уже переводятся на ваш стол!</p>
<script>
let a=new AudioContext();
function b(){let o=a.createOscillator();o.connect(a.destination);o.frequency.value=800;o.start();setTimeout(()=>o.stop(),200);}
setInterval(b,600);
setTimeout(()=>window.close(),15000);
</script></body></html>"""
        tf = tempfile_module.NamedTemporaryFile(
            "w", prefix=temp_prefix, suffix=".html", delete=False, encoding="utf-8"
        )
        tf.write(html)
        tf.close()
        schedule_temp_cleanup(tf.name)
        subprocess_module.Popen(f'explorer "{tf.name}"', shell=True)
        ok, text = True, "🎰 Экран с выигрышем $1,000,000 открыт!"
    except Exception as exc:
        ok, text = False, f"⚠️ Ошибка: {exc}"
    publish_response("output", {"type": "prank_confetti_winner", "device_id": device_id, "ok": ok, "text": text})


def open_browser_memes(payload: dict, *, device_id, publish_response, webbrowser_module, time_module) -> None:
    try:
        urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://cat-bounce.com/",
            "https://heeeeeeeey.com/",
        ]
        for url in urls:
            webbrowser_module.open_new_tab(url)
            time_module.sleep(0.3)
        text = "🌐 3 вкладки с легендарными мемами открыты в браузере!"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка браузера: {exc}"
        ok = False
    publish_response("output", {"type": "prank_open_browser_memes", "device_id": device_id, "ok": ok, "text": text})


def fake_virus(payload: dict, *, device_id, publish_response, ctypes_module, time_module, threading_module) -> None:
    def alerts():
        user32 = ctypes_module.windll.user32
        user32.MessageBoxW(None, "Обнаружен критический троян: Win32.Pivko.Trojan!\nВсе запасы пива под угрозой.", "Windows Defender Security Alert", 0x10)
        time_module.sleep(0.4)
        user32.MessageBoxW(None, "Попытка нейтрализации...\nОшибка: Троян слишком весёлый.", "Windows Defender", 0x30)
        time_module.sleep(0.4)
        user32.MessageBoxW(None, "Шутка! Ваша система в полной безопасности. 😉", "XGENT Prank", 0x40)
    threading_module.Thread(target=alerts, daemon=True).start()
    publish_response("output", {"type": "prank_fake_virus", "device_id": device_id, "ok": True, "text": "🦠 Шуточные предупреждения о вирусе показаны пользователю!"})


def open_cd(payload: dict, *, device_id, publish_response, ctypes_module, winsound_module) -> None:
    try:
        winmm = ctypes_module.windll.winmm
        winmm.mciSendStringW("set cdaudio door open", None, 0, None)
        text = "💿 Команда открытия CD-ROM отправлена!"
        ok = True
    except Exception as exc:
        winsound_module.Beep(800, 400)
        text = f"💿 CD-ROM физически не обнаружен (подан звуковой сигнал): {exc}"
        ok = True
    publish_response("output", {"type": "prank_open_cd", "device_id": device_id, "ok": ok, "text": text})


def fake_error_spam(payload: dict, *, device_id, publish_response, time_module, threading_module,
                    winsound_module=None) -> None:
    def beep_spam():
        for _ in range(12):
            try:
                if winsound_module is None:
                    import winsound as sound_module
                else:
                    sound_module = winsound_module
                sound_module.MessageBeep(0x00000010)
            except Exception:
                pass
            time_module.sleep(0.18)
    threading_module.Thread(target=beep_spam, daemon=True).start()
    publish_response("output", {"type": "prank_fake_error_spam", "device_id": device_id, "ok": True, "text": "🚨 Спам звуками критической ошибки Windows запущен!"})


def fake_delete_sys32(payload: dict, *, device_id, publish_response, subprocess_module) -> None:
    try:
        cmd = (
            'start "System Integrity Service" cmd /k "'
            'color 0A & echo [CRITICAL] Deleting Windows system files... & timeout /t 1 >nul & '
            'dir /s C:\\Windows\\System32 & cls & color 0C & '
            'echo [FATAL ERROR] 14,291 system files permanently purged! & '
            'echo System will halt in 5 seconds... & timeout /t 4 >nul & '
            'color 0A & echo ============================================= & '
            'echo    JUST A PRANK BY XGENT! EVERYTHING IS SAFE :) & '
            'echo ============================================= & '
            'timeout /t 6 >nul & exit"'
        )
        subprocess_module.Popen(cmd, shell=True)
        ok, text = True, "💻 Фейковое удаление System32 запущено в терминале!"
    except Exception as exc:
        ok, text = False, f"⚠️ Ошибка: {exc}"
    publish_response("output", {"type": "prank_fake_delete_sys32", "device_id": device_id, "ok": ok, "text": text})


def fbi_lock(payload: dict, *, device_id, publish_response, ctypes_module, threading_module) -> None:
    def show_warning():
        msg = (
            "ВНИМАНИЕ! Ваш IP-адрес зафиксирован Федеральной Службой Безопасности.\n\n"
            "Зафиксирована подозрительная активность.\n"
            "Веб-камера и микрофон переведены в режим протоколирования доказательств.\n"
            "Оставайтесь на месте до прибытия оперативной группы."
        )
        ctypes_module.windll.user32.MessageBoxW(None, msg, "⚠️ ФЕДЕРАЛЬНОЕ ПРЕДУПРЕЖДЕНИЕ", 0x10 | 0x0)
    threading_module.Thread(target=show_warning, daemon=True).start()
    publish_response("output", {"type": "prank_fbi_lock", "device_id": device_id, "ok": True, "text": "🚨 Предупреждение от спецслужб выведено на экран!"})


def cat_invaders(payload: dict, *, device_id, publish_response, webbrowser_module, time_module) -> None:
    try:
        urls = [
            "https://cataas.com/cat/says/HELLO%20HUMAN",
            "https://cataas.com/cat/gif",
            "https://cataas.com/cat",
        ]
        for url in urls:
            webbrowser_module.open(url)
            time_module.sleep(0.3)
        ok, text = True, "🐱 Нашествие котиков открыто в браузере!"
    except Exception as exc:
        ok, text = False, f"⚠️ Ошибка: {exc}"
    publish_response("output", {"type": "prank_cat_invaders", "device_id": device_id, "ok": ok, "text": text})


def fake_update(payload: dict, *, device_id, publish_response, threading_module, time_module,
                register_prank_cancel, finish_prank_cancel, logger) -> None:
    """Run the cancellable, local-only 22-second update demo."""
    cancel_event = threading_module.Event()
    root_ref: dict[str, object] = {}

    def request_close() -> None:
        cancel_event.set()
        root = root_ref.get("root")
        if root is not None:
            try:
                root.after(0, root.destroy)
            except Exception:
                pass

    scene_id = f"fake_update:{id(cancel_event)}"
    register_prank_cancel(scene_id, request_close)

    def update_screen():
        try:
            import tkinter as tk
            if cancel_event.is_set():
                return
            root = tk.Tk()
            root_ref["root"] = root
            root.title("XIDER demo")
            root.attributes("-fullscreen", True)
            root.attributes("-topmost", True)
            root.configure(bg="#000000")
            closed = False
            started = time_module.monotonic()

            def close(_event=None):
                nonlocal closed
                if closed:
                    return
                closed = True
                cancel_event.set()
                try:
                    root.destroy()
                except tk.TclError:
                    pass

            root.bind("<Escape>", close)
            root.bind("<Double-Button-1>", close)
            root.protocol("WM_DELETE_WINDOW", close)

            frame = tk.Frame(root, bg="#000000")
            frame.place(relx=0.5, rely=0.5, anchor="center")

            spinner = tk.Canvas(frame, width=80, height=80, bg="#000000", highlightthickness=0)
            spinner.pack(pady=20)

            lbl_title = tk.Label(frame, text="Работа с обновлениями", font=("Segoe UI", 26), fg="white", bg="#000000")
            lbl_title.pack(pady=(10, 5))

            lbl_demo = tk.Label(
                frame, text="ДЕМО · Esc закрывает · 22с",
                font=("Segoe UI", 16, "bold"), fg="#70d6ff", bg="#000000",
            )
            lbl_demo.pack(pady=5)

            lbl_sub = tk.Label(frame, text="Это локальная сценка: обновление и перезагрузка системы не запускаются.\nЭкран закроется автоматически; выйти можно клавишей Esc.", font=("Segoe UI Light", 14), fg="#cccccc", bg="#000000", justify="center")
            lbl_sub.pack(pady=(15, 0))

            angle = 0
            def spin():
                nonlocal angle
                if closed:
                    return
                spinner.delete("all")
                import math
                cx, cy, r = 40, 40, 25
                for i in range(6):
                    a = math.radians(angle + i * 35)
                    x = cx + r * math.cos(a)
                    y = cy + r * math.sin(a)
                    dot_r = 3 + i * 0.8
                    spinner.create_oval(x - dot_r, y - dot_r, x + dot_r, y + dot_r, fill="white", outline="")
                angle = (angle + 18) % 360
                root.after(40, spin)

            def countdown():
                if closed:
                    return
                remaining = max(0, 22 - int(time_module.monotonic() - started))
                lbl_demo.config(text=f"ДЕМО · Esc закрывает · {remaining}с")
                if remaining == 0:
                    close()
                else:
                    root.after(250, countdown)

            spin()
            countdown()
            root.mainloop()
        except Exception as exc:
            logger.error("Fake update error: %s", exc)
        finally:
            finish_prank_cancel(scene_id)

    threading_module.Thread(target=update_screen, daemon=True, name="xgent-fakeupdate").start()
    publish_response("output", {"type": "prank_fake_update", "device_id": device_id, "ok": True, "text": "⏳ Демо-экран запущен на 22 секунды. Закрыть сразу: Esc или двойной щелчок."})


def paste_clipboard_spam(payload: dict, *, device_id, publish_response, clipboard_module=None) -> None:
    try:
        if clipboard_module is None:
            import pyperclip as clipboard_module
        meme = "( ͡° ͜ʖ ͡°) Взлом жопы завершен на 99.9%"
        clipboard_module.copy(meme)
        text = f"📋 В буфер обмена подложен мем:\n'{meme}'"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка: {exc}"
        ok = False
    publish_response("output", {"type": "prank_paste_clipboard_spam", "device_id": device_id, "ok": ok, "text": text})


def type_reversed(payload: dict, *, device_id, publish_response, clipboard_module=None) -> None:
    try:
        if clipboard_module is None:
            import pyperclip as clipboard_module
        current = clipboard_module.paste() or "Привет мир"
        reversed_text = current[::-1]
        clipboard_module.copy(reversed_text)
        text = f"🔄 Буфер обмена инвертирован задом наперёд:\n{reversed_text[:100]}"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка: {exc}"
        ok = False
    publish_response("output", {"type": "prank_type_reversed", "device_id": device_id, "ok": ok, "text": text})


def change_wallpaper(payload: dict, *, device_id, publish_response, config_dir, winreg_module,
                     ctypes_module, image_module=None) -> None:
    try:
        key_path = r"Control Panel\Desktop"
        with winreg_module.OpenKey(winreg_module.HKEY_CURRENT_USER, key_path, 0, winreg_module.KEY_READ) as key:
            current_wallpaper, _ = winreg_module.QueryValueEx(key, "WallPaper")
            with open(config_dir / "backup_wallpaper.txt", "w", encoding="utf-8") as backup:
                backup.write(current_wallpaper)
        if image_module is None:
            from PIL import Image, ImageDraw
        else:
            Image, ImageDraw = image_module.Image, image_module.ImageDraw
        image = Image.new("RGB", (1920, 1080), color=(15, 20, 35))
        draw = ImageDraw.Draw(image)
        draw.text((650, 520), "HACKED BY XGENT :)\n(Все ваши печеньки принадлежат нам)", fill=(0, 255, 120))
        wallpaper_path = config_dir / "prank_wall.bmp"
        image.save(wallpaper_path, "BMP")
        ctypes_module.windll.user32.SystemParametersInfoW(20, 0, str(wallpaper_path), 3)
        text, ok = "🖼️ Мемные обои установлены (исходные сохранены для восстановления)!", True
    except Exception as exc:
        text, ok = f"⚠️ Ошибка установки обоев: {exc}", False
    publish_response("output", {"type": "prank_change_wallpaper", "device_id": device_id, "ok": ok, "text": text})


def restore_wallpaper(*, config_dir, ctypes_module, os_module, logger) -> tuple[bool, str]:
    """Restore a saved wallpaper without publishing a command response."""
    try:
        backup_path = config_dir / "backup_wallpaper.txt"
        if backup_path.exists():
            with open(backup_path, "r", encoding="utf-8") as backup:
                original_wallpaper = backup.read().strip()
            if original_wallpaper and os_module.path.exists(original_wallpaper):
                ctypes_module.windll.user32.SystemParametersInfoW(20, 0, original_wallpaper, 3)
                return True, "✅ Исходные обои восстановлены."
            return False, "ℹ️ Исходный файл обоев не найден на диске."
        return False, "ℹ️ Резервная копия обоев не найдена."
    except Exception as exc:
        logger.exception("Не удалось восстановить сохранённые обои")
        return False, f"⚠️ Ошибка восстановления обоев: {type(exc).__name__}."


def restore_wallpaper_command(payload: dict, *, device_id, publish_response, restore) -> None:
    ok, text = restore()
    publish_response("output", {"type": "prank_restore_wallpaper", "device_id": device_id, "ok": ok, "text": text})


def rickroll_terminal(payload: dict, *, device_id, publish_response, subprocess_module) -> None:
    try:
        subprocess_module.Popen(["start", "cmd", "/k", "curl -s parrot.live || curl -s ascii.live/rick"], shell=True)
        text, ok = "🕺 Терминал с ASCII анимацией запущен на экране!", True
    except Exception as exc:
        text, ok = f"⚠️ Ошибка запуска терминала: {exc}", False
    publish_response("output", {"type": "prank_rickroll_terminal", "device_id": device_id, "ok": ok, "text": text})


def screen_off_brief(payload: dict, *, device_id, publish_response, ctypes_module, time_module,
                     threading_module) -> None:
    def dark():
        user32 = ctypes_module.windll.user32
        user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        time_module.sleep(3)
        user32.mouse_event(0x0001, 1, 1, 0, 0)
    threading_module.Thread(target=dark, daemon=True).start()
    publish_response("output", {"type": "prank_screen_off_brief", "device_id": device_id, "ok": True, "text": "🌑 Экран временно погашен на 3 секунды!"})


def glitch_cursor(payload: dict, *, device_id, publish_response, ctypes_module, time_module,
                  threading_module) -> None:
    import math

    def glitch():
        user32 = ctypes_module.windll.user32
        class POINT(ctypes_module.Structure):
            _fields_ = [("x", ctypes_module.c_long), ("y", ctypes_module.c_long)]
        point = POINT()
        user32.GetCursorPos(ctypes_module.byref(point))
        cx, cy = point.x, point.y
        for i in range(50):
            angle = i * 0.25
            x = int(cx + 100 * math.sin(angle))
            y = int(cy + 60 * math.sin(angle * 2))
            user32.SetCursorPos(x, y)
            time_module.sleep(0.1)
    threading_module.Thread(target=glitch, daemon=True).start()
    publish_response("output", {"type": "prank_glitch_cursor", "device_id": device_id, "ok": True, "text": "🌀 Глючный курсор (траектория 'восьмёрка') кружится 5 секунд!"})


def cursor_circle(payload: dict, *, device_id, publish_response, ctypes_module, time_module,
                  threading_module) -> None:
    def circle():
        user32 = ctypes_module.windll.user32
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)
        cx, cy = screen_width // 2, screen_height // 2
        radius = min(screen_width, screen_height) // 3
        import math as math_module
        for step in range(80):
            angle = step * 0.15
            x = int(cx + radius * math_module.cos(angle))
            y = int(cy + radius * math_module.sin(angle))
            user32.SetCursorPos(x, y)
            time_module.sleep(0.05)
    threading_module.Thread(target=circle, daemon=True).start()
    publish_response("output", {"type": "prank_cursor_circle", "device_id": device_id, "ok": True, "text": "🌀 Курсор мыши закручен в гипнотический круг на 5 сек!"})


def nyan_stream(payload: dict, *, device_id, publish_response, webbrowser_module) -> None:
    try:
        webbrowser_module.open("https://www.youtube.com/watch?v=QH2-TGUlwu4")
        ok, text = True, "🌈 Nyan Cat с радугой и музыкой запущен в браузере!"
    except Exception as exc:
        ok, text = False, f"⚠️ Ошибка: {exc}"
    publish_response("output", {"type": "prank_nyan_stream", "device_id": device_id, "ok": ok, "text": text})


def low_battery_fake(payload: dict, *, device_id, publish_response, ctypes_module, threading_module) -> None:
    def show_alert():
        message = "Уровень заряда батареи: 0%!\nКомпьютер будет выключен через 15 секунд для предотвращения потери данных."
        ctypes_module.windll.user32.MessageBoxW(None, message, "Критический разряд аккумулятора", 0x30 | 0x0)
    threading_module.Thread(target=show_alert, daemon=True).start()
    publish_response("output", {"type": "prank_low_battery_fake", "device_id": device_id, "ok": True, "text": "🪫 Оповещение о 0% батареи показано на экране!"})


def stop_all(payload: dict, *, device_id, publish_response, cancel_active_pranks,
             restore_wallpaper, logger) -> None:
    """Restore only prank state for which XIDER has an explicit saved original."""
    logger.info("Экстренная остановка всех приколов (prank_stop_all)...")
    cancel_requests = cancel_active_pranks()
    # Wallpaper is the only prank here with an explicit persisted original.
    # Never guess user defaults for mouse, display orientation, icons, or night light.
    _wallpaper_restored, wallpaper = restore_wallpaper()
    publish_response("output", {
        "type": "prank_stop_all",
        "device_id": device_id,
        "ok": False,
        "state": "partial",
        "text": (
            f"🛑 Запрос отмены отправлен для сценок: {len(cancel_requests)}. {wallpaper} "
            "Результат частичный: не все старые эффекты поддерживают отмену. "
            "Остальные параметры системы не сбрасывал: "
            "короткие эффекты завершаются по своим таймерам, "
            "а для полной отмены каждой сцены нужен отдельный cancel-handler."
        ),
    })


def speak_time(payload: dict, *, device_id, publish_response, time_module, threading_module,
               speech_engine_module=None) -> None:
    now = time_module.strftime("%H часов %M минут")
    phrase = f"Внимание! Точное время на вашей планете: {now}. Не забудьте сделать разминку!"

    def speak():
        try:
            if speech_engine_module is None:
                import pyttsx3
                engine_module = pyttsx3
            else:
                engine_module = speech_engine_module
            engine = engine_module.init()
            engine.setProperty("rate", 140)
            engine.say(phrase)
            engine.runAndWait()
        except Exception:
            pass
    threading_module.Thread(target=speak, daemon=True).start()
    publish_response("output", {"type": "prank_speak_time", "device_id": device_id, "ok": True, "text": f"🗣️ Время озвучено: '{phrase}'"})


def say_whisper(payload: dict, *, device_id, publish_response, threading_module,
                speech_engine_module=None) -> None:
    phrase = payload.get("text") or "Обернись... Ты здесь совсем не один..."

    def speak():
        try:
            if speech_engine_module is None:
                import pyttsx3
                engine_module = pyttsx3
            else:
                engine_module = speech_engine_module
            engine = engine_module.init()
            engine.setProperty("rate", 90)
            engine.setProperty("volume", 0.7)
            engine.say(phrase)
            engine.runAndWait()
        except Exception:
            pass
    threading_module.Thread(target=speak, daemon=True).start()
    publish_response("output", {"type": "prank_say_whisper", "device_id": device_id, "ok": True, "text": f"🗣️ Шёпот воспроизведён: '{phrase}'"})


def laugh_track(payload: dict, *, device_id, publish_response, threading_module,
                logger, speech_engine_module=None) -> None:
    def laugh():
        try:
            if speech_engine_module is None:
                import pyttsx3
                engine_module = pyttsx3
            else:
                engine_module = speech_engine_module
            engine = engine_module.init()
            engine.setProperty("rate", 140)
            engine.setProperty("volume", 1.0)
            engine.say("Ха ха ха ха! Мва ха ха ха ха! Я контролирую этот компьютер!")
            engine.runAndWait()
        except Exception as exc:
            logger.error("Laugh track error: %s", exc)
    threading_module.Thread(target=laugh, daemon=True).start()
    publish_response("output", {"type": "prank_laugh_track", "device_id": device_id, "ok": True, "text": "😈 Зловещий смех запущен через динамики!"})


def random_beeps(payload: dict, *, device_id, publish_response, random_module,
                 time_module, threading_module, winsound_module=None) -> None:
    def beeps():
        for _ in range(8):
            try:
                if winsound_module is None:
                    import winsound
                    sound_module = winsound
                else:
                    sound_module = winsound_module
                frequency = random_module.randint(500, 2200)
                duration = random_module.randint(80, 250)
                sound_module.Beep(frequency, duration)
            except Exception:
                pass
            time_module.sleep(random_module.uniform(0.1, 0.4))
    threading_module.Thread(target=beeps, daemon=True).start()
    publish_response("output", {"type": "prank_random_beeps", "device_id": device_id, "ok": True, "text": "🔊 Серия случайных звуковых сигналов ПК запущена!"})


def caps_disco(payload: dict, *, device_id, publish_response, ctypes_module, time_module, threading_module) -> None:
    def disco():
        user32 = ctypes_module.windll.user32
        for _ in range(16):
            user32.keybd_event(0x14, 0, 0, 0)
            user32.keybd_event(0x14, 0, 2, 0)
            user32.keybd_event(0x90, 0, 0, 0)
            user32.keybd_event(0x90, 0, 2, 0)
            time_module.sleep(0.18)
    threading_module.Thread(target=disco, daemon=True).start()
    publish_response("output", {"type": "prank_caps_disco", "device_id": device_id, "ok": True, "text": "✨ Дискотека светодиодов клавиатуры запущена!"})


def alert_loop(payload: dict, *, device_id, publish_response, ctypes_module, time_module, threading_module) -> None:
    def loop():
        user32 = ctypes_module.windll.user32
        user32.MessageBoxW(None, "Хотите ли вы удалить Интернет для освобождения памяти?", "Система Windows", 0x24)
        time_module.sleep(0.3)
        user32.MessageBoxW(None, "Вы уверены? Это действие невозможно отменить!", "Система Windows", 0x34)
        time_module.sleep(0.3)
        user32.MessageBoxW(None, "Отмена операции: Интернет слишком тяжелый.", "Система Windows", 0x40)
    threading_module.Thread(target=loop, daemon=True).start()
    publish_response("output", {"type": "prank_alert_loop", "device_id": device_id, "ok": True, "text": "❓ Шуточная цепочка диалоговых окон показана пользователю!"})


def ghost_typer(payload: dict, *, device_id, publish_response, ctypes_module, random_module,
                time_module, threading_module, logger) -> None:
    def ghost():
        time_module.sleep(1.0)
        phrases = [
            "   👻 Привет... Я живу в твоем кулере...   ",
            "   👀 Не оборачивайся... Сзади кто-то стоит...   ",
            "   👽 Твой ПК захвачен внеземным разумом...   ",
        ]
        text = random_module.choice(phrases)
        try:
            user32 = ctypes_module.windll.user32
            for ch in text:
                code = ord(ch)
                user32.keybd_event(0, code, 0x0004, 0)
                user32.keybd_event(0, code, 0x0004 | 0x0002, 0)
                time_module.sleep(0.06)
        except Exception as exc:
            logger.error("Ghost typer error: %s", exc)
    threading_module.Thread(target=ghost, daemon=True).start()
    publish_response("output", {"type": "prank_ghost_typer", "device_id": device_id, "ok": True, "text": "👻 Призрачный набор текста активирован в активном окне!"})


def volume_jump(payload: dict, *, device_id, publish_response, get_master_volume,
                time_module, threading_module) -> None:
    def jump():
        try:
            volume = get_master_volume()
            original = volume.GetMasterVolumeLevelScalar()
            for _ in range(4):
                volume.SetMasterVolumeLevelScalar(1.0, None)
                time_module.sleep(0.3)
                volume.SetMasterVolumeLevelScalar(0.1, None)
                time_module.sleep(0.3)
            volume.SetMasterVolumeLevelScalar(original, None)
        except Exception:
            pass
    threading_module.Thread(target=jump, daemon=True).start()
    publish_response("output", {"type": "prank_volume_jump", "device_id": device_id, "ok": True, "text": "🔊 Скачки громкости (100% <-> 10%) запущены!"})


def toast_spam(payload: dict, *, device_id, publish_response, subprocess_module) -> None:
    try:
        ps = (
            "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null; "
            "$notify = New-Object System.Windows.Forms.NotifyIcon; "
            "$notify.Icon = [System.Drawing.SystemIcons]::Information; "
            "$notify.Visible = $True; "
            "1..5 | ForEach-Object { "
            "  $notify.ShowBalloonTip(3000, 'Внимание системы #'+$_, 'Зафиксировано вторжение космических котов!', [System.Windows.Forms.ToolTipIcon]::Warning); "
            "  Start-Sleep -Milliseconds 700 "
            "}; $notify.Dispose()"
        )
        subprocess_module.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        text = "🔔 Серия из 5 фейковых системных уведомлений запущена в трее!"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка уведомлений: {exc}"
        ok = False
    publish_response("output", {"type": "prank_toast_spam", "device_id": device_id, "ok": ok, "text": text})


def keyboard_disco(
    payload: dict, *, device_id, publish_response, ctypes_module, time_module, threading_module,
) -> None:
    def disco():
        user32 = ctypes_module.windll.user32
        keys = [0x14, 0x90, 0x91]
        for _ in range(12):
            for key in keys:
                user32.keybd_event(key, 0x45, 1, 0)
                user32.keybd_event(key, 0x45, 1 | 2, 0)
                time_module.sleep(0.12)
    threading_module.Thread(target=disco, daemon=True).start()
    publish_response("output", {"type": "prank_keyboard_disco", "device_id": device_id, "ok": True, "text": "💃 Дискотека клавиатуры (индикаторы Caps/Num/Scroll) активирована!"})


def beep_morse(payload: dict, *, device_id, publish_response, winsound_module, time_module, threading_module) -> None:
    def morse():
        pattern = [(1000, 150)] * 3 + [(1000, 400)] * 3 + [(1000, 150)] * 3
        for freq, duration in pattern:
            try:
                winsound_module.Beep(freq, duration)
                time_module.sleep(0.08)
            except Exception:
                pass
    threading_module.Thread(target=morse, daemon=True).start()
    publish_response("output", {"type": "prank_beep_morse", "device_id": device_id, "ok": True, "text": "📻 Сигнал Морзе (S.O.S) воспроизведён в динамиках!"})


def open_notepad_type(payload: dict, *, device_id, publish_response, subprocess_module) -> None:
    msg = payload.get("text") or "Привет... Я слежу за тобой через розетку 👁️"
    clean_msg = msg.replace("'", " ").replace('"', " ")
    ps = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"Start-Process notepad.exe; "
        f"Start-Sleep -Milliseconds 800; "
        f"$ws.AppActivate('Notepad'); "
        f"Start-Sleep -Milliseconds 400; "
        f"'{clean_msg}'.ToCharArray() | ForEach-Object {{ "
        f"  $ws.SendKeys($_); Start-Sleep -Milliseconds 120 "
        f"}}"
    )
    subprocess_module.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
        creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
    )
    publish_response("output", {"type": "prank_open_notepad_type", "device_id": device_id, "ok": True, "text": f"📝 Блокнот открыт, текст '{clean_msg[:40]}...' печатается!"})


def hacker_typer(payload: dict, *, device_id, publish_response, webbrowser_module) -> None:
    try:
        webbrowser_module.open("https://hackertyper.net")
        text = "🧑‍💻 Hacker Typer открыт в браузере!"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка: {exc}"
        ok = False
    publish_response("output", {"type": "prank_hacker_typer", "device_id": device_id, "ok": ok, "text": text})


def _beep_sequence(frequencies, duration, *, winsound_module, threading_module, command_type,
                   device_id, publish_response, text) -> None:
    def play():
        try:
            for frequency in frequencies:
                winsound_module.Beep(frequency, duration)
        except Exception:
            pass
    threading_module.Thread(target=play, daemon=True).start()
    publish_response("output", {"type": command_type, "device_id": device_id, "ok": True, "text": text})


def sound_spooky(payload: dict, *, device_id, publish_response, winsound_module, threading_module) -> None:
    _beep_sequence(
        (300, 315, 290, 420, 260, 240, 500, 200), 350,
        winsound_module=winsound_module, threading_module=threading_module,
        command_type="prank_sound_spooky", device_id=device_id, publish_response=publish_response,
        text="👻 Потусторонние жуткие звуки воспроизведены!",
    )


def sound_fart(payload: dict, *, device_id, publish_response, winsound_module, threading_module) -> None:
    _beep_sequence(
        (180, 160, 140, 120, 110, 95, 80), 80,
        winsound_module=winsound_module, threading_module=threading_module,
        command_type="prank_sound_fart", device_id=device_id, publish_response=publish_response,
        text="💨 Смешной звуковой эффект воспроизведён!",
    )


def minimize_all(payload: dict, *, device_id, publish_response, subprocess_module) -> None:
    try:
        ps = "(New-Object -ComObject Shell.Application).MinimizeAll()"
        subprocess_module.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=getattr(subprocess_module, "CREATE_NO_WINDOW", 0),
        )
        text = "📉 Все открытые окна свернуты на рабочий стол!"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка: {exc}"
        ok = False
    publish_response("output", {"type": "prank_minimize_all", "device_id": device_id, "ok": ok, "text": text})


def open_calc_spam(payload: dict, *, device_id, publish_response, subprocess_module, time_module) -> None:
    try:
        for _ in range(5):
            subprocess_module.Popen("calc.exe", shell=True)
            time_module.sleep(0.2)
        text = "🔢 5 калькуляторов открыты каскадом!"
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка: {exc}"
        ok = False
    publish_response("output", {"type": "prank_open_calc_spam", "device_id": device_id, "ok": ok, "text": text})


def invert_screen(
    payload: dict, *, device_id, publish_response, read_current_angle, rotate_screen,
    threading_module, time_module, register_cancel, finish_cancel,
) -> None:
    original_angle = read_current_angle()
    if original_angle is None:
        publish_response("output", {
            "type": "prank_invert_screen", "device_id": device_id, "ok": False,
            "state": "unknown", "text": "⚠️ Не удалось прочитать исходный поворот экрана; сценка не запускалась.",
        })
        return
    cancel_event = threading_module.Event()
    scene_id = f"invert_screen:{time_module.time_ns()}"

    def restore() -> None:
        cancel_event.set()
        rotate_screen(original_angle)

    register_cancel(scene_id, restore)
    rotated, detail = rotate_screen(180)
    if not rotated:
        finish_cancel(scene_id)
        publish_response("output", {
            "type": "prank_invert_screen", "device_id": device_id, "ok": False,
            "state": "failed", "text": f"⚠️ Экран не повёрнут: {detail}",
        })
        return
    if cancel_event.is_set():
        restore()

    def flip():
        try:
            if not cancel_event.wait(7):
                restore()
        finally:
            finish_cancel(scene_id)

    threading_module.Thread(target=flip, daemon=True, name="xider-prank-invert").start()
    publish_response("output", {"type": "prank_invert_screen", "device_id": device_id, "ok": True, "text": "🙃 Экран перевернут вверх дном на 7 секунд!"})


def slow_mouse(
    payload: dict, *, device_id, publish_response, ctypes_module, threading_module,
    time_module, register_cancel, finish_cancel,
) -> None:
    try:
        user32 = ctypes_module.windll.user32
        cur_speed = ctypes_module.c_uint()
        if not user32.SystemParametersInfoW(0x0070, 0, ctypes_module.byref(cur_speed), 0):
            raise RuntimeError("не удалось прочитать скорость указателя")
        original_speed = int(cur_speed.value)
        if not 1 <= original_speed <= 20:
            raise RuntimeError("Windows вернула скорость указателя вне диапазона")
    except Exception as exc:
        publish_response("output", {
            "type": "prank_slow_mouse", "device_id": device_id, "ok": False,
            "state": "unknown", "text": f"⚠️ Сценка не запущена: {type(exc).__name__}.",
        })
        return

    cancel_event = threading_module.Event()
    applied = threading_module.Event()
    restore_lock = threading_module.Lock()
    restored = False
    scene_id = f"slow_mouse:{time_module.time_ns()}"

    def restore() -> None:
        nonlocal restored
        cancel_event.set()
        if not applied.is_set():
            return
        with restore_lock:
            if restored:
                return
            restored = True
            user32.SystemParametersInfoW(0x0071, 0, original_speed, 0)

    register_cancel(scene_id, restore)
    try:
        if not user32.SystemParametersInfoW(0x0071, 0, 1, 0):
            raise RuntimeError("Windows не применила временную скорость указателя")
        applied.set()
        if cancel_event.is_set():
            restore()
    except Exception as exc:
        finish_cancel(scene_id)
        publish_response("output", {
            "type": "prank_slow_mouse", "device_id": device_id, "ok": False,
            "state": "failed", "text": f"⚠️ Сценка не запущена: {type(exc).__name__}.",
        })
        return

    def slow():
        try:
            if not cancel_event.wait(8):
                restore()
        finally:
            finish_cancel(scene_id)

    threading_module.Thread(target=slow, daemon=True, name="xider-prank-slow-mouse").start()
    publish_response("output", {"type": "prank_slow_mouse", "device_id": device_id, "ok": True, "text": "🐌 Мышь замедлена до минимальной скорости на 8 секунд!"})


def random_cursor_motion(
    payload: dict, *, device_id, publish_response, ctypes_module, random_module,
    time_module, threading_module,
) -> None:
    def jiggle():
        user32 = ctypes_module.windll.user32

        class POINT(ctypes_module.Structure):
            _fields_ = [("x", ctypes_module.c_long), ("y", ctypes_module.c_long)]

        point = POINT()
        for _ in range(25):
            user32.GetCursorPos(ctypes_module.byref(point))
            user32.SetCursorPos(
                point.x + random_module.randint(-15, 15),
                point.y + random_module.randint(-15, 15),
            )
            time_module.sleep(0.2)
    try:
        worker = threading_module.Thread(target=jiggle, daemon=True, name="xider-prank-cursor-jiggle")
        worker.start()
    except Exception as exc:
        publish_response("output", {
            "type": "prank_random_clicks", "device_id": device_id, "ok": False,
            "state": "failed", "effect_confirmed": False,
            "text": f"⚠️ Не удалось запустить движение курсора: {type(exc).__name__}",
        })
        return
    publish_response("output", {
        "type": "prank_random_clicks", "device_id": device_id, "ok": True,
        "state": "launch_accepted", "effect_confirmed": False,
        "text": "🤹 Запуск движения курсора принят на 5 секунд; клики не выполняются, перемещение не подтверждено.",
    })


def shake_window(payload: dict, *, device_id, publish_response, ctypes_module, time_module, threading_module) -> None:
    def shake():
        user32 = ctypes_module.windll.user32

        class RECT(ctypes_module.Structure):
            _fields_ = [("left", ctypes_module.c_long), ("top", ctypes_module.c_long),
                        ("right", ctypes_module.c_long), ("bottom", ctypes_module.c_long)]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes_module.byref(rect))
        x, y = rect.left, rect.top
        width, height = rect.right - rect.left, rect.bottom - rect.top
        for index in range(24):
            dx = 20 if index % 2 == 0 else -20
            user32.MoveWindow(hwnd, x + dx, y, width, height, True)
            time_module.sleep(0.06)
        user32.MoveWindow(hwnd, x, y, width, height, True)
    threading_module.Thread(target=shake, daemon=True).start()
    publish_response("output", {"type": "prank_shake_window", "device_id": device_id, "ok": True, "text": "🫨 Активное окно трясётся из стороны в сторону!"})


def earthquake(payload: dict, *, device_id, publish_response, ctypes_module, random_module, time_module, threading_module) -> None:
    def quake():
        user32 = ctypes_module.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return

        class RECT(ctypes_module.Structure):
            _fields_ = [("left", ctypes_module.c_long), ("top", ctypes_module.c_long),
                        ("right", ctypes_module.c_long), ("bottom", ctypes_module.c_long)]

        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes_module.byref(rect)):
            return
        width, height = rect.right - rect.left, rect.bottom - rect.top
        origin_x, origin_y = rect.left, rect.top
        for _ in range(40):
            dx = random_module.randint(-25, 25)
            dy = random_module.randint(-25, 25)
            user32.MoveWindow(hwnd, origin_x + dx, origin_y + dy, width, height, True)
            time_module.sleep(0.04)
        user32.MoveWindow(hwnd, origin_x, origin_y, width, height, True)
    threading_module.Thread(target=quake, daemon=True).start()
    publish_response("output", {"type": "prank_earthquake", "device_id": device_id, "ok": True, "text": "🌋 Землетрясение активного окна запущено на 3 секунды!"})


def bsod(
    payload: dict, *, device_id, publish_response, ctypes_module, time_module,
    threading_module, logger, tkinter_module=None,
) -> None:
    def run():
        try:
            if tkinter_module is None:
                import tkinter as tk
            else:
                tk = tkinter_module
            root = tk.Tk()
            root.title("System Fatal Error")
            root.attributes("-fullscreen", True)
            root.attributes("-topmost", True)
            root.config(cursor="none")
            root.configure(bg="#0078D7")
            root.bind("<Escape>", lambda e: root.destroy())
            root.bind("<Double-Button-1>", lambda e: root.destroy())
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            active = True

            def mouse_lock():
                user32 = ctypes_module.windll.user32
                while active:
                    user32.SetCursorPos(sw // 2, sh // 2)
                    time_module.sleep(0.08)
            threading_module.Thread(target=mouse_lock, daemon=True).start()

            frame = tk.Frame(root, bg="#0078D7")
            frame.place(relx=0.08, rely=0.15, relwidth=0.85, relheight=0.7)
            lbl_sad = tk.Label(frame, text=":(", font=("Segoe UI", 90), fg="white", bg="#0078D7")
            lbl_sad.pack(anchor="w", pady=(0, 20))
            msg_text = (
                "На вашем ПК возникла проблема, и его необходимо перезагрузить.\n"
                "Мы лишь собираем некоторые сведения об ошибке, а затем будет\n"
                "выполнена автоматическая перезагрузка.\n"
            )
            lbl_msg = tk.Label(frame, text=msg_text, font=("Segoe UI Light", 22), fg="white", bg="#0078D7", justify="left")
            lbl_msg.pack(anchor="w", pady=(0, 25))
            lbl_pct = tk.Label(frame, text="0% завершено", font=("Segoe UI Light", 24), fg="white", bg="#0078D7")
            lbl_pct.pack(anchor="w", pady=(0, 40))
            bottom_frame = tk.Frame(frame, bg="#0078D7")
            bottom_frame.pack(anchor="w", fill="x")
            qr_canvas = tk.Canvas(bottom_frame, width=95, height=95, bg="white", highlightthickness=0)
            qr_canvas.pack(side="left", padx=(0, 20))
            stop_text = (
                "Для получения дополнительных сведений об этой проблеме посетите\n"
                "https://www.windows.com/stopcode\n\n"
                "Код остановки: CRITICAL_PROCESS_DIED\n"
                "Что вызвало проблему: win32kbase.sys"
            )
            lbl_stop = tk.Label(bottom_frame, text=stop_text, font=("Segoe UI", 12), fg="white", bg="#0078D7", justify="left")
            lbl_stop.pack(side="left", anchor="w")
            pct = 0

            def update_pct():
                nonlocal pct
                if pct < 100:
                    pct += 15
                    if pct > 100:
                        pct = 100
                    lbl_pct.config(text=f"{pct}% завершено")
                    root.after(1400, update_pct)
                else:
                    root.after(2000, close)

            def close():
                nonlocal active
                active = False
                try:
                    root.destroy()
                except Exception:
                    pass

            root.after(1200, update_pct)
            root.after(18000, close)
            root.mainloop()
            active = False
        except Exception as exc:
            logger.error("BSOD error: %s", exc)

    threading_module.Thread(target=run, daemon=True, name="xgent-bsod").start()
    publish_response("output", {
        "type": "prank_bsod", "device_id": device_id, "ok": True,
        "text": "💀 Полноэкранный BSOD (со скрытием мыши и блокировкой экрана) активирован!",
    })


def fake_ransom_cats(
    payload: dict, *, device_id, publish_response, threading_module, logger, tkinter_module=None,
) -> None:
    def cat_screen():
        try:
            if tkinter_module is None:
                import tkinter as tk
            else:
                tk = tkinter_module
            root = tk.Tk()
            root.title("RansomCat v1.0")
            root.attributes("-fullscreen", True)
            root.attributes("-topmost", True)
            root.configure(bg="#1A002C")
            root.bind("<Escape>", lambda e: root.destroy())
            root.bind("<Double-Button-1>", lambda e: root.destroy())
            frame = tk.Frame(root, bg="#1A002C")
            frame.place(relx=0.5, rely=0.5, anchor="center")
            tk.Label(frame, text="🐱 ВНИМАНИЕ! ВСЕ ВАШИ ФАЙЛЫ ЗАХВАЧЕНЫ КОТАМИ! 🐾", font=("Segoe UI", 26, "bold"), fg="#FF55FF", bg="#1A002C").pack(pady=15)
            msg = (
                "Ваши файлы не зашифрованы, но котики очень голодны!\n"
                "Для разблокировки положите 3 пакетика влажного корма перед монитором.\n\n"
                "Осталось времени до того как кот скинет кружку со стола: 00:05:00\n"
                "(Для закрытия нажмите клавишу ESC)"
            )
            tk.Label(frame, text=msg, font=("Segoe UI", 16), fg="white", bg="#1A002C", justify="center").pack(pady=20)
            root.after(18000, lambda: root.destroy())
            root.mainloop()
        except Exception as exc:
            logger.error("Cat ransom error: %s", exc)
    threading_module.Thread(target=cat_screen, daemon=True).start()
    publish_response("output", {
        "type": "prank_fake_ransom_cats", "device_id": device_id, "ok": True,
        "text": "🐱 Кошачий экран-вымогатель выведен на весь экран!",
    })


def dancing_windows(
    payload: dict, *, device_id, ctypes_module, time_module,
    threading_module, publish_response,
) -> None:
    duration = min(int(payload.get("duration", 10) or 10), 30)

    def run():
        hwnd = ctypes_module.windll.user32.GetForegroundWindow()
        if not hwnd:
            return

        class RECT(ctypes_module.Structure):
            _fields_ = [("left", ctypes_module.c_long), ("top", ctypes_module.c_long),
                        ("right", ctypes_module.c_long), ("bottom", ctypes_module.c_long)]

        rect = RECT()
        ctypes_module.windll.user32.GetWindowRect(hwnd, ctypes_module.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        original_x, original_y = rect.left, rect.top
        deadline = time_module.time() + duration
        offset = 25
        while time_module.time() < deadline:
            ctypes_module.windll.user32.MoveWindow(hwnd, original_x + offset, original_y, width, height, True)
            time_module.sleep(0.05)
            ctypes_module.windll.user32.MoveWindow(hwnd, original_x - offset, original_y, width, height, True)
            time_module.sleep(0.05)
        ctypes_module.windll.user32.MoveWindow(hwnd, original_x, original_y, width, height, True)

    threading_module.Thread(target=run, name="xgent-dancewin", daemon=True).start()
    publish_response("prank_dancing_windows", {
        "type": "prank_dancing_windows", "device_id": device_id, "ok": True,
    })


def black_screen(payload: dict, *, device_id, ctypes_module, publish_response) -> None:
    sc_monitor_power = 0xF170
    wm_syscommand = 0x0112
    hwnd_broadcast = 0xFFFF
    try:
        result = ctypes_module.c_size_t()
        accepted = bool(ctypes_module.windll.user32.SendMessageTimeoutW(
            hwnd_broadcast, wm_syscommand, sc_monitor_power, 2,
            0x0002, 3000, ctypes_module.byref(result),
        ))
        publish_response("prank_black_screen", {
            "type": "prank_black_screen", "device_id": device_id,
            "ok": accepted,
            "state": "launch_accepted" if accepted else "failed",
            "effect_confirmed": False,
            "text": (
                "🌑 Windows приняла запрос погасить дисплей; визуальный результат не подтверждён."
                if accepted else "⚠️ Windows не приняла запрос погасить дисплей."
            ),
        })
    except Exception as exc:
        publish_response("prank_black_screen", {
            "type": "prank_black_screen", "device_id": device_id,
            "ok": False, "state": "failed", "effect_confirmed": False,
            "text": f"⚠️ Не удалось отправить запрос дисплею ({type(exc).__name__}).",
        })


def msgbox_spam(payload: dict, *, device_id, show_message_box, threading_module, publish_response) -> None:
    text = (payload.get("text") or "Привет от XGENT! :)")[:200]
    count = max(1, min(10, int(payload.get("count", 3) or 3)))

    def spam():
        for _ in range(count):
            show_message_box(None, text, "XGENT", 0x40)

    threading_module.Thread(target=spam, name="xgent-msgbox", daemon=True).start()
    publish_response("msgbox_spam", {
        "type": "msgbox_spam", "device_id": device_id,
        "ok": True, "count": count,
    })


def matrix(payload: dict, *, device_id, popen, publish_response) -> None:
    command = 'start cmd /k "color 0a && title MATRIX && mode con: cols=120 lines=40 && @echo off && :loop && echo %random% %random% %random% %random% %random% %random% %random% %random% %random% && goto loop"'
    popen(command, shell=True)
    publish_response("prank_matrix", {
        "type": "prank_matrix", "device_id": device_id, "id": payload.get("id"),
        "ok": True, "state": "launch_accepted", "effect_confirmed": False,
    })


def screamer(
    *, device_id, get_master_volume, open_new, beep, threading_module, publish_response,
) -> None:
    def run():
        try:
            volume = get_master_volume()
            volume.SetMasterVolumeLevelScalar(1.0, None)
            volume.SetMute(False, None)
        except Exception:
            pass
        open_new("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        for _ in range(5):
            try:
                beep(2500, 400)
            except Exception:
                pass

    threading_module.Thread(target=run, name="xgent-screamer", daemon=True).start()
    publish_response("prank_screamer", {"type": "prank_screamer", "device_id": device_id, "ok": True})


def siren(payload: dict, *, device_id, time_module, beep, threading_module, publish_response) -> None:
    duration = min(int(payload.get("duration", 10) or 10), 30)

    def run():
        deadline = time_module.time() + duration
        while time_module.time() < deadline:
            try:
                beep(1200, 200)
                beep(700, 200)
            except Exception:
                time_module.sleep(0.4)

    threading_module.Thread(target=run, name="xgent-siren", daemon=True).start()
    publish_response("prank_siren", {
        "type": "prank_siren", "device_id": device_id, "ok": True, "duration": duration,
    })


def shout_tts(payload: dict, *, device_id, threading_module, publish_response, logger) -> None:
    text = str(payload.get("text") or "ВНИМАНИЕ! КРИТИЧЕСКАЯ ОШИБКА СИСТЕМЫ!")[:300]

    def run():
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("volume", 1.0)
            engine.say(text)
            engine.runAndWait()
        except Exception as exc:
            logger.warning("TTS failed: %s", exc)

    threading_module.Thread(target=run, name="xgent-shout", daemon=True).start()
    publish_response("prank_shout_tts", {
        "type": "prank_shout_tts", "device_id": device_id, "ok": True,
    })


def crazy_cursor(payload: dict, *, device_id, user32, time_module, threading_module, publish_response) -> None:
    duration = min(int(payload.get("duration", 10) or 10), 30)

    def run():
        import math
        center_x, center_y = 800, 500
        angle = 0.0
        deadline = time_module.time() + duration
        while time_module.time() < deadline:
            x = center_x + int(250 * math.cos(angle))
            y = center_y + int(250 * math.sin(angle))
            user32.SetCursorPos(x, y)
            angle += 0.25
            time_module.sleep(0.02)

    threading_module.Thread(target=run, name="xgent-crazycursor", daemon=True).start()
    publish_response("prank_crazy_cursor", {
        "type": "prank_crazy_cursor", "device_id": device_id, "ok": True, "duration": duration,
    })


def rickroll(payload: dict, *, device_id, open_new_tab, sleep, publish_response, logger) -> None:
    try:
        tabs = min(max(int(payload.get("tabs", 10) or 10), 1), 50)
    except (TypeError, ValueError, OverflowError):
        tabs = 10
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    accepted = 0
    for index in range(tabs):
        try:
            accepted += int(bool(open_new_tab(url)))
        except Exception:
            logger.info("Windows browser rejected a prank tab request", exc_info=True)
        if index + 1 < tabs:
            sleep(0.08)
    failed = tabs - accepted
    ok = accepted > 0
    request_id = payload.get("id") if isinstance(payload.get("id"), str) else None
    publish_response("prank_rickroll", {
        "type": "prank_rickroll", "device_id": device_id, "id": request_id,
        "ok": ok, "state": "launch_accepted" if ok else "failed",
        "tabs_requested": tabs, "tabs_accepted": accepted, "tabs_failed": failed,
        "text": (f"Браузер принял запрос на {accepted}/{tabs} вкладок; это не подтверждает показ страниц."
                 if ok else "Браузер не принял ни одного запроса на открытие вкладки."),
    })


def random_site(*, device_id, choice, open_new_tab, publish_response) -> None:
    sites = [
        "https://hackertyper.net/",
        "https://pointerpointer.com/",
        "https://theuselessweb.com/",
        "https://cat-bounce.com/",
        "https://zoomquilt.org/",
    ]
    site = choice(sites)
    try:
        accepted = bool(open_new_tab(site))
    except Exception as exc:
        publish_response("prank_random_site", {
            "type": "prank_random_site", "device_id": device_id, "ok": False,
            "state": "failed", "effect_confirmed": False, "url": site,
            "text": f"⚠️ Не удалось передать ссылку браузеру ({type(exc).__name__}).",
        })
        return
    publish_response("prank_random_site", {
        "type": "prank_random_site", "device_id": device_id, "ok": accepted,
        "state": "launch_accepted" if accepted else "failed",
        "effect_confirmed": False, "url": site,
        "text": (
            "🌐 Браузер принял запрос открыть ссылку; отображение страницы не подтверждено."
            if accepted else "⚠️ Браузер не принял запрос открыть ссылку."
        ),
    })


# Compatibility methods moved out of the monolith; globals are supplied by XgentClient.
def _do_prank_screamer(self, payload: dict) -> None:
        return _prank_commands.screamer(
            device_id=DEVICE_ID, get_master_volume=self._get_master_volume,
            open_new=webbrowser.open_new, beep=winsound.Beep,
            threading_module=threading, publish_response=self._publish_response,
        )

def _do_prank_rickroll(self, payload: dict) -> None:
        return _prank_commands.rickroll(
            payload, device_id=DEVICE_ID, open_new_tab=webbrowser.open_new_tab,
            sleep=time.sleep, publish_response=self._publish_response, logger=log,
        )

def _do_prank_matrix(self, payload: dict) -> None:
        return _prank_commands.matrix(
            payload, device_id=DEVICE_ID, popen=subprocess.Popen,
            publish_response=self._publish_response,
        )

def _do_prank_siren(self, payload: dict) -> None:
        return _prank_commands.siren(
            payload, device_id=DEVICE_ID, time_module=time, beep=winsound.Beep,
            threading_module=threading, publish_response=self._publish_response,
        )

def _do_prank_shout_tts(self, payload: dict) -> None:
        return _prank_commands.shout_tts(
            payload, device_id=DEVICE_ID, threading_module=threading,
            publish_response=self._publish_response, logger=log,
        )

def _do_prank_swap_mouse(self, payload: dict) -> None:
        return _desktop_commands.prank_swap_mouse(
            payload, device_id=DEVICE_ID,
            swap_mouse_button=ctypes.windll.user32.SwapMouseButton,
            publish_response=self._publish_response,
        )

def _do_prank_crazy_cursor(self, payload: dict) -> None:
        return _prank_commands.crazy_cursor(
            payload, device_id=DEVICE_ID, user32=ctypes.windll.user32,
            time_module=time, threading_module=threading, publish_response=self._publish_response,
        )

def _do_prank_hide_desktop(self, payload: dict) -> None:
        return _desktop_commands.prank_hide_desktop(
            payload, device_id=DEVICE_ID, user32=ctypes.windll.user32,
            publish_response=self._publish_response,
        )

def _do_prank_dancing_windows(self, payload: dict) -> None:
        return _prank_commands.dancing_windows(
            payload, device_id=DEVICE_ID, ctypes_module=ctypes,
            time_module=time, threading_module=threading,
            publish_response=self._publish_response,
        )

def _do_prank_black_screen(self, payload: dict) -> None:
        return _prank_commands.black_screen(
            payload, device_id=DEVICE_ID, ctypes_module=ctypes,
            publish_response=self._publish_response,
        )

def _do_prank_random_site(self, payload: dict) -> None:
        return _prank_commands.random_site(
            device_id=DEVICE_ID, choice=random.choice,
            open_new_tab=webbrowser.open_new_tab,
            publish_response=self._publish_response,
        )

def _do_prank_bsod(self, payload: dict) -> None:
        """Полноэкранный фейковый BSOD с анимацией и скрытием курсора мыши."""
        return _prank_commands.bsod(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
            logger=log,
        )

def _do_prank_fake_update(self, payload: dict) -> None:
        """Полноэкранная локальная демо-анимация с видимым выходом и таймером."""
        return _prank_commands.fake_update(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            threading_module=threading, time_module=time,
            register_prank_cancel=self._register_prank_cancel,
            finish_prank_cancel=self._finish_prank_cancel, logger=log,
        )

def _do_prank_toast_spam(self, payload: dict) -> None:
        """Спам 5 системными уведомлениями в трее."""
        return _prank_commands.toast_spam(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            subprocess_module=subprocess,
        )

def _do_prank_keyboard_disco(self, payload: dict) -> None:
        """Мигание индикаторами клавиатуры (CapsLock, NumLock, ScrollLock)."""
        return _prank_commands.keyboard_disco(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_beep_morse(self, payload: dict) -> None:
        """Звуковой сигнал азбукой Морзе (SOS) через динамики."""
        return _prank_commands.beep_morse(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            winsound_module=winsound, time_module=time, threading_module=threading,
        )

def _do_prank_open_notepad_type(self, payload: dict) -> None:
        """Открытие блокнота с эффектом живого набора текста."""
        return _prank_commands.open_notepad_type(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            subprocess_module=subprocess,
        )

def _do_prank_hacker_typer(self, payload: dict) -> None:
        """Открытие интерактивного Hacker Typer."""
        return _prank_commands.hacker_typer(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            webbrowser_module=webbrowser,
        )

def _do_prank_sound_spooky(self, payload: dict) -> None:
        """Жуткие потусторонние звуки."""
        return _prank_commands.sound_spooky(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            winsound_module=winsound, threading_module=threading,
        )

def _do_prank_sound_fart(self, payload: dict) -> None:
        """Смешной звуковой эффект."""
        return _prank_commands.sound_fart(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            winsound_module=winsound, threading_module=threading,
        )

def _do_prank_minimize_all(self, payload: dict) -> None:
        """Сворачивание всех открытых окон."""
        return _prank_commands.minimize_all(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            subprocess_module=subprocess,
        )

def _do_prank_open_calc_spam(self, payload: dict) -> None:
        """Каскад из 5 калькуляторов."""
        return _prank_commands.open_calc_spam(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            subprocess_module=subprocess, time_module=time,
        )

def _do_prank_invert_screen(self, payload: dict) -> None:
        """Переворот экрана на 180° на 7 секунд с автовозвратом."""
        return _prank_commands.invert_screen(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            read_current_angle=_read_current_display_angle, rotate_screen=_rotate_screen,
            threading_module=threading, time_module=time,
            register_cancel=self._register_prank_cancel, finish_cancel=self._finish_prank_cancel,
        )

def _do_prank_slow_mouse(self, payload: dict) -> None:
        """Замедление скорости указателя мыши на 8 секунд с восстановлением."""
        return _prank_commands.slow_mouse(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, threading_module=threading, time_module=time,
            register_cancel=self._register_prank_cancel, finish_cancel=self._finish_prank_cancel,
        )

def _do_prank_random_clicks(self, payload: dict) -> None:
        """Request random cursor movement for five seconds; does not synthesize clicks."""
        return _prank_commands.random_cursor_motion(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, random_module=random, time_module=time, threading_module=threading,
        )

def _do_prank_paste_clipboard_spam(self, payload: dict) -> None:
        """Подмена буфера обмена на забавную фразу."""
        return _prank_commands.paste_clipboard_spam(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        )

def _do_prank_type_reversed(self, payload: dict) -> None:
        """Инвертирование содержимого буфера обмена задом наперёд."""
        return _prank_commands.type_reversed(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
        )

def _do_prank_volume_jump(self, payload: dict) -> None:
        """Скачки громкости (100% <-> 10%) с автовосстановлением."""
        return _prank_commands.volume_jump(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            get_master_volume=self._get_master_volume,
            time_module=time, threading_module=threading,
        )

def _do_prank_say_whisper(self, payload: dict) -> None:
        """Медленный шепчущий голос через TTS."""
        return _prank_commands.say_whisper(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            threading_module=threading,
        )

def _do_prank_fake_virus(self, payload: dict) -> None:
        """Серия шуточных предупреждений об опасном вирусе."""
        return _prank_commands.fake_virus(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_open_cd(self, payload: dict) -> None:
        """Открытие лотка CD-ROM дисковода или звуковой сигнал."""
        return _prank_commands.open_cd(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, winsound_module=winsound,
        )

def _do_prank_change_wallpaper(self, payload: dict) -> None:
        """Установка шуточных обоев с сохранением резервной копии."""
        return _prank_commands.change_wallpaper(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            config_dir=CONFIG_DIR, winreg_module=winreg, ctypes_module=ctypes,
        )

def _do_prank_restore_wallpaper(self, payload: dict) -> None:
        """Восстановление исходных обоев рабочего стола."""
        return _prank_commands.restore_wallpaper_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            restore=self._restore_saved_prank_wallpaper,
        )

def _do_prank_speak_time(self, payload: dict) -> None:
        """Озвучивание точного времени говорящим синтезатором речи."""
        return _prank_commands.speak_time(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            time_module=time, threading_module=threading,
        )

def _do_prank_rickroll_terminal(self, payload: dict) -> None:
        """Запуск терминала с ASCII анимацией / рикроллом."""
        return _prank_commands.rickroll_terminal(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            subprocess_module=subprocess,
        )

def _do_prank_screen_off_brief(self, payload: dict) -> None:
        """Кратковременное погасание экрана на 3 секунды."""
        return _prank_commands.screen_off_brief(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_alert_loop(self, payload: dict) -> None:
        """Цепочка из 3 забавных диалоговых окон."""
        return _prank_commands.alert_loop(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_open_browser_memes(self, payload: dict) -> None:
        """Открытие 3 вкладок с легендарными мемами в браузере."""
        return _prank_commands.open_browser_memes(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            webbrowser_module=webbrowser, time_module=time,
        )

def _do_prank_glitch_cursor(self, payload: dict) -> None:
        """Курсор рисует 'восьмёрку' в течение 5 секунд."""
        return _prank_commands.glitch_cursor(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_shake_window(self, payload: dict) -> None:
        """Тряска активного окна."""
        return _prank_commands.shake_window(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_meme_wallpaper(self, payload: dict) -> None:
        """Скачать и установить случайный смешной мем в качестве обоев."""
        payload["random_meme"] = True
        self._do_wallpaper_set(payload)

def _do_prank_caps_disco(self, payload: dict) -> None:
        """Дискотека индикаторами CapsLock/NumLock на клавиатуре."""
        return _prank_commands.caps_disco(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_cursor_circle(self, payload: dict) -> None:
        """Круговые гипнотические движения курсора мыши."""
        return _prank_commands.cursor_circle(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, time_module=time, threading_module=threading,
        )

def _do_prank_fake_error_spam(self, payload: dict) -> None:
        """Каскадный спам звуками критической ошибки Windows."""
        return _prank_commands.fake_error_spam(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            time_module=time, threading_module=threading, winsound_module=winsound,
        )

def _do_prank_fake_delete_sys32(self, payload: dict) -> None:
        """Имитация удаления System32 в терминале."""
        return _prank_commands.fake_delete_sys32(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            subprocess_module=subprocess,
        )

def _do_prank_ghost_typer(self, payload: dict) -> None:
        """Печать призрачных фраз в активное окно."""
        return _prank_commands.ghost_typer(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, random_module=random, time_module=time,
            threading_module=threading, logger=log,
        )

def _do_prank_fbi_lock(self, payload: dict) -> None:
        """Окно предупреждения от спецслужб."""
        return _prank_commands.fbi_lock(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, threading_module=threading,
        )

def _do_prank_cat_invaders(self, payload: dict) -> None:
        """Открытие вкладок с котиками в браузере."""
        return _prank_commands.cat_invaders(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            webbrowser_module=webbrowser, time_module=time,
        )

def _do_prank_fake_ransom_cats(self, payload: dict) -> None:
        """Полноэкранный шуточный вымогатель с котиками."""
        return _prank_commands.fake_ransom_cats(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            threading_module=threading, logger=log,
        )

def _do_prank_nyan_stream(self, payload: dict) -> None:
        """Запуск Nyan Cat с радугой."""
        return _prank_commands.nyan_stream(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            webbrowser_module=webbrowser,
        )

def _do_prank_random_beeps(self, payload: dict) -> None:
        """Случайные внезапные звуковые сигналы материнской платы."""
        return _prank_commands.random_beeps(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            random_module=random, time_module=time, threading_module=threading,
        )

def _do_prank_confetti_winner(self, payload: dict) -> None:
        """Праздничный экран «Вы выиграли миллион долларов»."""
        return _prank_commands.confetti_winner(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            tempfile_module=tempfile, temp_prefix=_PRANK_TEMP_PREFIX,
            schedule_temp_cleanup=_schedule_prank_temp_cleanup,
            subprocess_module=subprocess,
        )

def _do_prank_low_battery_fake(self, payload: dict) -> None:
        """Ложное системное оповещение о критическом заряде батареи 0%."""
        return _prank_commands.low_battery_fake(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, threading_module=threading,
        )

def _do_prank_earthquake(self, payload: dict) -> None:
        """Землетрясение активного окна (вибрация 8.0 баллов)."""
        return _prank_commands.earthquake(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            ctypes_module=ctypes, random_module=random, time_module=time, threading_module=threading,
        )

def _do_prank_laugh_track(self, payload: dict) -> None:
        """Зловещий закадровый смех через динамики."""
        return _prank_commands.laugh_track(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            threading_module=threading, logger=log,
        )

def _do_prank_stop_all(self, payload: dict) -> None:
        """Restore only prank state for which XIDER has an explicit saved original value."""
        return _prank_commands.stop_all(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            cancel_active_pranks=self._cancel_active_pranks,
            restore_wallpaper=self._restore_saved_prank_wallpaper, logger=log,
        )
