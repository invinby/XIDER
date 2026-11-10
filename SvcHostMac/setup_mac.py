#!/usr/bin/env python3
"""Интерактивный помощник выдачи прав на macOS (TCC Wizard).

Проверяет и вызывает системные окна для:
1. Записи экрана (Screen Recording)
2. Веб-камеры (Camera)
3. Микрофона (Microphone)
4. Универсального доступа (Accessibility)
"""

import os
import subprocess
import sys
import time


def print_header() -> None:
    print("=" * 65)
    print(" 🍎 XGENT — Мастер первоначальной настройки прав macOS 🍎")
    print("=" * 65)
    print("macOS требует выдать явные разрешения для терминала / Python.")
    print("Сейчас мы проверим и запросим каждое разрешение по очереди.\n")


def check_screen_recording() -> bool:
    print("1️⃣ [1/4] Проверка разрешения «Запись экрана» (Скриншоты)...")
    test_file = "/tmp/xgent_test_screen.png"
    if os.path.exists(test_file):
        try:
            os.remove(test_file)
        except OSError:
            pass
    res = subprocess.run(["screencapture", "-x", test_file], capture_output=True)
    if res.returncode == 0 and os.path.exists(test_file) and os.path.getsize(test_file) > 1000:
        print("   ✅ [OK] Запись экрана РАЗРЕШЕНА!\n")
        try:
            os.remove(test_file)
        except OSError:
            pass
        return True
    else:
        print("   ⚠️ [ТРЕБУЕТСЯ ДЕЙСТВИЕ] Разрешение на запись экрана не выдано.")
        print("   👉 Открываю «Системные настройки -> Запись экрана»...")
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"], check=False)
        print("   Включите тумблер для Terminal / iTerm / Python.")
        try:
            input("   Нажмите [ENTER] здесь, когда включите тумблер в Настройках... ")
        except (EOFError, KeyboardInterrupt):
            pass
        return False


def check_camera() -> bool:
    print("2️⃣ [2/4] Проверка разрешения «Камера» (Веб-камера)...")
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        opened = cap.isOpened()
        cap.release()
        if opened:
            print("   ✅ [OK] Камера РАЗРЕШЕНА!\n")
            return True
    except Exception:
        pass

    print("   ⚠️ [ТРЕБУЕТСЯ ДЕЙСТВИЕ] Разрешение на камеру не выдано.")
    print("   👉 Открываю «Системные настройки -> Камера»...")
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"], check=False)
    print("   Включите тумблер для Terminal / iTerm / Python.")
    try:
        input("   Нажмите [ENTER] здесь, когда включите тумблер в Настройках... ")
    except (EOFError, KeyboardInterrupt):
        pass
    return False


def check_microphone() -> bool:
    print("3️⃣ [3/4] Проверка разрешения «Микрофон» (Прослушка)...")
    try:
        import sounddevice as sd
        rec = sd.rec(int(0.2 * 44100), samplerate=44100, channels=1)
        sd.wait()
        print("   ✅ [OK] Микрофон РАЗРЕШЕН!\n")
        return True
    except Exception as exc:
        print(f"   ⚠️ Ошибка доступа к микрофону: {exc}")
        print("   👉 Открываю «Системные настройки -> Микрофон»...")
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"], check=False)
        print("   Включите тумблер для Terminal / iTerm / Python.")
        try:
            input("   Нажмите [ENTER] здесь, когда включите тумблер в Настройках... ")
        except (EOFError, KeyboardInterrupt):
            pass
        return False


def check_accessibility() -> bool:
    print("4️⃣ [4/4] Проверка разрешения «Универсальный доступ» (Клавиатура/Ввод)...")
    script = 'tell application "System Events" to get name of current user'
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if res.returncode == 0:
        print("   ✅ [OK] Управление системой и клавиатурой РАЗРЕШЕНО!\n")
        return True
    else:
        print("   ⚠️ [ТРЕБУЕТСЯ ДЕЙСТВИЕ] Разрешение на управление вводом не выдано.")
        print("   👉 Открываю «Системные настройки -> Универсальный доступ»...")
        subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"], check=False)
        print("   Включите тумблер для Terminal / iTerm / Python.")
        try:
            input("   Нажмите [ENTER] здесь, когда включите тумблер в Настройках... ")
        except (EOFError, KeyboardInterrupt):
            pass
        return False


def main() -> None:
    print_header()
    check_screen_recording()
    check_camera()
    check_microphone()
    check_accessibility()
    print("=" * 65)
    print(" 🎉 Проверка разрешений завершена!")
    print(" Теперь запустите агента:")
    print(" ./start_agent.sh")
    print("=" * 65)


if __name__ == "__main__":
    main()
