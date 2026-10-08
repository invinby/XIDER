# Screenshot result protocol

Both Windows and macOS agents and the Telegram bot use this canonical state mapping on the existing `screenshot` MQTT suffix and `type: screenshot`. Existing signing, encryption and command correlation remain unchanged. These states describe one screenshot request, not the separate Task 1 permission/capability snapshot states.

Every modern result contains `state`, nonempty `detail`, `platform`, `device_id`, and `image` (base64 JPEG on success, null otherwise).

| State | Meaning and owner action |
| --- | --- |
| success | One capture returned a readable, bounded image. This does not assert a persistent OS permission grant. |
| permission_denied | Explicit access-denied capture error. Inspect permissions locally; no remote TCC/UAC consent. |
| session_unavailable | No accessible owner GUI session. Check login, lock, sleep/wake, user switching and LaunchAgent context. |
| tool_missing | Capture tool or Pillow unavailable, including disappearance after dependency preflight. Repair local installation. |
| command_failed | Capture command/API failed without specific permission evidence. Check the diagnostic; do not infer TCC denial. |
| file_empty | Empty capture artifact/dimensions/encoding. Check the session and retry a permitted action. |
| file_oversized | Artifact or encoded image exceeds 30 MiB, or Windows raw image exceeds 40 megapixels. Reduce screen dimensions/count. |
| file_unreadable | Capture artifact cannot be read/decoded or cleaned up. Check local disk/file access. |
| timeout | Capture exceeded the 10-second caller deadline, or the previous Windows native call is still running. Check the GUI session and retry. |
| transport_failed | MQTT publisher positively reported delivery failure. Check transport; the diagnostic itself may also fail to arrive. |

Unknown/legacy failure responses remain unknown. Modern failed states override even an attached image. Modern success images pass one shared bot validator: strict bounded base64, JPEG/PNG format allowlist, positive dimensions at most 40 megapixels, then complete pixel decode. Truncated images and Pillow decompression bombs fail per item, preserving valid peer results in bulk requests. Legacy images keep existing compatibility, with strict base64, nonempty and size checks. The bot result wait deadline reports uncertain delivery, never proves that the device is offline.

Owner-local macOS guidance: Системные настройки → Конфиденциальность и безопасность → Запись экрана (Screen Recording). Названия разделов могут отличаться в разных версиях macOS. Verify the active LaunchAgent GUI session after lock, sleep/wake and user switching on a physical Mac. Console ownership alone does not prove TCC permission or guarantee post-lock capture behavior.

The Windows ImageGrab API has no built-in wall-clock deadline. A single daemon worker isolates its call with a 10-second caller deadline; the command worker is freed on timeout. While that native call is still running, subsequent requests return timeout without spawning another capture thread. A late image is closed and discarded. A permanently hung native call leaves screenshot capture unavailable until agent restart; it is not forcefully terminated or allowed to accumulate threads. No live device acceptance is implied by mocked tests.
