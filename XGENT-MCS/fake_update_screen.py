"""Small, local-only macOS demo screen used by the prank command.

The window is intentionally time-bounded and has an always-visible close
button. Its progress indicator is indeterminate: it never claims a real update
percentage and this helper performs no system update or network request.
"""

from __future__ import annotations


DEMO_SECONDS = 30.0


def run_fake_update_demo() -> None:
    """Show a native borderless demo window over the main display."""
    try:
        import AppKit
        import Foundation
    except ImportError as exc:
        raise RuntimeError("для полноэкранного окна требуется системный AppKit/PyObjC") from exc

    screen = AppKit.NSScreen.mainScreen()
    if screen is None:
        raise RuntimeError("macOS не предоставила основной экран")
    screen_frame = screen.frame()
    width = float(AppKit.NSWidth(screen_frame))
    height = float(AppKit.NSHeight(screen_frame))
    if width < 640 or height < 480:
        raise RuntimeError("размер основного экрана не удалось определить")

    class DemoWindow(AppKit.NSWindow):
        def canBecomeKeyWindow(self):  # noqa: N802 - Objective-C selector name
            return True

        def keyDown_(self, event):
            if int(event.keyCode()) == 53:  # Escape
                self.demo_controller.finish_(None)

    class DemoController(Foundation.NSObject):
        def finish_(self, _sender):
            self.application.terminate_(None)

    application = AppKit.NSApplication.sharedApplication()
    # Keep the demo visible as a normal macOS app instead of disguising it as
    # a background-only process.
    application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    controller = DemoController.alloc().init()
    controller.application = application

    window = DemoWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        screen_frame,
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    if window is None:
        raise RuntimeError("не удалось создать полноэкранное окно")
    window.demo_controller = controller
    window.setReleasedWhenClosed_(False)
    window.setOpaque_(True)
    window.setBackgroundColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        0.055, 0.075, 0.12, 1.0,
    ))
    window.setLevel_(AppKit.NSNormalWindowLevel)

    view = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, width, height))
    window.setContentView_(view)

    def add_label(text: str, y: float, size: float, *, bold: bool = False, color=None) -> None:
        label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(width * 0.12, y, width * 0.76, size * 2.2),
        )
        label.setStringValue_(text)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setAlignment_(AppKit.NSTextAlignmentCenter)
        label.setFont_(
            AppKit.NSFont.boldSystemFontOfSize_(size)
            if bold else AppKit.NSFont.systemFontOfSize_(size)
        )
        label.setTextColor_(color or AppKit.NSColor.whiteColor())
        view.addSubview_(label)

    add_label("Системное обновление macOS", height * 0.60, 31, bold=True)
    add_label(
        "Подготовка экрана. Это демонстрация — системные файлы не изменяются.",
        height * 0.53,
        17,
        color=AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.78, 1.0),
    )

    progress = AppKit.NSProgressIndicator.alloc().initWithFrame_(
        AppKit.NSMakeRect(width * 0.28, height * 0.46, width * 0.44, 12),
    )
    progress.setStyle_(AppKit.NSProgressIndicatorStyleBar)
    progress.setIndeterminate_(True)
    progress.startAnimation_(None)
    view.addSubview_(progress)

    add_label(
        "ДЕМО · Esc или кнопка ниже закрывает экран автоматически через 30 секунд",
        height * 0.30,
        13,
        color=AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.72, 1.0),
    )
    close_button = AppKit.NSButton.alloc().initWithFrame_(
        AppKit.NSMakeRect(width * 0.40, height * 0.20, width * 0.20, 42),
    )
    close_button.setTitle_("Закрыть демо")
    close_button.setBezelStyle_(AppKit.NSBezelStyleRounded)
    close_button.setTarget_(controller)
    close_button.setAction_("finish:")
    view.addSubview_(close_button)

    timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        DEMO_SECONDS,
        controller,
        "finish:",
        None,
        False,
    )
    window.makeKeyAndOrderFront_(None)
    window.makeFirstResponder_(window)
    application.activateIgnoringOtherApps_(True)
    # Retain both the window and timer for the duration of the native event loop.
    window._xider_demo_timer = timer
    application.run()
    window.close()
