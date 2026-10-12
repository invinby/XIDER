# Permission settings reference: macOS and Windows 11

Checked against first-party Apple and Microsoft documentation on 2026-10-12. This is a UI/API reference for the XIDER capability screen; it is not a report that permissions have been granted on a particular machine.

## macOS

The common path is **Apple menu → System Settings → Privacy & Security → [category]**. Apple may place categories lower in the sidebar. Labels below reflect current Apple documentation; older macOS releases may use different names.

| Capability | Current Settings category and user action | What the agent can honestly report |
|---|---|---|
| Camera | **Camera**; turn the app's row on. Only apps that have requested camera access appear. | OS authorization state plus an actual camera-open smoke test are distinct. A green hardware light indicates camera use, not that XIDER's full camera workflow succeeded. |
| Microphone | **Microphone**; turn the app's row on. | OS authorization state is not proof an input device is present or audio capture works; test capture separately. |
| Location | **Location Services**; enable globally, then enable the app in the list. System Services has separate controls. | Report the native location authorization/result separately from IP-derived location. A successful permission state does not guarantee a precise fix; a failed native fix must not be labelled as GPS. |
| Screen capture | Current label: **Screen & System Audio Recording**; enable the app. | This is the relevant macOS privacy permission for screen capture. After changing it, restart/relaunch the capturing app if required and run a real screenshot test. Do not infer success from an enabled row alone. |
| Accessibility | **Accessibility**; enable the app. If absent, Apple says to use the Add button to select it. | This grants a broad ability to control the Mac via accessibility features. Report the OS permission separately from whether any particular UI automation action works. |
| Files & Folders | **Files & Folders**; enable the requested locations for the app. | Permissions are location-specific (for example Desktop, Documents, Downloads); this is not blanket disk access. Report access to a named path only after attempting it. Full Disk Access is a separate, broader category. |

Apple documents the manual navigation and authorization semantics for [camera](https://support.apple.com/en-gb/guide/mac-help/-mchlf6d108da/mac), [microphone](https://support.apple.com/en-am/guide/mac-help/mchla1b1e1fe/mac), [Location Services](https://support.apple.com/en-euro/guide/mac-help/mh35873/mac), [screen and system audio recording](https://support.apple.com/en-gb/guide/mac-help/mchld6aa7d23/mac), [Accessibility](https://support.apple.com/guide/mac-help/allow-accessibility-apps-to-access-your-mac-mh43185/mac), and [Files & Folders](https://support.apple.com/en-ie/guide/mac-help/mchld5a35146/27/mac/27). Apple's current label for the screen permission is “Screen & System Audio Recording”; older releases may show “Screen Recording.”

### macOS settings links

Apple Support pages offer a **“Open Privacy & Security settings for me”** action in the browser. The Apple user/developer documentation reviewed here does **not** specify a public, stable app URI for opening each TCC privacy subpage directly. Therefore, XIDER should always show the click path and treat any per-category `x-apple.systempreferences:` URL as best-effort/unsupported unless Apple documents it for the target OS. Never show “opened settings” as success until the native launcher confirms it; always retain manual steps.

For screen capture, Apple's ScreenCaptureKit documentation says to request permission before capture; its sample notes that the first run prompts for Screen Recording and capture may require restarting after permission is granted: [ScreenCaptureKit](https://developer.apple.com/documentation/ScreenCaptureKit) and [macOS capture sample](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos). Accessibility and file access are powerful grants; the app must explain why before directing the owner to enable them.

## Windows 11

The common path is **Start → Settings → Privacy & security → [category]**. Names and availability can differ under device-management policy and between packaged Store apps and classic desktop apps.

| Capability | Current Settings page / controls | Scope and honest reporting |
|---|---|---|
| Camera | **Camera**: `Camera access`, `Let apps access your camera`, and `Let desktop apps access your camera`. | Store apps may have per-app toggles. The desktop-app toggle is a shared control, not a per-desktop-app grant; Microsoft warns the list can be incomplete and some desktop apps may still access camera through unsupported/other methods. Confirm with an actual capture attempt. |
| Microphone | **Microphone**: `Microphone access`, `Let apps access your microphone`, and `Let desktop apps access your microphone`. | Same packaged-app vs desktop-app distinction. A toggle is not proof that an input device exists or that recording works. |
| Location | **Location**: `Location services`, `Let apps access your location`, and app-specific precise-location controls where supported. | Classic desktop apps do not have the same per-app precise-location control as Store apps. Windows location may combine GPS, Wi-Fi, cell, IP, or default location; identify the actual source and accuracy. An IP fallback is not “GPS.” |
| Screen capture | Windows has **Graphics capture** privacy pages/URIs for packaged app capabilities, including programmatic capture and capture without the system border. The capture API can also show secure picker UI and a visible border. | These controls are not a universal privacy gate for arbitrary classic desktop screenshot methods. For an unpackaged agent, report only a real capture test; do not equate an app-capability toggle with a global “screen recording permission.” |
| Accessibility | Windows **Accessibility** pages control accessibility features for the user (for example Narrator and interaction settings); there is no general per-desktop-app “Accessibility permission” toggle equivalent to macOS's Accessibility grant in the cited app-permission model. | Do not report “accessibility permission granted/denied” as an OS fact for the Python desktop agent. State the relevant feature/action and test it directly. |
| Files | **File system** (and separate Documents/Downloads/Pictures/etc. pages may be present). | `broadFileSystemAccess` is a restricted capability for apps that declare/use that Windows capability. Windows documents that classic desktop programs may not appear and are not controlled by the general File system toggle. For XIDER's desktop process, verify actual access to a specific path; do not claim this toggle gates all file access. |

Microsoft documents the camera/microphone pages and desktop-app limitations in [Camera, microphone, and privacy](https://support.microsoft.com/en-us/windows/privacy/windows-camera-microphone-and-privacy); location scope and accuracy in [Windows location service and privacy](https://support.microsoft.com/en-us/windows/privacy/windows-location-service-and-privacy); and File system exceptions in [Windows file system access and privacy](https://support.microsoft.com/en-us/windows/privacy/windows-file-system-access-and-privacy). The Microsoft Learn [Settings URI reference](https://learn.microsoft.com/en-us/windows/apps/develop/launch/launch-settings) documents `ms-settings:` links, including:

| Page | Documented URI |
|---|---|
| Camera | `ms-settings:privacy-webcam` |
| Microphone | `ms-settings:privacy-microphone` |
| Location | `ms-settings:privacy-location` |
| File system | `ms-settings:privacy-broadfilesystemaccess` |
| Programmatic graphics capture | `ms-settings:privacy-graphicscaptureprogrammatic` |
| Graphics capture without border | `ms-settings:privacy-graphicscapturewithoutborder` |

The [Windows screen-capture API guide](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture) describes secure picker UI and the capture border. Microsoft's [app capability declarations](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/app-capability-declarations) describe graphics-capture capabilities; these are package/app-capability semantics, not a blanket permission indicator for all desktop code.

## Product rules for XIDER's “check permissions” screen

1. Separate **OS-reported authorization**, **feature support**, **device presence**, and **last real test result**. Never collapse them into a single green check.
2. Use `Unknown / needs user check` where the OS does not expose a meaningful status for this app type. Do not infer `Granted` from an empty settings list.
3. Offer an OS settings shortcut only from a fixed platform allowlist. For Windows use the documented `ms-settings:` URIs above. For macOS prefer opening Privacy & Security (if supported) plus explicit manual path until a tested, supported per-pane link exists.
4. After returning from Settings, rerun the relevant feature probe; changing a toggle is not proof the permission works.
5. Always distinguish Windows native location from IP fallback, and report the source and confidence for each result.
