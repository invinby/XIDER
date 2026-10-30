"""Migrate approved candidate rows into the static, non-executable registry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = "docs/superpowers/specs/2026-10-14-tarped-capability-expansion-design.md"
BASELINE_COMMIT = "6d20370a06eb157428627f9002852514615e8ee2"
AREA_TITLES = {
    "B01": ("Telegram navigation", "Навигация Telegram-бота"),
    "B02": ("Device catalog", "Каталог устройств"),
    "D01": ("System", "Система"),
    "D02": ("Screen and media", "Экран и медиа"),
    "D03": ("Files", "Файлы"),
    "D04": ("Network", "Сеть"),
    "D05": ("Terminal", "Терминал"),
    "D06": ("Power and Guard Keeper", "Питание и Guard Keeper"),
    "D07": ("Pranks", "Розыгрыши"),
    "A01": ("Admin and RBAC", "Админка и RBAC"),
    "S01": ("Server", "Сервер"),
    "R01": ("Versions and updates", "Версии и обновления"),
    "P01": ("Permissions and location", "Разрешения и геолокация"),
    "O01": ("Diagnostics and audit", "Диагностика и аудит"),
    "W01": ("Windows-native", "Windows-агент"),
    "M01": ("macOS-native", "macOS-агент"),
    "G01": ("Documentation and site", "Документация и сайт"),
}
AREA_COMPONENTS = {
    **{area: {"bot"} for area in ("B01", "B02", "A01")},
    **{area: {"bot", "windows_agent", "macos_agent"} for area in (
        "D01", "D02", "D03", "D04", "D05", "D06", "D07", "P01"
    )},
    "S01": {"bot", "server"},
    "R01": {"bot", "server", "windows_agent", "macos_agent"},
    "O01": {"bot", "server"},
    "W01": {"bot", "windows_agent"},
    "M01": {"bot", "macos_agent"},
    "G01": {"site"},
}
AREA_PLATFORMS = {
    "B01": ["telegram"],
    "B02": ["telegram"],
    **{area: ["telegram", "windows", "macos"] for area in (
        "D01", "D02", "D03", "D04", "D05", "D06", "D07", "P01"
    )},
    "A01": ["telegram"],
    "S01": ["telegram", "server"],
    "R01": ["telegram", "server", "windows", "macos"],
    "O01": ["telegram", "server", "windows", "macos"],
    "W01": ["telegram", "windows"],
    "M01": ["telegram", "macos"],
    "G01": ["web"],
}
COMPONENTS = ("bot", "windows_agent", "macos_agent", "server", "site")
DEVICE_NAVIGATION = (
    ("device.system", "system", "cat:system", "primary", "nav"),
    ("device.screen", "screen", "cat:screen", "success", "nav"),
    ("device.files", "files", "cat:files", "primary", "nav"),
    ("device.network", "network", "cat:network", "primary", "nav"),
    ("device.terminal", "terminal", "cat:terminal", "primary", "nav"),
    ("device.power", "power", "cat:power", "danger", "nav"),
    ("device.versions", "versions", "versions:device", "success", "versions"),
    ("device.permissions", "permissions", "cmd:capabilities", "primary", "capabilities"),
    ("device.pranks", "pranks", "cat:pranks", "success", "nav"),
    ("device.tools", "device_tools_button", "device:tools", "primary", "device_tools"),
)
ADMIN_NAVIGATION = (
    ("admin.users", "admin_users", "admin:users", "primary", "nav"),
    ("admin.audit", "admin_audit", "admin:audit", "primary", "nav"),
    ("admin.stale_grants", "admin_stale_grants", "admin:stale-grants", "primary", "admin_stale_grants"),
    ("admin.access_review", "access_review", "admin:access-review", "primary", "access_review"),
    ("admin.permission_requests", "access_requests", "admin:access-requests", "primary", "permission_requests"),
    ("admin.bulk_grant", "bulk_grant", "admin:bulk-grant", "primary", "bulk_grant"),
    ("admin.bulk_revoke", "bulk_revoke", "admin:bulk-revoke", "danger", "bulk_revoke"),
    ("admin.texts", "admin_texts", "admin:texts", "primary", "nav"),
    ("admin.style", "admin_style_button", "admin:style", "success", "nav"),
    ("admin.events", "events", "ev:menu", "primary", "nav"),
    ("admin.server", "admin_server", "menu:server", "primary", "nav"),
)
ADMIN_USERS_NAVIGATION = (
    ("admins.owner_id", "admin_owner_id_button", "noop", "primary", "nav"),
    ("admins.remove", "admin_remove_id_button", "adm:rm:*", "danger", "nav"),
    ("admins.add", "admins_add_button", "adm:add", "success", "nav"),
)
SERVER_NAVIGATION = (
    ("server.versions", "server_versions", "versions:server", "success", "versions"),
    ("server.status", "server_status", "server:status", "primary", "nav"),
    ("server.specs", "server_specs", "server:specs", "primary", "nav"),
    ("server.metrics", "server_metrics", "server:metrics", "primary", "nav"),
    ("server.chart", "server_chart", "server:chart", "primary", "nav"),
    ("server.logs", "server_logs", "server:logs", "primary", "nav"),
    ("server.diagnostics", "server_status", "server:diagnostics", "primary", "nav"),
    ("server.terminal", "server_terminal", "server:terminal", "primary", "nav"),
    ("server.approval", "server_approval", "server:approval", "success", "nav"),
    ("server.restart", "server_restart", "server:restart", "danger", "nav"),
    ("server.update", "server_update", "server:update", "primary", "nav"),
    ("server.rollback", "server_rollback", "server:rollback", "danger", "nav"),
)
DEVICE_SETTINGS_NAVIGATION = (
    ("settings.rename", "settings_rename_button", "devmg:rename:*", "primary", ["owner"]),
    ("settings.favorites", "settings_favorites_button", "fav:menu", "primary", ["owner", "user"]),
    ("settings.history", "settings_history_button", "hist:0", "primary", ["owner", "user"]),
    ("settings.versions", "settings_versions_button", "versions:device", "success", ["owner"]),
    ("settings.autorun", "settings_autorun_status_button", "cmd:autorun_status", "primary", ["owner", "user"]),
    ("settings.autorun_on", "settings_autorun_enable_button", "cmd:autorun_enable", "success", ["owner"]),
    ("settings.autorun_off", "settings_autorun_disable_button", "cmd:autorun_disable", "danger", ["owner"]),
    ("settings.remove", "settings_remove_device_button", "devmg:delete:*", "danger", ["owner"]),
    ("settings.block", "settings_block_device_button", "devmg:block:*", "danger", ["owner"]),
    ("settings.uninstall", "settings_uninstall_button", "devmg:uninstall:*", "danger", ["owner"]),
)
EVENTS_NAVIGATION = (
    ("events.online", "events_online_button", "ev:toggle:notify_online", "primary"),
    ("events.offline", "events_offline_button", "ev:toggle:notify_offline", "primary"),
    ("events.battery", "events_battery_button", "ev:toggle:notify_battery_low", "primary"),
    ("events.quiet", "events_quiet_hours_button", "ev:quiet", "primary"),
    ("events.digest", "events_digest_button", "ev:digest", "primary"),
    ("events.admins", "events_admins_button", "ev:admins", "primary"),
    ("events.server_autostart", "events_server_autostart_button", "ev:server_autostart:toggle", "primary"),
    ("events.hide_command_inputs", "events_hide_command_inputs_button", "ev:toggle:hide_command_inputs", "primary"),
    ("events.home", "home", "menu:main", "primary"),
)
QUIET_NAVIGATION = (
    ("quiet.off", "quiet_disable_button", "quiet::"),
    ("quiet.23_08", "quiet_2308_button", "quiet:23:8"),
    ("quiet.22_07", "quiet_2207_button", "quiet:22:7"),
    ("quiet.00_06", "quiet_0006_button", "quiet:0:6"),
    ("quiet.back", "events_back_button", "ev:menu"),
)
DIGEST_NAVIGATION = (
    ("digest.off", "digest_disable_button", "digest:"),
    ("digest.09", "digest_09_button", "digest:9"),
    ("digest.21", "digest_21_button", "digest:21"),
    ("digest.back", "events_back_button", "ev:menu"),
)
SCREEN_NAVIGATION = (
    ("screen.screenshot", "media_screenshot_button", "cmd:screenshot", "success"),
    ("screen.screenshot.preview", "feature.d02.f05.title", "screenshot:preview", "primary", ["owner"]),
    ("screen.screenshot.mask", "feature.d02.f04.title", "screenshot:mask", "primary", ["owner"]),
    ("screen.display.single", "feature.d02.f01.title", "display:screenshot", "primary", ["owner"]),
    ("screen.window.single", "feature.d02.f02.title", "window:screenshot", "primary", ["owner"]),
    ("screen.display.contact_sheet", "feature.d02.f06.title", "display:catalog", "primary", ["owner"]),
    ("screen.display.resolution", "feature.d02.f10.title", "display:resolution", "primary", ["owner"]),
    ("screen.media_temp.cleanup", "feature.d02.f23.title", "media:temp:cleanup", "primary", ["owner"]),
    ("screen.location", "geo_beta", "cmd:geo_location", "primary"),
    ("screen.webcam", "media_webcam_button", "camera:catalog", "success", ["owner"]),
    ("screen.camera_quality", "media_webcam_button", "camera:quality", "success", "camera_quality"),
    ("screen.mic", "media_mic_button", "mic:opts", "success"),
    ("screen.speak", "media_speak_button", "cmd:sound", "primary"),
    ("screen.volume", "media_volume_button", "vol:opts", "primary"),
    ("screen.mute", "media_mute_button", "cmd:volume", "primary"),
    ("screen.off", "screen_off_button", "fun:screenoff", "danger"),
    ("screen.saver", "screen_saver_button", "fun:screensaver", "primary"),
    ("screen.wallpaper", "screen_wallpaper_button", "menu:wallpaper", "success"),
    ("screen.brightness", "screen_brightness_button", "cmd:brightness", "success"),
    ("screen.nightlight", "screen_nightlight_disabled_button", "cmd:nightlight", "primary", ["owner", "user"], "screen_action"),
    ("screen.rotate", "screen_rotate_button", "cmd:rotate", "primary"),
)
INPUT_NAVIGATION = (
    ("input.clipboard", "input_clipboard_read_button", "cmd:clipboard", "success"),
    ("input.clipset", "input_clipboard_paste_button", "cmd:clipset", "primary"),
    ("input.type", "input_type_text_button", "fun:type", "primary"),
    ("input.hotkey", "input_hotkey_button", "fun:hotkey", "primary"),
    ("input.swapmouse", "input_mouse_swap_button", "prank:swapmouse", "primary"),
    ("input.crazycursor", "input_crazy_cursor_button", "prank:crazycursor", "primary"),
)
SYSTEM_NAVIGATION = (
    ("system.info", "system_sysinfo_button", "cmd:sysinfo", "primary"),
    ("system.uptime", "system_uptime_button", "cmd:sys_uptime", "success"),
    ("system.battery", "system_battery_button", "cmd:battery", "success"),
    ("system.disks", "system_disks_button", "cmd:disks", "primary"),
    ("system.smart", "system_smart_button", "cmd:smart", "success"),
    ("system.clean_temp", "system_clean_temp_button", "cmd:sys_clean_temp", "danger"),
    ("system.apps", "system_apps_button", "cmd:apps", "primary"),
    ("system.status", "system_refresh_status_button", "cmd:status", "success"),
    ("system.filevault", "feature.m01.f01.title", "filevault:status", "primary", ["owner"], "filevault_status"),
    ("system.sip", "feature.m01.f02.title", "sip:status", "primary", ["owner"], "sip_status"),
    ("system.gatekeeper", "feature.m01.f03.title", "gatekeeper:status", "primary", ["owner"], "gatekeeper_status"),
)
NETWORK_NAVIGATION = (
    ("network.external_ip", "network_external_ip_button", "fun:extip", "success"),
    ("network.ping", "network_ping_button", "cmd:net_ping", "success"),
    ("network.latency_series", "feature.d04.f01.title", "cmd:latency_series", "primary", ["owner"], "network_latency_series"),
    ("network.packet_loss_history", "feature.d04.f02.title", "cmd:packet_loss_history", "primary", ["owner"], "network_packet_loss_history"),
    ("network.jitter", "feature.d04.f03.title", "cmd:network_jitter", "primary", ["owner"], "network_jitter"),
    ("network.throughput", "feature.d04.f04.title", "cmd:network_throughput", "primary", ["owner"], "network_throughput"),
    ("network.link_speed", "feature.d04.f21.title", "cmd:network_link_speed", "primary", ["owner"], "network_link_speed"),
    ("network.route_lookup", "feature.d04.f22.title", "cmd:route_lookup", "primary", ["owner"], "network_route_lookup"),
    ("network.dns_resolution", "feature.d04.f05.title", "cmd:dns_resolution", "success", ["owner"], "network_dns_resolution"),
    ("network.dns_settings", "feature.d04.f06.title", "cmd:dns_settings", "primary", ["owner"], "network_dns_settings"),
    ("network.dns_comparison", "feature.d04.f07.title", "cmd:dns_comparison", "primary", ["owner"], "network_dns_comparison"),
    ("network.gateway_probe", "feature.d04.f08.title", "cmd:gateway_probe", "primary", ["owner"], "network_gateway_probe"),
    ("network.captive_portal", "feature.d04.f11.title", "cmd:captive_portal", "primary", ["owner"], "network_captive_portal"),
    ("network.ipv6_connectivity", "feature.d04.f12.title", "cmd:ipv6_connectivity", "primary", ["owner"], "network_ipv6_connectivity"),
    ("network.tls_certificate", "feature.d04.f13.title", "cmd:tls_certificate", "primary", ["owner"], "network_tls_certificate"),
    ("network.service_probe", "feature.d04.f14.title", "cmd:service_probe", "primary", ["owner"], "network_service_probe"),
    ("network.wifi_quality_history", "feature.d04.f15.title", "cmd:wifi_quality_history", "primary", ["owner"], "network_wifi_quality_history"),
    ("network.wifi_channel_summary", "feature.d04.f16.title", "cmd:wifi_channel_summary", "primary", ["owner"], "network_wifi_channel_summary"),
    ("network.wifi_connection_history", "feature.d04.f17.title", "cmd:wifi_connection_history", "primary", ["owner"], "network_wifi_connection_history"),
    ("network.proxy_status", "feature.d04.f23.title", "cmd:proxy_status", "primary", ["owner"], "network_proxy_status"),
    ("network.vpn_route_status", "feature.d04.f24.title", "cmd:vpn_route_status", "primary", ["owner"], "network_vpn_route_status"),
    ("network.endpoint_batch", "feature.d04.f25.title", "cmd:endpoint_batch", "primary", ["owner"], "network_endpoint_batch"),
    ("network.dnssec_check", "feature.d04.f26.title", "cmd:dnssec_check", "primary", ["owner"], "network_dnssec_check"),
    ("network.path_diagnostic", "feature.d04.f27.title", "cmd:path_diagnostic", "primary", ["owner"], "network_path_diagnostic"),
    ("network.check_sets", "feature.d04.f29.title", "cmd:network_check_sets", "primary", ["owner"], "network_check_sets"),
    ("network.external_ip_watch", "feature.d04.f30.title", "cmd:external_ip_watch", "primary", ["owner"], "network_external_ip_watch"),
    ("network.interface_traffic", "feature.d04.f18.title", "cmd:network_traffic", "primary", ["owner"], "network_interface_traffic"),
    ("network.route_trace", "feature.d04.f09.title", "cmd:route_trace", "primary", ["owner"], "network_route_trace"),
    ("network.path_mtu", "feature.d04.f10.title", "cmd:path_mtu", "primary", ["owner"], "network_path_mtu"),
    ("network.adapters", "network_adapters_button", "cmd:network", "primary"),
    ("network.wifi", "network_wifi_button", "cmd:wifi", "primary"),
    ("network.usb", "network_usb_button", "cmd:usb", "primary"),
    ("network.bluetooth", "network_bluetooth_button", "cmd:bluetooth", "primary"),
    ("network.netstat", "network_netstat_button", "cmd:netstat", "primary"),
    ("network.apps", "system_apps_button", "cmd:apps", "primary"),
    ("network.open_url", "network_open_url_button", "cmd:url", "success"),
    ("network.url_favorites", "url_favorites_button", "url:favorites", "primary", ["owner"]),
)
FILES_NAVIGATION = (
    ("files.list", "files_list_button", "files:list", "primary"),
    ("files.find", "files_find_button", "files:find", "primary"),
    ("files.download", "files_download_button", "files:get", "success"),
    ("files.upload", "files_upload_button", "files:put", "success"),
    ("files.open", "files_open_path_button", "files:open", "primary"),
    ("files.delete", "files_delete_button", "files:del", "danger"),
)
TERMINAL_NAVIGATION = (
    ("terminal.processes", "terminal_processes_button", "proc:0", "primary"),
    ("terminal.kill", "terminal_kill_process_button", "cmd:prockillname", "danger"),
    ("terminal.shell", "terminal_shell_button", "cmd:shell", "danger"),
    ("terminal.shell_runbook", "feature.d05.f22.title", "cmd:shell_runbook", "danger", "terminal_shell_runbook", ["owner"]),
    ("terminal.shell_runbook_templates", "feature.d05.f26.title", "cmd:shell_runbook_templates", "primary", "terminal_shell_runbook_templates", ["owner"]),
    ("terminal.shell_metadata_export", "feature.d05.f27.title", "cmd:shell_metadata_export", "primary", "terminal_shell_metadata_export", ["owner"]),
    ("terminal.shell_runner_diagnostic", "feature.d05.f29.title", "cmd:shell_runner_diagnostic", "primary", "terminal_shell_runner_diagnostic", ["owner"]),
    ("terminal.shell_late_results", "feature.d05.f30.title", "cmd:shell_late_results", "primary", "terminal_shell_late_results", ["owner"]),
    ("terminal.open_app", "terminal_open_app_button", "cmd:open_app", "primary"),
    ("terminal.input", "input", "cat:input", "primary", "nav"),
    ("terminal.startup", "terminal_startup_button", "cmd:startup", "primary"),
    ("terminal.services", "terminal_services_button", "cmd:services", "primary"),
    ("terminal.history", "terminal_history_button", "cmd:cmdhistory", "primary"),
    ("terminal.shell_timing", "feature.d05.f28.title", "cmd:shell_timing_report", "primary", "terminal_shell_timing", ["owner"]),
    ("terminal.shell_approval_inbox", "feature.d05.f19.title", "cmd:shell_approval_inbox", "primary", "terminal_shell_approval_inbox", ["owner"]),
)
POWER_NAVIGATION = (
    ("power.standby", "power_standby_agent_button", "cmd:standby_sleep", "primary", "awake"),
    ("power.wake", "power_wake_agent_button", "cmd:wake", "success", "standby"),
    ("power.lock", "power_lock_screen_button", "cmd:lock", "primary", None),
    ("power.sleep", "power_sleep_device_button", "power:sleep", "primary", None),
    ("power.reboot", "power_reboot_device_button", "power:reboot", "danger", None),
    ("power.shutdown", "power_shutdown_device_button", "power:shutdown", "danger", None),
    ("power.wol", "power_wol_button", "cmd:wol", "primary", None),
    ("power.autorun_status", "power_autorun_status_button", "cmd:autorun_status", "primary", None),
    ("power.autorun_enable", "power_autorun_enable_button", "cmd:autorun_enable", "success", None),
    ("power.autorun_disable", "power_autorun_disable_button", "cmd:autorun_disable", "danger", None),
    ("power.guardian", "power_guardian_menu_button", "cmd:guardian_menu", "primary", None),
    ("power.stop", "power_stop_agent_button", "cfm:stop", "danger", None),
)
GUARDIAN_NAVIGATION = (
    ("guardian.status", "guardian_status_button", "cmd:guardian_status", "primary"),
    ("guardian.start", "guardian_start_agent_button", "cmd:guardian_start", "success"),
    ("guardian.stop", "guardian_stop_agent_button", "cfm:guardian_stop", "danger"),
    ("guardian.restart", "guardian_restart_agent_button", "cmd:guardian_restart", "primary"),
    ("guardian.auto_on", "guardian_auto_enable_button", "cmd:guardian_auto_on", "success"),
    ("guardian.auto_off", "guardian_auto_disable_button", "cmd:guardian_auto_off", "danger"),
)
PRANK_NAVIGATION = (
    ("prank.visual", "prank_stop_all_button", "prank:stop_all", "danger"),
    ("prank.visual", "prank_screamer_button", "prank:screamer", "danger"),
    ("prank.visual", "prank_rickroll_button", "prank:rickroll50", "success"),
    ("prank.visual", "prank_matrix_button", "prank:matrix", "success"),
    ("prank.visual", "prank_bsod_button", "cmd:prank_bsod", "danger"),
    ("prank.visual", "prank_fake_update_button", "cmd:prank_fake_update", "primary"),
    ("prank.visual", "prank_rotate_screen_button", "cmd:prank_invert_screen", "primary"),
    ("prank.visual", "prank_hide_icons_button", "prank:hidedesktop", "primary"),
    ("prank.visual", "prank_window_dance_button", "prank:dancewin", "primary"),
    ("prank.visual", "prank_black_screen_button", "prank:blackscreen", "danger"),
    ("prank.visual", "prank_shake_window_button", "cmd:prank_shake_window", "primary"),
    ("prank.sound", "prank_stop_all_button", "prank:stop_all", "danger"),
    ("prank.sound", "prank_siren_button", "prank:siren", "danger"),
    ("prank.sound", "prank_shout_button", "prank:shout", "success"),
    ("prank.sound", "prank_morse_button", "cmd:prank_beep_morse", "primary"),
    ("prank.sound", "prank_spooky_button", "cmd:prank_sound_spooky", "danger"),
    ("prank.sound", "prank_fart_button", "cmd:prank_sound_fart", "primary"),
    ("prank.sound", "prank_volume_jump_button", "cmd:prank_volume_jump", "primary"),
    ("prank.sound", "prank_speak_time_button", "cmd:prank_speak_time", "success"),
    ("prank.sound", "prank_whisper_button", "cmd:prank_say_whisper", "danger"),
    ("prank.sound", "prank_laugh_track_button", "cmd:prank_laugh_track", "primary"),
    ("prank.sound", "prank_random_beeps_button", "cmd:prank_random_beeps", "primary"),
    ("prank.input", "prank_stop_all_button", "prank:stop_all", "danger"),
    ("prank.input", "prank_mouse_swap_button", "prank:swapmouse", "primary"),
    ("prank.input", "prank_crazy_cursor_button", "prank:crazycursor", "primary"),
    ("prank.input", "prank_slow_mouse_button", "cmd:prank_slow_mouse", "primary"),
    ("prank.input", "prank_glitch_cursor_button", "cmd:prank_glitch_cursor", "primary"),
    ("prank.input", "prank_random_clicks_button", "cmd:prank_random_clicks", "primary"),
    ("prank.input", "prank_keyboard_disco_button", "cmd:prank_keyboard_disco", "success"),
    ("prank.input", "prank_caps_disco_button", "cmd:prank_caps_disco", "primary"),
    ("prank.input", "prank_cursor_circle_button", "cmd:prank_cursor_circle", "primary"),
    ("prank.input", "prank_notepad_type_button", "cmd:prank_open_notepad_type", "primary"),
    ("prank.input", "prank_hacker_typer_button", "cmd:prank_hacker_typer", "success"),
    ("prank.chaos", "prank_stop_all_button", "prank:stop_all", "danger"),
    ("prank.chaos", "prank_spam_windows_button", "fun:spam", "danger"),
    ("prank.chaos", "prank_calculator_spam_button", "cmd:prank_open_calc_spam", "primary"),
    ("prank.chaos", "prank_fake_virus_button", "cmd:prank_fake_virus", "danger"),
    ("prank.chaos", "prank_alert_loop_button", "cmd:prank_alert_loop", "primary"),
    ("prank.chaos", "prank_clipboard_spam_button", "cmd:prank_paste_clipboard_spam", "primary"),
    ("prank.chaos", "prank_reverse_clipboard_button", "cmd:prank_type_reversed", "primary"),
    ("prank.chaos", "prank_error_spam_button", "cmd:prank_fake_error_spam", "danger"),
    ("prank.chaos", "prank_fake_sys32_button", "cmd:prank_fake_delete_sys32", "danger"),
    ("prank.chaos", "prank_ghost_typer_button", "cmd:prank_ghost_typer", "primary"),
    ("prank.chaos", "prank_earthquake_button", "cmd:prank_earthquake", "danger"),
    ("prank.memes", "prank_stop_all_button", "prank:stop_all", "danger"),
    ("prank.memes", "prank_meme_wallpaper_button", "cmd:prank_meme_wallpaper", "success"),
    ("prank.memes", "prank_cat_invaders_button", "cmd:prank_cat_invaders", "primary"),
    ("prank.memes", "prank_fake_ransom_cats_button", "cmd:prank_fake_ransom_cats", "danger"),
    ("prank.memes", "prank_nyan_stream_button", "cmd:prank_nyan_stream", "success"),
    ("prank.memes", "prank_confetti_winner_button", "cmd:prank_confetti_winner", "success"),
    ("prank.memes", "prank_fake_low_battery_button", "cmd:prank_low_battery_fake", "danger"),
    ("prank.memes", "prank_fbi_lock_button", "cmd:prank_fbi_lock", "danger"),
    ("prank.memes", "prank_browser_memes_button", "cmd:prank_open_browser_memes", "success"),
    ("prank.memes", "prank_ascii_rickroll_button", "cmd:prank_rickroll_terminal", "success"),
    ("prank.memes", "prank_random_site_button", "prank:randomsite", "primary"),
)
PRANK_TABS = (
    ("prank.tabs.visual", "prank_tab_visual", "prankpage:1"),
    ("prank.tabs.sound", "prank_tab_sound", "prankpage:2"),
    ("prank.tabs.input", "prank_tab_input", "prankpage:3"),
    ("prank.tabs.chaos", "prank_tab_chaos", "prankpage:4"),
    ("prank.tabs.memes", "prank_tab_memes", "prankpage:5"),
)
HOME_NAVIGATION = (
    ("home.devices", "devices", "menu:devices", ["owner", "user", "guest"], "home_devices"),
    ("home.server", "server", "menu:server", ["owner", "user", "guest"], "home_server"),
    ("home.admin", "admin", "menu:admin", ["owner"], "home_admin"),
    ("home.access_request", "request_access_button", "access:request", ["user"], "home_access_request"),
    ("home.catalog", "catalog", "catalog:areas", ["owner", "user", "guest"], "home_catalog"),
    ("home.about", "about", "menu:about", ["owner", "user", "guest"], "home_about"),
)
FEATURE_IMPLEMENTATION_OVERRIDES = {
    "D03-F27": {
        # The inbox has source and integration coverage; a real device placement
        # flow is still required before this can be called fully verified.
        "support_state": "needs_device_test",
        "handler_id": "d03.f27.files.owner_staged_inbox",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_file_inbox.py::test_staged_file_is_owner_only_and_returns_metadata_not_storage_path",
            "TG-BOT-SERVER/tests/test_file_inbox.py::test_staged_file_limits_expiry_and_owner_scoped_delete",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_file_inbox_is_owner_only_and_uses_registered_actions",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_staged_file_placement_requires_preview_and_explicit_confirmation",
        ],
    },
    "D05-F18": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f18.terminal.owner_task_capacity",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F18 novelty audit (2026-10-21)",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_command_queue.py::test_global_task_cap_fairly_queues_across_devices_and_releases_capacity",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_tasks_across_devices_wait_for_owner_capacity_in_same_card",
        ],
    },
    "D05-F17": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f17.terminal.device_command_queue",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F17 novelty audit (2026-10-21)",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_command_queue.py::test_fifo_is_per_device_and_release_wakes_next_ticket",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_approved_shell_commands_for_one_device_wait_in_fifo_and_show_position",
        ],
    },
    "D05-F15": {
        # The bot parses only the already-redacted text; physical cross-platform
        # acceptance remains a separate gate.
        "support_state": "needs_device_test",
        "handler_id": "d05.f15.terminal.json_fields",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F15 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_result_pager.py::test_json_fields_are_flattened_only_after_redaction_and_bounded",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_valid_json_shell_result_can_switch_between_fields_and_redacted_source",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_secret_redaction_happens_before_json_view_detection",
        ],
    },
    "D04-F01": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f01.network.latency_series",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F01 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_latency_series.py::test_agent_publishes_correlated_series_from_fixed_five_attempt_contract",
            "XGENT-MCS/tests/test_latency_series.py::test_agent_publishes_correlated_series_from_fixed_five_attempt_contract",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_latency_series_owner_flow_is_correlated_and_edits_existing_card",
        ],
    },
    "D04-F02": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f02.network.packet_loss_history",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F02 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_packet_loss_history.py::test_agent_publishes_correlated_packet_loss_history",
            "XGENT-MCS/tests/test_packet_loss_history.py::test_agent_publishes_correlated_packet_loss_history",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_packet_loss_history_owner_flow_is_single_device_owner_only_and_correlated",
        ],
    },
    "D04-F03": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f03.network.jitter",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F03 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_jitter_measurement.py::test_agent_publishes_correlated_icmp_jitter_result",
            "XGENT-MCS/tests/test_jitter_measurement.py::test_agent_publishes_correlated_icmp_jitter_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_jitter_owner_flow_is_single_device_owner_only_and_correlated",
        ],
    },
    "D04-F04": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f04.network.throughput",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F04 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "XGENT-WDS/tests/test_network_throughput.py::test_agent_publishes_correlated_throughput_result",
            "XGENT-MCS/tests/test_network_throughput.py::test_agent_publishes_correlated_throughput_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_throughput_owner_flow_is_single_device_owner_only_and_correlated",
        ],
    },
    "D04-F21": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f21.network.link_speed",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F21 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "XGENT-WDS/tests/test_network_link_speed.py::test_agent_publishes_correlated_link_speed_result",
            "XGENT-MCS/tests/test_network_link_speed.py::test_agent_publishes_correlated_link_speed_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_link_speed_owner_flow_selects_one_interface_and_edits_card",
        ],
    },
    "D04-F22": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f22.network.route_lookup",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F22 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "XGENT-WDS/tests/test_route_lookup.py::test_agent_publishes_correlated_route_lookup",
            "XGENT-MCS/tests/test_route_lookup.py::test_agent_publishes_correlated_route_lookup",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_route_lookup_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D05-F28": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f28.terminal.shell_timing_report",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F28 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_shell_timing_summary_groups_only_the_two_approved_intervals",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_timing_report_is_owner_only_aggregate_and_edits_one_card",
        ],
    },
    "D05-F29": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f29.terminal.shell_runner_diagnostic",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F29",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "XGENT-MCS/tests/test_capabilities.py::test_shell_runner_probe_checks_filesystem_only_and_never_starts_a_process",
            "XGENT-MCS/tests/test_capabilities.py::test_capabilities_response_includes_status_and_legacy_fields",
            "XGENT-WDS/tests/test_capabilities.py::test_capabilities_include_passive_shell_runner_diagnostics",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_shell_runner_diagnostic_never_turns_missing_or_malformed_fields_into_ready",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_runner_diagnostic_is_owner_only_and_uses_fresh_capability_reply",
        ],
    },
    "D05-F19": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f19.terminal.shell_approval_inbox",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F19 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_command_approval.py::test_pending_inbox_lists_only_fresh_requests_for_the_captured_owner",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_approval_inbox_is_owner_only_read_only_and_shows_exact_preview",
        ],
    },
    "D05-F20": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f20.terminal.cooperative_cancel",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F20 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "XGENT-MCS/tests/test_shell_cancellation.py::test_cancel_shell_stops_only_matching_active_request_and_reports_confirmation",
            "XGENT-WDS/tests/test_shell_approval_gate.py::test_shell_cancel_handler_is_registered_and_reports_unknown_without_echoing_command",
            "XGENT-MCS/tests/test_shell_approval_gate.py::test_shell_cancel_handler_is_registered_and_reports_unknown_without_echoing_command",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_running_card_offers_owner_stop_and_reports_only_correlated_confirmation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_stop_unknown_when_response_is_missing",
        ],
    },
    "D05-F21": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f21.terminal.dry_run_preview",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F21 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_dry_run.py::test_builds_native_dry_run_for_allowlisted_commands",
            "TG-BOT-SERVER/tests/test_command_approval.py::test_dry_run_replacement_invalidates_original_and_keeps_original_expiry",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_dry_run_replaces_preview_then_requires_fresh_owner_approval",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_approval_offers_dry_run_only_for_supported_mutating_commands",
        ],
    },
    "D05-F22": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f22.terminal.owner_runbook",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F22 ruling and baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_runbook.py::test_runbook_starts_only_after_owner_confirmation_and_tracks_exact_order",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_confirmation_creates_only_first_exact_owner_approval",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_success_requires_fresh_approval_for_next_step_and_failure_stops",
        ],
    },
    "D05-F23": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f23.terminal.runbook_step_report",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F23 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_runbook.py::test_runbook_report_records_terminal_outcome_duration_and_exit_code_without_commands",
            "TG-BOT-SERVER/tests/test_shell_runbook.py::test_runbook_report_marks_unknown_result_and_unstarted_step_truthfully",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_success_requires_fresh_approval_for_next_step_and_failure_stops",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_runbook_nonzero_exit_stops_without_creating_next_pending_step",
        ],
    },
    "D05-F24": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f24.terminal.shell_result_compare",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F24 baseline audit (2026-10-22)",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_result_compare.py::test_manual_selection_compares_only_redacted_results_and_is_single_use",
            "TG-BOT-SERVER/tests/test_shell_result_compare.py::test_selection_is_owner_scoped_and_expired_result_cannot_be_compared",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_result_comparison_requires_two_manual_owner_selections",
        ],
    },
    "D05-F25": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f25.terminal.repeat_request",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F25 baseline audit (2026-10-22)",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_repeat.py::test_repeat_is_owner_scoped_short_lived_and_consumed_once",
            "TG-BOT-SERVER/tests/test_shell_repeat.py::test_repeat_candidates_require_a_terminal_execution_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_repeat_creates_a_fresh_approval_without_publishing",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_repeat_callback_route_is_owner_gated",
        ],
    },
    "D05-F26": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f26.terminal.shell_runbook_templates",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F26",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_shell_runbook_templates_are_owner_only_persistent_and_separate_from_execution_plans",
            "TG-BOT-SERVER/tests/test_device_store.py::test_shell_runbook_template_rename_and_delete_change_only_template_library",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_runbook_template_library_is_owner_only_and_visible_in_terminal_menu",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_using_saved_runbook_template_only_creates_draft_and_keeps_step_approval",
        ],
    },
    "D05-F27": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f27.terminal.shell_metadata_export",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F27",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_shell_audit_export_filters_dates_and_status_without_command_content",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_shell_audit_export_rejects_non_owner_invalid_status_and_unbounded_range",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_metadata_export_parser_requires_bounded_date_range_and_known_statuses",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_metadata_export_delivers_owner_scoped_filtered_json_without_command_data",
        ],
    },
    "D05-F16": {
        "support_state": "implemented",
        "handler_id": "d05.f16.terminal.redactor_summary",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F16 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_shell_redactor_summary_counts_real_categories_without_storing_secret_values",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_result_reports_only_redaction_counts_and_categories",
        ],
    },
    "D05-F14": {
        "support_state": "implemented",
        "handler_id": "d05.f14.terminal.redacted_result_preview",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F14 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_shell_redacted_output_requires_owner_preview_tap",
        ],
    },
    "D05-F13": {
        "support_state": "needs_device_test",
        "handler_id": "d05.f13.terminal.result_pagination",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D05-F13 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_shell_result_pager.py::test_result_pages_are_bounded_owner_scoped_and_expire",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_long_shell_result_pages_through_the_same_owner_card",
        ],
    },
    "D04-F05": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f05.network.dns_resolution",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_dns_diagnostics.py::test_agent_publishes_correlated_dns_result_and_does_not_connect_to_addresses",
            "XGENT-MCS/tests/test_dns_diagnostics.py::test_agent_publishes_correlated_dns_result_and_does_not_connect_to_addresses",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_dns_resolution_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F06": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f06.network.dns_settings",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_dns_settings.py::test_agent_publishes_correlated_private_dns_snapshot",
            "XGENT-MCS/tests/test_dns_settings.py::test_agent_publishes_correlated_private_dns_snapshot",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_dns_settings_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F07": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f07.network.dns_comparison",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_dns_comparison.py::test_agent_publishes_correlated_dns_comparison",
            "XGENT-MCS/tests/test_dns_comparison.py::test_agent_publishes_correlated_dns_comparison",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_dns_comparison_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F08": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f08.network.gateway_probe",
        "acceptance_level": ["unit", "integration", "device"],
        "test_refs": [
            "XGENT-WDS/tests/test_gateway_probe.py::test_agent_publishes_correlated_gateway_probe_result",
            "XGENT-MCS/tests/test_gateway_probe.py::test_agent_publishes_correlated_gateway_probe_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_gateway_probe_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F09": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f09.network.route_trace",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_route_trace.py::test_agent_publishes_correlated_route_trace",
            "XGENT-MCS/tests/test_route_trace.py::test_agent_publishes_correlated_route_trace",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_route_trace_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F10": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f10.network.path_mtu",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_path_mtu.py::test_agent_publishes_correlated_path_mtu_result",
            "XGENT-MCS/tests/test_path_mtu.py::test_agent_publishes_correlated_path_mtu_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_path_mtu_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F11": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f11.network.captive_portal",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_captive_portal.py::test_agent_publishes_correlated_captive_portal_status",
            "XGENT-MCS/tests/test_captive_portal.py::test_agent_publishes_correlated_captive_portal_status",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_captive_portal_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F12": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f12.network.ipv6_connectivity",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_ipv6_connectivity.py::test_agent_publishes_correlated_ipv6_reachability",
            "XGENT-MCS/tests/test_ipv6_connectivity.py::test_agent_publishes_correlated_ipv6_reachability",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_ipv6_connectivity_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D04-F13": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f13.network.tls_certificate",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_tls_certificate.py::test_agent_publishes_correlated_certificate_result",
            "XGENT-MCS/tests/test_tls_certificate.py::test_agent_publishes_correlated_certificate_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_tls_certificate_is_owner_only_correlated_and_replaces_the_current_card",
        ],
    },
    "D04-F14": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f14.network.service_probe",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_service_probe.py::test_agent_publishes_correlated_service_status",
            "XGENT-MCS/tests/test_service_probe.py::test_agent_publishes_correlated_service_status",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_service_probe_is_owner_only_and_replaces_the_current_card",
        ],
    },
    "D04-F15": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f15.network.wifi_quality_history",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_quality_sample_without_network_identity",
            "XGENT-MCS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_quality_sample_without_network_identity",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_wifi_quality_history_is_owner_only_persists_one_sample_and_edits_card",
        ],
    },
    "D04-F16": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f16.network.wifi_channel_summary",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_channel_summary",
            "XGENT-MCS/tests/test_wifi_quality.py::test_agent_publishes_correlated_wifi_channel_summary",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_wifi_channel_summary_is_owner_only_correlated_and_replaces_the_current_card",
        ],
    },
    "D04-F17": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f17.network.wifi_connection_history",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_wifi_connection_history.py::test_agent_publishes_correlated_wifi_connection_history_without_event_payloads",
            "XGENT-MCS/tests/test_wifi_connection_history.py::test_agent_publishes_correlated_wifi_connection_history_without_event_payloads",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_wifi_connection_history_is_owner_only_and_edits_the_same_card",
        ],
    },
    "D04-F23": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f23.network.proxy_status",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F23 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_proxy_status.py::test_agent_publishes_correlated_redacted_proxy_status",
            "XGENT-MCS/tests/test_proxy_status.py::test_agent_publishes_correlated_redacted_proxy_status",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_proxy_status_is_owner_only_and_edits_the_current_card",
        ],
    },
    "D04-F24": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f24.network.vpn_route_status",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F24 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_vpn_route.py::test_agent_publishes_correlated_vpn_route_summary",
            "XGENT-MCS/tests/test_vpn_route.py::test_agent_publishes_correlated_vpn_route_summary",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_vpn_route_status_is_owner_only_and_edits_the_current_card",
        ],
    },
    "D04-F25": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f25.network.endpoint_batch",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F25 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_endpoint_batch.py::test_agent_publishes_correlated_bounded_endpoint_set",
            "XGENT-MCS/tests/test_endpoint_batch.py::test_agent_publishes_correlated_bounded_endpoint_set",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_endpoint_batch_is_owner_only_fixed_and_edits_the_current_card",
        ],
    },
    "D04-F26": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f26.network.dnssec_check",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F26",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_dnssec_check.py::test_agent_publishes_correlated_dnssec_assessment",
            "XGENT-MCS/tests/test_dnssec_check.py::test_agent_publishes_correlated_dnssec_assessment",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_dnssec_check_is_owner_only_and_edits_current_card",
        ],
    },
    "D04-F27": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f27.network.path_diagnostic",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F27 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_path_diagnostic_runs_fixed_correlated_probes_and_edits_one_card",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_path_diagnostic_labels_only_confirmed_failures",
        ],
    },
    "D04-F28": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f28.network.report_export",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F28 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_path_report_is_editable_json_with_allowlisted_fields_only",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_path_report_export_is_owner_bound_and_consumed_once",
        ],
    },
    "D04-F29": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f29.network.check_sets",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F29 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_network_check_sets_are_owner_only_bounded_and_persist",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_check_set_callbacks_are_owner_only_and_registered",
        ],
    },
    "D04-F30": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f30.network.external_ip_watch",
        "baseline_evidence": [{
            "path": SPEC,
            "reference": "D04-F30 baseline audit",
            "novelty_status": "confirmed_absent",
        }],
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_external_ip_watch_is_owner_opt_in_persistent_and_queues_changes",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_external_ip_watch_callbacks_are_owner_only_and_registered",
            "XGENT-WDS/tests/test_wds.py::test_external_ip_response_is_correlated_and_rejects_non_public_ip",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_external_ip_response_correlates_public_result_and_hides_provider_error",
        ],
    },
    "D04-F18": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f18.network.interface_traffic",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_network_traffic.py::test_windows_agent_publishes_correlated_selected_adapter_traffic",
            "XGENT-MCS/tests/test_network_traffic.py::test_macos_agent_publishes_correlated_selected_adapter_traffic",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_traffic_is_owner_only_and_edits_the_same_card",
        ],
    },
    "D04-F19": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f19.network.traffic_threshold_alert",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_network_traffic.py::test_windows_agent_acknowledges_owner_selected_threshold_schedule",
            "XGENT-MCS/tests/test_network_traffic.py::test_macos_agent_acknowledges_owner_selected_threshold_schedule",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_network_traffic_alert_sample_requires_exact_fresh_selected_interface",
        ],
    },
    "D04-F20": {
        "support_state": "needs_device_test",
        "handler_id": "d04.f20.network.interface_status_history",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_interface_status_history.py::test_history_records_only_verified_state_transitions_and_bounds_rows",
            "XGENT-MCS/tests/test_interface_status_history.py::test_history_records_only_verified_state_transitions_and_bounds_rows",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_interface_status_history_validator_requires_fresh_exact_transition_rows",
        ],
    },
    "D02-F22": {
        # Cooperative cancellation is unit/integration-tested; physical device
        # and live Telegram/MQTT acceptance remain explicit release gates.
        "support_state": "needs_device_test",
        "handler_id": "d02.f22.media.request.cancel",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "XGENT-WDS/tests/test_media_request_control.py::test_agent_media_cancel_returns_correlated_truthful_receipt",
            "XGENT-MCS/tests/test_media_request_control.py::test_agent_media_cancel_returns_correlated_truthful_receipt",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_media_series_cancel_is_owner_only_correlated_and_suppresses_preview",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_series_cancellation_withholds_completed_preview",
        ],
    },
    "A01-F01": {
        # Explicit owner-confirmed 24-hour device/action grants; local tests
        # cover expiry, RBAC, and stale/consumed confirmation behavior.
        "support_state": "needs_device_test",
        "handler_id": "a01.f01.admin.temporary_grant",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_temporary_grant_explicitly_sets_expiry_without_toggling_existing_access",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_grant_menus_offer_owner_only_temporary_grant_entry",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_temporary_admin_grant_requires_owner_confirmation_and_expires",
        ],
    },
    "A01-F04": {
        # The owner can inspect a target account's effective navigation without
        # signing in as that user or invoking any represented callback.
        "support_state": "needs_device_test",
        "handler_id": "a01.f04.admin.simulate_user_access",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_profile_simulates_only_effective_device_actions",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_profile_simulation_denies_blocked_user",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_access_simulation_matches_guest_read_only_device_menu",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_access_simulation_matches_owner_device_actions",
        ],
    },
    "A01-F03": {
        # Reusable profiles are snapshots of effective, existing device and
        # callback grants; application remains a separate owner confirmation.
        "support_state": "needs_device_test",
        "handler_id": "a01.f03.admin.permission_profiles",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_named_permission_profile_captures_only_effective_grants_and_is_owner_only",
            "TG-BOT-SERVER/tests/test_access_store.py::test_named_permission_profile_rejects_invalid_names_and_unknown_source_grants",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_named_permission_profiles_are_owner_only_and_reuse_the_confirmed_grant_flow",
        ],
    },
    "A01-F05": {
        # Callback denials expose only the first policy reason, never the
        # account's full grant list or device inventory.
        "support_state": "needs_device_test",
        "handler_id": "a01.f05.rbac.denial_reason",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_access_explanation_reports_nonsecret_rbac_denial_without_changing_policy",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_callback_access_denial_shows_the_rbac_reason_without_running_action",
        ],
    },
    "A01-F06": {
        # Historical grants without issuance metadata remain explicitly
        # unknown; new authorized actions update only their exact grant scope.
        "support_state": "needs_device_test",
        "handler_id": "a01.f06.admin.grant_review",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_review_distinguishes_expired_unused_and_unknown_history",
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_use_records_only_the_authorized_device_and_action",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_filter_records_only_the_grant_used_by_an_allowed_callback",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_list_surfaces_expired_grants_for_owner_review",
        ],
    },
    "A01-F07": {
        # The owner confirmation previews only the currently effective scope;
        # the read-only preview never mutates the grant or its audit trail.
        "support_state": "needs_device_test",
        "handler_id": "a01.f07.admin.grant_revocation_preview",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_revocation_preview_lists_exact_affected_scope_without_mutation",
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_revocation_preview_ignores_expired_grants",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_revocation_confirmation_shows_affected_grants_without_mutating",
        ],
    },
    "A01-F08": {
        # Temporary blocks retain an owner-entered reason and can be extended;
        # expiration restores the exact pre-block role without granting new rights.
        "support_state": "needs_device_test",
        "handler_id": "a01.f08.admin.temporary_block",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_temporary_block_preserves_reason_expires_and_can_be_extended",
            "TG-BOT-SERVER/tests/test_access_store.py::test_temporary_block_rejects_invalid_expiry_or_reason_without_mutation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_temporary_user_block_requires_reason_preview_and_one_use_confirmation",
        ],
    },
    "A01-F09": {
        # Both permanent and temporary block confirmations expose the exact
        # effective grant impact and restored role before the one-use decision.
        "support_state": "needs_device_test",
        "handler_id": "a01.f09.admin.block_impact_preview",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_block_confirmation_shows_the_access_it_will_revoke",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_temporary_user_block_requires_reason_preview_and_one_use_confirmation",
        ],
    },
    "A01-F10": {
        # Reminders are sent once to the grantee for each unchanged expiry;
        # Telegram delivery and real-user acceptance remain external gates.
        "support_state": "needs_device_test",
        "handler_id": "a01.f10.access.expiry_reminder",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_expiring_grants_are_listed_once_claimed_and_reissued_after_extension",
            "TG-BOT-SERVER/tests/test_access_store.py::test_expiry_reminder_claim_skips_revoked_blocked_or_expired_permission",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_expiring_grant_reminder_sends_one_plain_action_and_marks_sent",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_expiring_grant_delivery_failure_releases_claim_for_retry",
        ],
    },
    "A01-F11": {
        # Role transitions are read only, owner-scoped and sourced from typed
        # audit events; legacy events without an exact resulting role remain unknown.
        "support_state": "needs_device_test",
        "handler_id": "a01.f11.admin.user_role_history",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_can_review_exact_role_history_for_one_user",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_detail_has_owner_only_role_history_route",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_role_history_renders_only_target_role_events_on_same_card",
        ],
    },
    "A01-F12": {
        # The owner sees typed, target-scoped grant/revoke events only; no raw
        # general-audit details are exposed in the per-user profile.
        "support_state": "needs_device_test",
        "handler_id": "a01.f12.admin.user_grant_history",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_grant_history_is_typed_target_scoped_and_includes_expiry",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_detail_has_owner_only_grant_history_route",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_grant_history_renders_typed_access_events_on_same_card",
        ],
    },
    "A01-F13": {
        # The owner-only filter reads only bounded, already-redacted audit rows;
        # physical Telegram acceptance remains a release gate.
        "support_state": "needs_device_test",
        "handler_id": "a01.f13.admin.audit.filter",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_audit_filter_matches_inclusive_time_actor_type_and_device",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_audit_filter_ignores_malformed_rows_and_rejects_invalid_ranges",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audit_filter_parser_accepts_optional_exact_filters",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audit_filter_keyboard_is_registry_driven_and_has_owner_back_route",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audit_renderer_skips_bad_timestamps_escapes_values_and_bounds_card",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_audit_filter_text_edits_one_owner_card_with_filtered_rows",
        ],
    },
    "A01-F14": {
        # Event cards are owner-only fixed projections; raw audit detail is never rendered.
        "support_state": "needs_device_test",
        "handler_id": "a01.f14.admin.audit.event_detail",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_audit_event_is_normalized_and_never_returns_raw_detail_or_credentials",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_audit_event_does_not_expose_freeform_legacy_detail",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audit_keyboard_adds_opaque_event_buttons_without_raw_detail",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_audit_event_opens_only_normalized_owner_projection",
        ],
    },
    "A01-F15": {
        # Export is explicitly date-bounded, capped, and serialized from the
        # normalized event projection; physical Telegram acceptance remains open.
        "support_state": "needs_device_test",
        "handler_id": "a01.f15.admin.audit.export",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_audit_export_is_range_limited_redacted_and_capped",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audit_export_parser_requires_bounded_inclusive_utc_date_range",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_audit_export_delivers_only_bounded_normalized_json",
        ],
    },
    "A01-F16": {
        # Conflict analysis is shown in the existing owner one-use grant
        # confirmation, before any persistent permission change.
        "support_state": "needs_device_test",
        "handler_id": "a01.f16.admin.permission.conflict_preview",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_conflict_preview_detects_duplicate_coverage_and_broader_overlap_without_mutation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_grant_confirmation_previews_overlapping_access_before_owner_decision",
        ],
    },
    "A01-F17": {
        # A read-only owner report compares active grants against the exact
        # current device inventory and never revokes or edits access.
        "support_state": "needs_device_test",
        "handler_id": "a01.f17.admin.device_grants.stale_scan",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_stale_device_grant_scan_uses_exact_inventory_and_skips_expired_rights",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_stale_device_grants_screen_shows_only_missing_registry_ids",
        ],
    },
    "A01-F18": {
        # The owner explicitly pages through a current, read-only access
        # snapshot; viewing or refreshing never changes roles or grants.
        "support_state": "needs_device_test",
        "handler_id": "a01.f18.admin.monthly_access_review",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_monthly_owner_access_review_is_read_only_exact_and_paginated",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_monthly_access_review_is_owner_only_paginated_and_read_only",
        ],
    },
    "A01-F19": {
        # A bounded, exact bulk grant is staged as a one-use owner decision;
        # applying it rechecks the target snapshot and exact device inventory.
        "support_state": "needs_device_test",
        "handler_id": "a01.f19.admin.bulk_grant.preview",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_bulk_grant_preview_is_exact_read_only_and_confirmation_rejects_stale_targets",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_grant_admin_entry_uses_registry_label_and_exact_callback",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_grant_confirmation_applies_once_and_reports_exact_counts",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_grant_input_stages_exact_allowlisted_targets_before_confirmation",
        ],
    },
    "A01-F20": {
        # Dry-run lists exact users and currently active callback grants; the
        # one-use owner confirmation rechecks the complete target snapshot.
        "support_state": "needs_device_test",
        "handler_id": "a01.f20.admin.bulk_revoke.dry_run",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_bulk_revoke_dry_run_lists_exact_users_and_callbacks_and_rechecks_before_apply",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_revoke_input_stages_exact_callbacks_for_dry_run",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_revoke_confirmation_applies_exact_dry_run_once",
        ],
    },
    "A01-F21": {
        # Optional shared expiration is applied only to newly added rights;
        # existing grants and their current expiry are preserved.
        "support_state": "needs_device_test",
        "handler_id": "a01.f21.admin.bulk_grant.expiry",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_bulk_grant_expiry_applies_to_new_group_rights_without_shortening_existing_access",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_grant_input_accepts_an_expiry_choice_before_preview",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_bulk_grant_confirmation_applies_shared_expiry_and_reports_deadline",
        ],
    },
    "A01-F22": {
        # A user may submit one exact callback/device request; it is persisted
        # and owner-notified but never changes effective grants.
        "support_state": "needs_device_test",
        "handler_id": "a01.f22.permission_request.submit",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_user_permission_request_is_owner_bound_deduplicated_and_never_grants",
            "TG-BOT-SERVER/tests/test_access_store.py::test_permission_request_rejects_guest_blocked_owner_and_unregistered_targets",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_user_permission_request_is_submitted_and_not_granted",
        ],
    },
    "A01-F23": {
        # Owner queue decisions are one-shot, audited, and grant only the
        # exact requested item for 24h; stale role/device state fails closed.
        "support_state": "needs_device_test",
        "handler_id": "a01.f23.permission_request.decide",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_decides_exact_permission_request_once_and_approval_expires",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_can_reject_request_without_grant_and_cannot_approve_stale_device",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_permission_request_queue_reviews_and_applies_exact_expiring_grant",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_non_owner_cannot_decide_permission_request_through_callback",
        ],
    },
    "A01-F24": {
        # Revoke all stored/effective device and callback grants for one user
        # after a one-use owner confirmation bound to an exact preview.
        "support_state": "needs_device_test",
        "handler_id": "a01.f24.admin.user_revoke_all_grants",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_revokes_all_target_grants_only_after_exact_snapshot_confirmation",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_bulk_user_grant_revocation_rejects_stale_and_owner_targets",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_detail_exposes_owner_only_confirmed_revoke_all_grants",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_revokes_all_user_grants_through_one_use_confirmation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_non_owner_cannot_preview_all_grant_revocation",
        ],
    },
    "A01-F25": {
        # Pause preserves stored grants, but requires an exact owner preview
        # before either pausing or resuming; expired/stale-device grants stay ineffective.
        "support_state": "needs_device_test",
        "handler_id": "a01.f25.admin.user_grant_pause",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_pause_preserves_saved_rights_and_owner_confirmation_resumes_current_rights",
            "TG-BOT-SERVER/tests/test_access_store.py::test_grant_pause_resume_fails_when_preview_or_live_device_inventory_changes",
            "TG-BOT-SERVER/tests/test_access_store.py::test_resuming_grants_drops_device_permissions_missing_from_confirmed_inventory",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_user_detail_exposes_owner_only_confirmed_grant_pause",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_pauses_and_resumes_user_grants_through_exact_one_use_preview",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_non_owner_cannot_pause_user_grants",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_resume_preview_warns_that_missing_device_grants_need_reissue",
        ],
    },
    "A01-F26": {
        # Every staged per-user role/action-grant change previews the exact
        # registry-backed Telegram navigation buttons added and removed.
        "support_state": "needs_device_test",
        "handler_id": "a01.f26.admin.user_access_navigation_diff",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_access_navigation_diff_reports_added_and_removed_buttons",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_confirmation_preview_uses_effective_grants_after_expiry",
        ],
    },
    "A01-F27": {
        # Read-only check that the active Telegram identity and stored owner row
        # do not contradict the protected configured owner ID.
        "support_state": "implemented",
        "handler_id": "a01.f27.admin.owner_invariant",
        "acceptance_level": ["unit", "integration", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_invariant_report_detects_owner_substitution_and_stored_block",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_invariant_is_owner_only_in_selected_user_menu",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_invariant_handler_reports_only_owner_configuration_state",
        ],
    },
    "A01-F28": {
        # Owner-only, immutable-by-API snapshot of effective and stored RBAC
        # grants with a versioned schema and canonical SHA-256 digest.
        "support_state": "implemented",
        "handler_id": "a01.f28.admin.rbac_policy_snapshot",
        "acceptance_level": ["unit", "integration", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_rbac_policy_snapshot_is_versioned_secret_free_and_verifiable",
            "TG-BOT-SERVER/tests/test_access_store.py::test_rbac_policy_snapshot_verification_rejects_tampering_and_non_owner_write",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_rbac_policy_snapshot_is_owner_only_and_creates_same_card_receipt",
        ],
    },
    "A01-F29": {
        # Latest-two RBAC snapshot comparison, with verified receipts only.
        "support_state": "implemented",
        "handler_id": "a01.f29.admin.rbac_policy_compare",
        "acceptance_level": ["unit", "integration", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_rbac_policy_snapshot_comparison_reports_exact_role_and_grant_changes",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_can_list_only_verified_rbac_snapshot_receipts",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_rbac_policy_comparison_is_owner_only_and_reports_latest_two_on_same_card",
        ],
    },
    "A01-F30": {
        # Reversible, one-use owner-confirmed undo guarded by exact post-state.
        "support_state": "implemented",
        "handler_id": "a01.f30.admin.undo_latest_grant",
        "acceptance_level": ["unit", "integration", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_can_undo_latest_grant_only_when_exact_post_state_still_matches",
            "TG-BOT-SERVER/tests/test_access_store.py::test_owner_grant_undo_fails_closed_on_policy_conflict_or_missing_device",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_admin_grant_undo_requires_preview_then_one_use_confirmation",
        ],
    },
    "B01-F01": {
        # Bot-side implementation is covered by the exact-vs-outcome search
        # regression; Telegram rendering/live delivery is still an open gate.
        "support_state": "needs_device_test",
        "handler_id": "b01.f01.catalog.search.exact",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_exact_and_outcome_search_are_distinct",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_search_is_available_in_bot_and_edits_one_card",
        ],
    },
    "B01-F02": {
        # Fuzzy lookup corrects only longer ID/title tokens; live rendering
        # still needs Telegram/device acceptance.
        "support_state": "needs_device_test",
        "handler_id": "b01.f02.catalog.search.fuzzy",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_fuzzy_search_corrects_typos_only_in_identity_and_ranks_closest_title",
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_fuzzy_search_requires_every_query_token_and_ignores_short_noise",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_search_is_available_in_bot_and_edits_one_card",
        ],
    },
    "B01-F03": {
        # Owner-managed vocabulary is persisted locally; the search UI and
        # Telegram card flow have integration coverage, while live acceptance
        # remains a separate gate.
        "support_state": "needs_device_test",
        "handler_id": "b01.f03.catalog.search.synonyms",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_search_synonyms_persist_and_can_be_removed",
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_exact_search_uses_owner_synonyms_without_mixing_outcome_search",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_catalog_synonym_flow_edits_one_card_and_only_changes_exact_search",
        ],
    },
    "B01-F04": {
        # Area pages are filtered by the selected device's declared OS; an
        # unrecognized OS never falls through to Windows or macOS claims.
        "support_state": "needs_device_test",
        "handler_id": "b01.f04.catalog.filter.platform",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_feature_page_can_filter_to_selected_device_platform_without_changing_grants",
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_feature_page_unknown_platform_does_not_claim_platform_support",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_area_page_filters_to_selected_device_platform",
        ],
    },
    "B01-F05": {
        # Result-type metadata is explicit in the registry; only explicitly
        # classified rows appear, and detail buttons still re-check RBAC.
        "support_state": "needs_device_test",
        "handler_id": "b01.f05.catalog.filter.result_type",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_result_type_filter_uses_only_explicit_registry_metadata",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_result_type_filter_is_registry_backed_and_role_filtered",
        ],
    },
    "B01-F06": {
        # The permission filter distinguishes live OS privacy grants from the
        # separate RBAC and explicit-action confirmation gates.
        "support_state": "needs_device_test",
        "handler_id": "b01.f06.catalog.filter.permission",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_permission_filter_keeps_only_no_permission_or_freshly_granted_os_requirements",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_permission_filter_uses_fresh_device_snapshot_and_preserves_rbac",
        ],
    },
    "B01-F07": {
        # This cross-area view uses the selected device's authorized platform,
        # the caller's role, and exact current effective grants.
        "support_state": "needs_device_test",
        "handler_id": "b01.f07.catalog.filter.access",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_access_filter_uses_role_grants_platform_and_supported_state",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_access_filter_uses_current_grants_and_selected_device",
        ],
    },
    "B01-F08": {
        # Owner feature details explain the selected device's platform and
        # fresh OS-permission blockers without treating unknown as granted.
        "support_state": "needs_device_test",
        "handler_id": "b01.f08.catalog.explain_availability",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_availability_explainer_reports_independent_role_grant_os_and_permission_blockers",
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_availability_explainer_keeps_unobserved_permission_unknown_and_owner_bypasses_grant",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_feature_detail_explains_selected_device_os_and_permission_blockers",
        ],
    },
    "B01-F18": {
        # Outcome search intentionally indexes only the desired user outcome;
        # its live Telegram screen remains part of the acceptance gate.
        "support_state": "needs_device_test",
        "handler_id": "b01.f18.catalog.search.outcome",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_feature_catalog.py::test_exact_and_outcome_search_are_distinct",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_feature_catalog_search_is_available_in_bot_and_edits_one_card",
        ],
    },
    "B02-F01": {
        # Search is scoped to the live role/device-visible catalog; actual
        # Telegram delivery still needs acceptance on the production bot.
        "support_state": "needs_device_test",
        "handler_id": "b02.f01.devices.search",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_search_matches_name_id_and_hostname_only_in_visible_catalog",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_search_entry_respects_roles_and_existing_device_visibility",
        ],
    },
    "B02-F02": {
        # Uses only the OS/version values already reported by visible devices;
        # actual inventory freshness remains part of the device acceptance gate.
        "support_state": "needs_device_test",
        "handler_id": "b02.f02.devices.os_filter",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_os_filter_matches_only_reported_platform_and_version",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_os_filter_updates_existing_card_and_shows_only_matching_devices",
        ],
    },
    "B02-F03": {
        # Heartbeat freshness is a one-shot owner inventory filter, not history.
        "support_state": "needs_device_test",
        "handler_id": "b02.f03.devices.heartbeat_filter",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_heartbeat_age_filter_handles_boundaries_and_unknown_times",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_heartbeat_filter_edits_one_card_with_only_matching_device",
        ],
    },
    "B02-F04": {
        # Searches exact component-specific versions; unknown/stale companion
        # state never falls back to the agent version.
        "support_state": "needs_device_test",
        "handler_id": "b02.f04.devices.component_version_filter",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_component_version_filter_uses_exact_reported_component_version",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_component_version_filter_updates_existing_card",
        ],
    },
    "B02-F05": {
        # Declarations are exact, one-shot, and age-bounded; they do not prove
        # that hardware or a real action is currently working.
        "support_state": "needs_device_test",
        "handler_id": "b02.f05.devices.capability_filter",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_capability_filter_uses_only_fresh_declarations",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_capability_filter_menu_is_owner_only",
        ],
    },
    "B02-F06": {
        # Inventory age comes from the last saved hardware snapshot, not the
        # device's independent heartbeat timestamp.
        "support_state": "needs_device_test",
        "handler_id": "b02.f06.devices.inventory_age_filter",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_inventory_age_filter_uses_snapshot_time_not_heartbeat",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_inventory_age_filter_updates_existing_card",
        ],
    },
    "B02-F07": {
        # Free-form labels are persisted by the bot for the configured owner;
        # live Telegram acceptance remains a separate gate.
        "support_state": "needs_device_test",
        "handler_id": "b02.f07.devices.tags",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_can_store_multiple_normalized_tags_per_device",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_tags_entry_is_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_tags_editor_persists_owner_input_and_replaces_one_card",
        ],
    },
    "B02-F08": {
        # Durable, editable owner collections are separate from expiring snapshots.
        "support_state": "needs_device_test",
        "handler_id": "b02.f08.devices.collections",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_device_collections_are_persistent_named_groups_not_expiring_snapshots",
            "TG-BOT-SERVER/tests/test_device_store.py::test_device_collection_updates_are_owner_only_unique_and_keep_removed_ids_visible",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_collection_entry_is_owner_only_and_registry_backed",
        ],
    },
    "B02-F09": {
        # Saved rules re-run against the current authorized device inventory.
        "support_state": "needs_device_test",
        "handler_id": "b02.f09.devices.saved_filters",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_saved_device_filters_are_durable_normalized_and_private",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_saved_device_filter_entry_is_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_can_create_and_apply_saved_device_filter_in_one_card",
        ],
    },
    "B02-F10": {
        # Sorting is a persisted owner preference; unknown values remain last.
        "support_state": "needs_device_test",
        "handler_id": "b02.f10.devices.sorting",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_device_catalog_sort_preference_is_persistent_and_allowlisted",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_sorting_is_owner_configurable_and_orders_unknowns_last",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_sort_entry_is_owner_only_and_registry_backed",
        ],
    },
    "B02-F11": {
        # Owner's pinned targets are a persistent device list, not action favorites.
        "support_state": "needs_device_test",
        "handler_id": "b02.f11.devices.pinned_list",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_pinned_device_list_is_persistent_ordered_and_separate_from_action_favorites",
            "TG-BOT-SERVER/tests/test_device_store.py::test_pinned_devices_reject_unknown_ids_and_filter_removed_devices",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_pinned_device_entry_and_toggle_are_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_pinned_device_list_opens_only_currently_visible_devices",
        ],
    },
    "B02-F12": {
        # Owner-defined ordering is independent from the persisted sort mode.
        "support_state": "needs_device_test",
        "handler_id": "b02.f12.devices.custom_order",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_device_catalog_custom_order_persists_prunes_stale_and_appends_new_devices",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_custom_order_is_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_custom_order_move_updates_same_card_and_rejects_stale_callback",
        ],
    },
    "B02-F13": {
        # Compares only the owner-visible stored inventory; stale reports remain unknown.
        "support_state": "needs_device_test",
        "handler_id": "b02.f13.devices.compare_cards",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_comparison_formats_only_reported_fields_and_marks_stale_capabilities_unknown",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_comparison_marks_missing_values_unknown_and_bounds_reported_capabilities",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_compare_entry_is_owner_only_and_uses_bounded_snapshot_index",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_compare_start_rejects_stale_inventory_fingerprint",
        ],
    },
    "B02-F14": {
        # Reports completeness of the saved card and the source observation times; never infers missing data.
        "support_state": "needs_device_test",
        "handler_id": "b02.f14.devices.card_completeness",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_completeness_lists_missing_fields_and_truthful_observation_sources",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_completeness_does_not_invent_timestamps_or_treat_future_as_observed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_completeness_entry_is_owner_only_and_edits_the_current_card",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_completeness_rejects_stale_inventory_fingerprint",
        ],
    },
    "B02-F15": {
        # Registration provenance comes from first observed bot-side path; legacy origin stays unknown.
        "support_state": "needs_device_test",
        "handler_id": "b02.f15.devices.registration_origin",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_device_registration_provenance_preserves_origin_and_counts_reregistrations",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_registration_origin_is_owner_only_and_edits_the_current_card",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_registration_origin_does_not_infer_legacy_source",
        ],
    },
    "B02-F16": {
        # Owner-only local inventory wizard; no message is sent to an agent.
        "support_state": "needs_device_test",
        "handler_id": "b02.f16.devices.inventory_wizard",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_inventory_wizard_is_owner_only_and_registered",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_inventory_wizard_guides_tags_and_typed_fields_without_remote_actions",
        ],
    },
    "B02-F17": {
        # Owner-only private device notes stay inside the bot's local catalog.
        "support_state": "needs_device_test",
        "handler_id": "b02.f17.devices.private_note",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_private_device_note_is_local_bounded_and_not_exposed_to_other_actors",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_private_device_note_is_owner_only_and_audit_excludes_note_text",
        ],
    },
    "B02-F18": {
        # Typed owner-managed inventory fields have bot/store coverage; live
        # Telegram acceptance remains a separate gate.
        "support_state": "needs_device_test",
        "handler_id": "b02.f18.devices.typed_inventory_fields",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_typed_inventory_fields_validate_enum_date_and_boolean",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_typed_inventory_owner_can_define_new_enum_schema_in_telegram",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_typed_inventory_editor_saves_enum_boolean_and_date_values",
        ],
    },
    "B02-F19": {
        # Owner-entered place text is not a coordinate or a system location fix.
        "support_state": "needs_device_test",
        "handler_id": "b02.f19.devices.manual_place_label",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_manual_device_place_label_is_text_not_gps_and_owner_scoped",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_manual_device_place_label_is_owner_only_and_explicitly_not_gps",
        ],
    },
    "B02-F20": {
        # One allowlisted purpose is persisted separately from free-form tags.
        "support_state": "needs_device_test",
        "handler_id": "b02.f20.devices.purpose_classification",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_device_purpose_is_a_single_allowlisted_owner_classification",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_purpose_entry_is_owner_only_and_distinct_from_tags",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_purpose_callback_sets_only_allowlisted_classification",
        ],
    },
    "B02-F21": {
        # Templates contain only owner-managed metadata, not agent reports.
        "support_state": "needs_device_test",
        "handler_id": "b02.f21.devices.inventory_templates",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_inventory_template_copies_only_safe_metadata_to_blank_device",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_inventory_template_apply_is_owner_only_and_uses_preview_confirmation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_inventory_template_actions_are_only_shown_to_owner",
        ],
    },
    "B02-F22": {
        # A hostname/platform match is only a comparison suggestion, never an identity claim or merge.
        "support_state": "needs_device_test",
        "handler_id": "b02.f22.devices.duplicate_candidates",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_likely_duplicate_detection_requires_same_normalized_hostname_and_platform",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_duplicate_candidate_is_owner_only_and_opens_read_only_comparison",
        ],
    },
    "B02-F23": {
        # Aggregate heartbeat-age history is covered locally; live Telegram
        # acceptance remains unverified.
        "support_state": "needs_device_test",
        "handler_id": "b02.f23.devices.aggregate_inactivity_history",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_inactivity_history_is_aggregate_and_returns_platform_deltas",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_owner_can_capture_and_compare_aggregate_inactivity_history",
        ],
    },
    "B02-F24": {
        # A manually selected review date is bot-side metadata; no background
        # tracking or agent command is involved.
        "support_state": "needs_device_test",
        "handler_id": "b02.f24.devices.manual_review_date",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_owner_device_review_date_is_strict_persistent_and_clearable",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_review_date_entry_is_owner_only_and_shows_saved_date",
        ],
    },
    "B02-F25": {
        # Maintenance is owner-authored catalog metadata; it never controls
        # agent availability and only excludes the device from inactivity aggregates.
        "support_state": "needs_device_test",
        "handler_id": "b02.f25.devices.maintenance_mode",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_device_maintenance_is_owner_only_expires_and_excludes_only_selected_aggregate",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_maintenance_entry_is_owner_only_and_shows_expiry",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_maintenance_input_sets_owner_metadata_without_remote_command",
        ],
    },
    "B02-F26": {
        # Exact selected IDs with an expiry are separate from reusable dynamic filters.
        "support_state": "needs_device_test",
        "handler_id": "b02.f26.devices.selected_snapshot_ttl",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_selected_device_snapshot_expires_and_reports_stale_ids",
            "TG-BOT-SERVER/tests/test_device_store.py::test_selected_snapshot_list_is_owner_only_and_omits_expired_rows",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_selected_device_snapshot_ui_entry_is_owner_only",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_snapshot_selection_aborts_if_device_index_order_changes",
        ],
    },
    "B02-F27": {
        # Archiving changes only normal catalog visibility; restoring returns the same record.
        "support_state": "needs_device_test",
        "handler_id": "b02.f27.devices.archive_restore",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_device_archive_is_owner_only_reversible_and_does_not_remove_device",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_archive_controls_are_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_archive_and_restore_actions_are_local_and_same_card",
        ],
    },
    "B02-F28": {
        # Explicit owner selection and a fixed safe-field projection; sensitive device/catalog fields are excluded.
        "support_state": "needs_device_test",
        "handler_id": "b02.f28.devices.explicit_field_export",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_export_includes_only_explicit_allowlisted_fields",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_export_csv_uses_only_selected_fields_and_stable_header",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_export_is_owner_only_field_selected_and_replaces_the_current_card",
        ],
    },
    "B02-F29": {
        # Uploaded exports are parsed against a strict allowlist and compared only;
        # they can never create devices or overwrite live agent reports.
        "support_state": "needs_device_test",
        "handler_id": "b02.f29.devices.read_only_import_reconcile",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_catalog_import.py::test_json_import_reports_new_missing_and_field_conflicts_without_mutating_current",
            "TG-BOT-SERVER/tests/test_device_catalog_import.py::test_csv_import_accepts_exported_catalog_and_normalizes_types",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_catalog_import_preview_is_owner_only_and_replaces_current_card",
        ],
    },
    "B02-F30": {
        "support_state": "needs_device_test",
        "handler_id": "b02.f30.devices.manual_relations",
        "acceptance_level": ["unit", "integration", "device", "live"],
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_manual_device_relations_are_owner_only_symmetric_and_pruned_when_device_is_removed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_relations_entry_is_owner_only_and_shows_link_count",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_relation_input_links_only_a_visible_peer_without_remote_command",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_device_relation_input_rejects_self_and_unknown_ids",
        ],
    },
    "D01-F02": {
        # Bounded on-demand sampling is locally tested; physical Windows/macOS
        # acceptance remains separate evidence.
        "support_state": "needs_device_test",
        "handler_id": "d01.f02.cpu.history",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_cpu_history_returns_bounded_correlated_samples_for_selected_period",
            "XGENT-WDS/tests/test_capabilities.py::test_cpu_history_rejects_unbounded_period_without_sampling",
            "XGENT-MCS/tests/test_capabilities.py::test_cpu_history_returns_bounded_correlated_samples_for_selected_period",
            "XGENT-MCS/tests/test_capabilities.py::test_cpu_history_rejects_unbounded_period_without_sampling",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_cpu_history_renders_only_a_correlated_selected_period",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_cpu_history_hides_samples_for_stale_or_malformed_results",
        ],
    },
    "D01-F03": {
        # Bounded RAM/swap sampling is locally tested; physical Windows/macOS
        # acceptance remains separate evidence.
        "support_state": "needs_device_test",
        "handler_id": "d01.f03.memory.history",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_memory_history_returns_bounded_correlated_ram_and_swap_samples",
            "XGENT-WDS/tests/test_capabilities.py::test_memory_history_marks_absent_swap_unsupported_not_zero",
            "XGENT-MCS/tests/test_capabilities.py::test_memory_history_returns_bounded_correlated_ram_and_swap_samples",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_history_renders_only_a_correlated_bounded_ram_and_swap_series",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_history_does_not_invent_swap_zero_or_render_stale_samples",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_history_keeps_ram_when_swap_series_is_partial_and_marks_swap_unknown",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_history_period_picker_is_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_history_does_not_wait_for_agent_that_advertises_no_command",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_history_updates_the_existing_card_with_its_correlated_result",
        ],
    },
    "D01-F04": {
        # A trend is an owner-triggered measurement. Local history is bounded
        # and in-process; only a same-volume observation at least six hours
        # old can produce a linear estimate. Device acceptance remains open.
        "support_state": "needs_device_test",
        "handler_id": "d01.f04.volume.history",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_volume_history_keeps_real_samples_and_withholds_short_term_forecast",
            "XGENT-WDS/tests/test_capabilities.py::test_volume_history_estimates_only_from_same_volume_capacity_after_six_hours",
            "XGENT-MCS/tests/test_capabilities.py::test_volume_history_keeps_real_samples_and_withholds_short_term_forecast",
            "XGENT-MCS/tests/test_capabilities.py::test_volume_history_estimates_only_from_same_volume_capacity_after_six_hours",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_volume_history_renders_correlated_live_usage_and_measured_forecast",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_volume_history_hides_stale_or_unreliable_forecast_values",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_volume_history_is_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_volume_history_updates_the_existing_card_with_correlated_result",
        ],
    },
    "D01-F05": {
        # Platform APIs are sampled locally on request. Windows reports native
        # low/high thresholds; macOS reports the memory_pressure utility's
        # free-percent metric, explicitly not Activity Monitor's graph.
        "support_state": "needs_device_test",
        "handler_id": "d01.f05.memory.pressure.history",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_memory_pressure_history_samples_native_source_and_marks_its_contract",
            "XGENT-WDS/tests/test_capabilities.py::test_memory_pressure_invalid_period_does_not_query_native_source",
            "XGENT-MCS/tests/test_capabilities.py::test_memory_pressure_utility_sample_is_a_free_percent_metric_not_activity_monitor_level",
            "XGENT-MCS/tests/test_capabilities.py::test_memory_pressure_utility_malformed_output_is_unknown_not_zero",
            "XGENT-MCS/tests/test_capabilities.py::test_memory_pressure_history_samples_native_source_and_marks_its_contract",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_pressure_renderer_preserves_platform_source_and_metric_meaning",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_memory_pressure_renderer_hides_stale_and_malformed_samples",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_pressure_history_is_owner_only_and_registry_backed",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_pressure_history_does_not_dispatch_to_agent_without_the_command",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_memory_pressure_history_edits_the_existing_card",
        ],
    },
    "D01-F06": {
        # Windows reads only native ACPI WMI thermal-zone values when exposed;
        # macOS explicitly reports unsupported until a supported public sensor
        # adapter is available. Neither path fabricates missing sensor values.
        "support_state": "needs_device_test",
        "handler_id": "d01.f06.thermal.snapshot",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_thermal_snapshot_reads_only_bounded_wmi_zones_and_converts_kelvin_tenths",
            "XGENT-WDS/tests/test_capabilities.py::test_thermal_snapshot_missing_wmi_provider_is_unsupported_not_zero",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_thermal_snapshot_is_explicitly_unsupported_without_a_public_sensor_source",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_thermal_snapshot_renderer_distinguishes_sensor_data_and_unsupported_platform",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_thermal_snapshot_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F07": {
        # The agents expose design/full-charge capacity only when their native provider returns a matched pair.
        # Values are a ratio of remaining capacity, not live charge; device acceptance remains open.
        "support_state": "needs_device_test",
        "handler_id": "d01.f07.battery.health",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_battery_health_joins_design_and_full_capacity_by_instance",
            "XGENT-WDS/tests/test_capabilities.py::test_battery_health_rejects_unmatched_or_relative_capacity_data",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_battery_health_reads_design_and_full_capacity_from_system_profiler_json",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_battery_health_missing_capacity_is_unknown_not_zero",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_battery_health_renderer_separates_wear_from_charge_and_rejects_bad_data",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_battery_health_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F08": {
        # This is a separate, optional provider counter; zero on Windows means
        # the WMI provider does not support the field, not zero battery wear.
        "support_state": "needs_device_test",
        "handler_id": "d01.f08.battery.cycles",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_battery_cycle_snapshot_uses_only_positive_wmi_cycle_counters",
            "XGENT-WDS/tests/test_capabilities.py::test_battery_cycle_snapshot_treats_wmi_zero_as_unsupported",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_battery_cycle_snapshot_reads_reported_system_profiler_count",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_battery_cycle_snapshot_missing_field_is_unknown",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_battery_cycle_renderer_reports_only_correlated_provider_counts",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_battery_cycles_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F09": {
        # Windows exports SRUM locally and sends only a bounded estimate summary;
        # macOS lacks a supported public API for arbitrary apps. Device acceptance remains open.
        "support_state": "needs_device_test",
        "handler_id": "d01.f09.energy.usage",
        "test_refs": [
            "XGENT-WDS/tests/test_energy_usage.py::test_srum_energy_csv_aggregates_only_local_app_energy_and_drops_paths",
            "XGENT-WDS/tests/test_energy_usage.py::test_srum_energy_csv_uses_total_measure_once_and_converts_mwh",
            "XGENT-WDS/tests/test_energy_usage.py::test_srum_energy_csv_fails_closed_when_multiple_total_columns_are_ambiguous",
            "XGENT-WDS/tests/test_energy_usage.py::test_srum_energy_csv_fails_closed_for_unknown_units_or_schema",
            "XGENT-WDS/tests/test_capabilities.py::test_energy_usage_snapshot_exports_srum_locally_and_returns_only_bounded_aggregate",
            "XGENT-WDS/tests/test_capabilities.py::test_energy_usage_snapshot_is_explicitly_unknown_on_export_failure",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_energy_usage_is_explicitly_unsupported_without_a_public_per_app_provider",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_energy_usage_renderer_shows_only_correlated_estimates_and_redacts_paths",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_energy_usage_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F10": {
        # Only event kind and timestamp leave the device; physical-device acceptance stays separate.
        "support_state": "needs_device_test",
        "handler_id": "d01.f10.sleep.wake.history",
        "test_refs": [
            "XGENT-WDS/tests/test_sleep_wake_history.py::test_windows_sleep_wake_parser_keeps_only_allowlisted_recent_events",
            "XGENT-WDS/tests/test_wds.py::test_windows_sleep_wake_snapshot_is_bounded_redacted_and_registered",
            "XGENT-MCS/tests/test_sleep_wake_history.py::test_pmset_parser_returns_recent_normalized_sleep_and_wake_without_raw_log_text",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_sleep_wake_snapshot_uses_pmset_and_publishes_redacted_correlated_events",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sleep_wake_renderer_accepts_only_correlated_recent_allowlisted_events",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_sleep_wake_history_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F11": {
        # Windows reports a measured BootTime field; macOS is explicitly unsupported without a reliable public source.
        "support_state": "needs_device_test",
        "handler_id": "d01.f11.startup.duration",
        "test_refs": [
            "XGENT-WDS/tests/test_startup_duration.py::test_windows_startup_duration_keeps_only_valid_boot_duration_and_timestamp",
            "XGENT-WDS/tests/test_startup_duration.py::test_windows_startup_duration_fails_closed_for_missing_invalid_or_future_values",
            "XGENT-WDS/tests/test_wds.py::test_windows_startup_duration_uses_diagnostics_event_100_only",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_startup_duration_is_explicitly_unsupported_without_reliable_public_source",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_startup_duration_renderer_uses_measured_boot_duration_not_uptime",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_startup_duration_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F12": {
        # Windows reads a fixed, read-only marker set; macOS is explicit unsupported.
        "support_state": "needs_device_test",
        "handler_id": "d01.f12.pending.restart",
        "test_refs": [
            "XGENT-WDS/tests/test_pending_restart.py::test_pending_restart_parser_returns_only_fixed_reason_ids",
            "XGENT-WDS/tests/test_pending_restart.py::test_pending_restart_parser_distinguishes_clear_from_unknown_and_partial",
            "XGENT-WDS/tests/test_wds.py::test_windows_pending_restart_reads_only_fixed_markers_and_publishes_correlated_state",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_pending_restart_is_explicitly_unsupported_without_reliable_public_source",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_pending_restart_renderer_requires_fresh_correlation_and_fixed_reason_ids",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_pending_restart_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F13": {
        # The reference is dated and device acceptance remains a distinct gate.
        "support_state": "needs_device_test",
        "handler_id": "d01.f13.os.support",
        "test_refs": [
            "XGENT-WDS/tests/test_wds.py::test_windows_os_support_identity_reads_release_and_edition_without_raw_registry",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_os_support_identity_reports_only_system_version",
            "TG-BOT-SERVER/tests/test_os_support.py::test_windows_lifecycle_is_exact_release_edition_and_reference_dated",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_os_support_renderer_is_dated_source_labelled_and_correlation_bound",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_os_support_action_is_owner_only_registry_backed_and_edits_existing_card",
        ],
    },
    "D01-F14": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f14.hardware.inventory.history",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_hardware_inventory_snapshots_compare_only_allowlisted_nonserial_fields",
            "XGENT-WDS/tests/test_wds.py::test_windows_hardware_inventory_projects_model_fields_without_serials",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_hardware_inventory_uses_sysctl_allowlist_without_serials",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_hardware_inventory_history_is_correlated_owner_only_and_redacted",
        ],
    },
    "D01-F15": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f15.driver.inventory.history",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_driver_snapshots_are_bounded_redacted_and_diffed_by_provider_and_name",
            "XGENT-WDS/tests/test_wds.py::test_windows_driver_inventory_projects_only_names_providers_and_versions",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_driver_inventory_is_explicitly_unsupported_without_stable_public_list",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_driver_inventory_history_is_correlated_owner_only_and_redacted",
        ],
    },
    "D01-F16": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f16.installed.app.history",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_installed_app_snapshots_are_allowlisted_bounded_and_diffed",
            "XGENT-WDS/tests/test_wds.py::test_windows_installed_app_inventory_projects_only_name_publisher_and_version",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_installed_app_inventory_reads_bounded_bundle_metadata_only",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_installed_app_history_is_correlated_owner_only_and_redacted",
        ],
    },
    "D01-F17": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f17.startup.history",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_startup_snapshots_keep_only_names_and_fixed_scopes",
            "XGENT-WDS/tests/test_wds.py::test_windows_startup_inventory_exports_entry_labels_and_scopes_not_commands",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_startup_inventory_is_explicitly_unsupported_without_querying_login_items",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_startup_inventory_history_is_correlated_owner_only_and_redacted",
        ],
    },
    "D01-F18": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f18.service.history",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_service_snapshots_keep_only_public_labels_and_diff_fixed_state_fields",
            "XGENT-WDS/tests/test_wds.py::test_windows_service_inventory_projects_names_states_and_start_types_only",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_service_inventory_is_explicitly_unsupported_without_shell_probing",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_service_inventory_history_is_correlated_owner_only_and_redacted",
        ],
    },
    "D01-F19": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f19.crash.summary",
        "test_refs": [
            "XGENT-WDS/tests/test_wds.py::test_windows_crash_summary_aggregates_only_provider_event_level_and_count",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_crash_summary_is_unsupported_without_exporting_private_unified_log",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_crash_summary_is_correlated_owner_only_and_redacted",
        ],
    },
    "D01-F20": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f20.smart.counter.trend",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_smart_counter_snapshots_are_bounded_allowlisted_and_compared_by_disk_label",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_smart_trend_is_owner_only_correlated_and_persists_two_device_snapshots",
            "XGENT-WDS/tests/test_wds.py::test_windows_smart_trend_snapshot_projects_only_supported_numeric_counters",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_smart_counter_trend_is_explicitly_unsupported",
        ],
    },
    "D01-F21": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f21.volume.topology",
        "test_refs": [
            "XGENT-WDS/tests/test_wds.py::test_windows_volume_topology_hashes_volume_ids_and_keeps_mount_aliases",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_volume_topology_groups_mount_aliases_without_device_paths",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_volume_topology_is_owner_only_correlated_and_updates_one_card",
        ],
    },
    "D01-F22": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f22.clock.drift.local",
        "test_refs": [
            "XGENT-WDS/tests/test_wds.py::test_windows_clock_drift_compares_wall_and_monotonic_elapsed_without_setting_time",
            "XGENT-MCS/tests/test_capabilities.py::test_macos_clock_drift_compares_wall_and_monotonic_elapsed_without_setting_time",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_clock_drift_is_owner_only_correlated_and_explains_local_estimate",
        ],
    },
    "D01-F24": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f24.system.summary.history",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_system_summary_history_is_allowlisted_bounded_and_expires",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_snapshot_history_is_owner_only_correlated_and_edits_one_card",
        ],
    },
    "D01-F25": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f25.system.summary.compare",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_system_summary_comparison_uses_two_newest_snapshots_and_only_allowlisted_fields",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_snapshot_comparison_is_owner_only_and_edits_one_card",
        ],
    },
    "D01-F26": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f26.system.summary.interval",
        "test_refs": [
            "XGENT-WDS/tests/test_sysinfo_schedule.py::test_agent_applies_owner_command_and_reports_correlated_schedule",
            "XGENT-WDS/tests/test_sysinfo_schedule.py::test_scheduler_publishes_only_sanitized_metrics_and_coalesces_missed_ticks",
            "XGENT-MCS/tests/test_sysinfo_schedule.py::test_agent_applies_owner_command_and_reports_correlated_schedule",
            "XGENT-MCS/tests/test_sysinfo_schedule.py::test_scheduler_publishes_only_sanitized_metrics_and_coalesces_missed_ticks",
            "TG-BOT-SERVER/tests/test_device_store.py::test_system_summary_schedule_requires_allowlisted_interval_and_schedule_identity",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_summary_interval_is_owner_only_and_applies_correlated_agent_ack",
        ],
    },
    "D01-F27": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f27.system.summary.thresholds",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_system_threshold_alert_requires_two_consecutive_samples_and_rearms_below_hysteresis",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_summary_threshold_settings_are_owner_only_and_replace_one_card",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_scheduled_threshold_notice_requires_two_samples_and_valid_sensor_provenance",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_scheduled_thresholds_ignore_samples_older_than_active_schedule_window",
            "XGENT-WDS/tests/test_sysinfo_schedule.py::test_scheduler_publishes_only_sanitized_metrics_and_coalesces_missed_ticks",
            "XGENT-MCS/tests/test_sysinfo_schedule.py::test_scheduler_publishes_only_sanitized_metrics_and_coalesces_missed_ticks",
        ],
    },
    "D01-F28": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f28.system.summary.aggregate",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_scheduled_system_summary_aggregates_only_fresh_scheduled_samples_by_period",
            "TG-BOT-SERVER/tests/test_device_store.py::test_scheduled_system_summary_rejects_invalid_period_and_unknown_when_empty",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_scheduled_system_summary_aggregate_is_owner_only_and_updates_one_card",
        ],
    },
    "D01-F29": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f29.system.summary.export",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_device_store.py::test_system_summary_snapshot_lookup_is_device_scoped_and_allowlisted",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_system_snapshot_export_selects_allowlisted_snapshot_and_keeps_one_card",
        ],
    },
    "D01-F30": {
        "support_state": "needs_device_test",
        "handler_id": "d01.f30.system.summary.uncertainty",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_uncertainty_distinguishes_measured_estimated_missing_and_stale",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_uncertainty_rejects_uncorrelated_or_malformed_provenance",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_sysinfo_uncertainty_is_owner_only_correlated_and_updates_one_card",
        ],
    },
    "D02-F13": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f13.camera.quality.local",
        "test_refs": [
            "XGENT-WDS/tests/test_camera_quality.py::test_camera_quality_captures_one_local_frame_and_never_returns_image",
            "XGENT-WDS/tests/test_camera_quality.py::test_camera_quality_reports_capture_failure_without_sending_an_image",
            "XGENT-MCS/tests/test_camera_quality.py::test_camera_quality_captures_one_local_frame_and_never_returns_image",
            "XGENT-MCS/tests/test_camera_quality.py::test_camera_quality_reports_capture_failure_without_sending_an_image",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_camera_quality_formatter_shows_only_correlated_metrics_and_explains_heuristic",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_quality_is_owner_only_uses_one_correlated_request_and_edits_one_card",
        ],
    },
    "D02-F04": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f04.screenshot.mask.preview",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_screenshot.py::test_masked_screenshot_covers_normalized_rectangle_without_mutating_source",
            "TG-BOT-SERVER/tests/test_screenshot.py::test_mask_cache_is_owner_token_phase_bound_and_expires",
            "TG-BOT-SERVER/tests/test_screenshot.py::test_owner_mask_flow_keeps_raw_image_out_of_telegram_and_previews_only_masked_copy",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_mask_navigation_is_owner_only_and_registry_backed",
        ],
    },
    "D02-F05": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f05.screenshot.preview",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_preview_stages_original_until_one_use_owner_confirmation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_preview_cancel_discards_staged_original",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_preview_callbacks_are_owner_only",
        ],
    },
    "D02-F06": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f06.display.contact_sheet",
        "test_refs": [
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_contact_sheet_contains_only_selected_images_and_is_bounded",
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_agent_returns_only_selected_contact_sheet",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_contact_sheet_contains_only_selected_images_and_is_bounded",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_agent_returns_only_selected_contact_sheet",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_contact_sheet_navigation_is_owner_only_and_supports_explicit_selection",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_catalog_formatter_requires_fresh_correlated_explicit_catalog",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_contact_sheet_result_is_bound_to_exact_selection_and_platform",
        ],
    },
    "D02-F07": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f07.screenshot.annotation_copy",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_screenshot_annotation.py::test_annotation_returns_a_distinct_same_size_copy_without_mutating_source",
            "TG-BOT-SERVER/tests/test_screenshot_annotation.py::test_annotation_rejects_unknown_unbounded_or_ambiguous_input",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_preview_keyboard_offers_non_destructive_annotation_and_confirmed_copy",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_annotation_updates_the_same_card_and_preserves_original",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_preview_callbacks_are_owner_only",
        ],
    },
    "D02-F08": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f08.screenshot.visual_compare",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_screenshot_comparison.py::test_comparison_is_same_size_side_by_side_and_highlights_changed_pixels",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_comparison_captures_second_image_and_sends_only_confirmed_diff",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_comparison_mqtt_failure_answers_callback_once_and_keeps_preview",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_screenshot_preview_callbacks_are_owner_only",
        ],
    },
    "D02-F01": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f01.display.screenshot",
        "test_refs": [
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_single_display_capture_is_full_resolution_and_rejects_a_stale_catalog",
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_full_resolution_single_display",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_single_display_capture_is_full_resolution_and_rejects_a_stale_catalog",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_full_resolution_single_display",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_contact_sheet_navigation_is_owner_only_and_supports_explicit_selection",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_single_display_result_is_fresh_correlated_and_bound_to_one_display",
        ],
    },
    "D02-F02": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f02.window.screenshot",
        "component_min_versions": {
            "bot": {"state": "planned", "minimum": None, "reason": "Implemented in the working tree; minimum version awaits signed packaging and device acceptance"},
            "windows_agent": {"state": "planned", "minimum": None, "reason": "Implemented in the working tree; minimum version awaits signed packaging and device acceptance"},
            "macos_agent": {"state": "planned", "minimum": None, "reason": "Implemented in the working tree; minimum version awaits signed packaging and device acceptance"},
            "server": {"state": "not_applicable", "minimum": None, "reason": "D02 does not implement behavior in server"},
            "site": {"state": "not_applicable", "minimum": None, "reason": "D02 does not implement behavior in site"},
        },
        "test_refs": [
            "XGENT-WDS/tests/test_window_capture.py::test_catalog_exposes_only_bounded_owner_selectable_metadata",
            "XGENT-WDS/tests/test_window_capture.py::test_window_catalog_token_detects_stale_selection_and_maps_only_opaque_id",
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_owner_window_catalog_and_selected_image",
            "XGENT-MCS/tests/test_window_capture.py::test_catalog_exposes_only_bounded_owner_selectable_metadata",
            "XGENT-MCS/tests/test_window_capture.py::test_window_catalog_token_detects_stale_selection_and_maps_only_opaque_id",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_owner_window_catalog_and_selected_image",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_window_capture_is_owner_only_correlated_and_replaces_the_current_card",
        ],
    },
    "D02-F03": {
        # Agent and owner-only Telegram flows are implemented in source; keep
        # device acceptance separate from repository tests and signed rollout.
        "support_state": "needs_device_test",
        "handler_id": "d02.f03.display.region.screenshot",
        "test_refs": [
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_capture_display_region_crops_only_requested_rectangle_and_rejects_out_of_bounds",
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_selected_region_image",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_capture_display_region_crops_only_requested_rectangle_and_rejects_out_of_bounds",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_selected_region_image",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_contact_sheet_navigation_is_owner_only_and_supports_explicit_selection",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_region_parser_requires_four_bounded_integer_coordinates",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_region_result_requires_exact_request_and_cropped_dimensions",
        ],
    },
    "D02-F10": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f10.display.resolution",
        "test_refs": [
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_logical_backing_resolution_report_is_sanitized_and_compares_scale",
            "XGENT-WDS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_logical_backing_resolution_report",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_logical_backing_resolution_report_is_sanitized_and_compares_scale",
            "XGENT-MCS/tests/test_display_contact_sheet.py::test_agent_returns_correlated_logical_backing_resolution_report",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_display_resolution_is_owner_only_correlated_and_queries_only_the_selected_monitor",
        ],
    },
    "D02-F11": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f11.camera.selection",
        "test_refs": [
            "XGENT-WDS/tests/test_camera_catalog.py::test_camera_catalog_exposes_bounded_labels_and_opaque_ids",
            "XGENT-WDS/tests/test_camera_catalog.py::test_camera_catalog_action_publishes_a_correlated_privacy_limited_result",
            "XGENT-MCS/tests/test_camera_catalog.py::test_camera_catalog_exposes_bounded_labels_and_opaque_ids",
            "XGENT-MCS/tests/test_camera_catalog.py::test_camera_catalog_action_publishes_a_correlated_privacy_limited_result",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_catalog_is_owner_only_and_requires_separate_capture_confirmation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_catalog_rejects_stale_or_untrusted_camera_entries",
        ],
    },
    "D02-F12": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f12.camera.preview.decision",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_catalog_is_owner_only_and_requires_separate_capture_confirmation",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_preview_keep_is_owner_only_bound_to_one_message_and_one_use",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_preview_discard_removes_only_the_selected_preview_card",
        ],
    },
    "D02-F14": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f14.camera.bounded_series",
        "test_refs": [
            "XGENT-WDS/tests/test_camera_catalog.py::test_webcam_series_returns_bounded_in_memory_contact_sheet",
            "XGENT-WDS/tests/test_camera_catalog.py::test_webcam_series_rejects_out_of_range_frame_count_before_camera_open",
            "XGENT-MCS/tests/test_camera_catalog.py::test_webcam_series_returns_bounded_in_memory_contact_sheet",
            "XGENT-MCS/tests/test_camera_catalog.py::test_webcam_series_rejects_out_of_range_frame_count_before_camera_open",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_series_is_owner_only_correlated_bounded_and_uses_one_preview_card",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_camera_catalog_is_owner_only_and_requires_separate_capture_confirmation",
        ],
    },
    "D02-F15": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f15.microphone.input.selection",
        "test_refs": [
            "XGENT-WDS/tests/test_microphone_devices.py::test_agent_publishes_correlated_microphone_catalog_without_backend_fields",
            "XGENT-WDS/tests/test_microphone_devices.py::test_selected_microphone_is_resolved_again_before_recording",
            "XGENT-MCS/tests/test_microphone_devices.py::test_agent_publishes_correlated_microphone_catalog_without_backend_fields",
            "XGENT-MCS/tests/test_microphone_devices.py::test_selected_microphone_is_resolved_again_before_recording",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_mic_duration_requires_fresh_catalog_then_owner_selects_input",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_mic_picker_fails_closed_for_old_agent_without_catalog_command",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_legacy_mic_action_opens_duration_picker_instead_of_recording",
        ],
    },
    "D02-F16": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f16.microphone.level.meter",
        "test_refs": [
            "XGENT-WDS/tests/test_microphone_devices.py::test_microphone_level_meter_returns_only_aggregates_and_never_audio",
            "XGENT-WDS/tests/test_microphone_devices.py::test_stale_microphone_level_request_never_opens_input_stream",
            "XGENT-MCS/tests/test_microphone_devices.py::test_microphone_level_meter_returns_only_aggregates_and_never_audio",
            "XGENT-MCS/tests/test_microphone_devices.py::test_stale_microphone_level_request_never_opens_input_stream",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_microphone_level_is_owner_selected_fresh_correlated_and_never_records_audio",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_mic_and_volume_option_menus_are_rendered_from_registry",
        ],
    },
    "D02-F17": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f17.microphone.output.roundtrip",
        "test_refs": [
            "XGENT-WDS/tests/test_microphone_devices.py::test_microphone_roundtrip_uses_selected_live_devices_and_deletes_temp_audio",
            "XGENT-WDS/tests/test_microphone_devices.py::test_microphone_roundtrip_rejects_stale_output_before_recording",
            "XGENT-WDS/tests/test_microphone_devices.py::test_microphone_roundtrip_deletes_temp_audio_when_playback_fails",
            "XGENT-WDS/tests/test_microphone_devices.py::test_agent_microphone_test_publishes_only_result_metadata",
            "XGENT-MCS/tests/test_microphone_devices.py::test_microphone_roundtrip_rejects_stale_output_before_recording",
            "XGENT-MCS/tests/test_microphone_devices.py::test_microphone_roundtrip_deletes_temp_audio_when_playback_fails",
            "XGENT-MCS/tests/test_microphone_devices.py::test_agent_microphone_test_publishes_only_result_metadata",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_microphone_roundtrip_is_owner_selected_fresh_and_edits_one_card",
        ],
    },
    "D02-F19": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f19.audio.output.catalog",
        "test_refs": [
            "XGENT-WDS/tests/test_microphone_devices.py::test_projects_only_bounded_output_device_metadata",
            "XGENT-WDS/tests/test_microphone_devices.py::test_agent_publishes_correlated_output_catalog_without_backend_fields",
            "XGENT-MCS/tests/test_microphone_devices.py::test_projects_only_bounded_output_device_metadata",
            "XGENT-MCS/tests/test_microphone_devices.py::test_agent_publishes_correlated_output_catalog_without_backend_fields",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audio_output_catalog_is_owner_only_fresh_correlated_and_edits_one_card",
        ],
    },
    "D02-F20": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f20.microphone.preview_before_delivery",
        "test_refs": [
            "XGENT-WDS/tests/test_microphone_devices.py::test_preview_recording_plays_locally_before_returning_audio_and_cleans_temp",
            "XGENT-WDS/tests/test_microphone_devices.py::test_agent_microphone_preview_returns_audio_only_after_local_preview",
            "XGENT-MCS/tests/test_microphone_devices.py::test_preview_recording_plays_locally_before_returning_audio_and_cleans_temp",
            "XGENT-MCS/tests/test_microphone_devices.py::test_agent_microphone_preview_returns_audio_only_after_local_preview",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_microphone_preview_is_owner_selected_correlated_and_requires_confirmation",
        ],
    },
    "D02-F21": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f21.microphone.stereo.balance",
        "test_refs": [
            "XGENT-WDS/tests/test_microphone_devices.py::test_stereo_channel_balance_returns_only_metrics_and_zeroes_sample_buffer",
            "XGENT-WDS/tests/test_microphone_devices.py::test_agent_stereo_balance_publishes_only_correlated_scalar_metrics",
            "XGENT-MCS/tests/test_microphone_devices.py::test_stereo_channel_balance_returns_only_metrics_and_zeroes_sample_buffer",
            "XGENT-MCS/tests/test_microphone_devices.py::test_agent_stereo_balance_publishes_only_correlated_scalar_metrics",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_microphone_balance_is_owner_selected_correlated_and_edits_one_card",
        ],
    },
    "D02-F24": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f24.media.history.metadata_only",
        "test_refs": [
            "XGENT-WDS/tests/test_media_history.py::test_media_history_records_only_allowlisted_metadata_not_payload",
            "XGENT-WDS/tests/test_media_history.py::test_agent_media_history_command_returns_only_correlated_metadata",
            "XGENT-MCS/tests/test_media_history.py::test_media_history_records_only_allowlisted_metadata_not_payload",
            "XGENT-MCS/tests/test_media_history.py::test_agent_media_history_command_returns_only_correlated_metadata",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_media_history_is_owner_only_correlated_metadata_and_edits_one_card",
        ],
    },
    "D02-F18": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f18.audio.output.switch",
        "test_refs": [
            "XGENT-WDS/tests/test_audio_output_control.py::test_switches_only_the_fresh_owner_selected_system_output",
            "XGENT-WDS/tests/test_audio_output_control.py::test_rolls_back_when_system_does_not_confirm_new_default",
            "XGENT-WDS/tests/test_audio_output_control.py::test_agent_reports_only_verified_switch_metadata",
            "XGENT-MCS/tests/test_microphone_devices.py::test_mac_agent_reports_verified_system_output_switch_metadata",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_audio_output_switch_requires_fresh_owner_selection_and_edits_one_card",
        ],
    },
    "D02-F23": {
        "support_state": "needs_device_test",
        "handler_id": "d02.f23.media_temp.cleanup",
        "test_refs": [
            "XGENT-WDS/tests/test_media_temp.py::test_cleanup_only_removes_expired_owned_media_files",
            "XGENT-WDS/tests/test_media_temp.py::test_cleanup_command_is_advertised_and_returns_only_bounded_report",
            "XGENT-MCS/tests/test_media_temp.py::test_cleanup_only_removes_expired_owned_media_files",
            "XGENT-MCS/tests/test_media_temp.py::test_cleanup_command_is_advertised_and_returns_only_bounded_report",
            "TG-BOT-SERVER/tests/test_bot_ui.py::test_media_temp_cleanup_is_owner_only_and_rejects_uncorrelated_reports",
        ],
    },
    "D01-F01": {
        # The explanation is rendered from the correlated sysinfo reply;
        # physical-device acceptance and the remaining expandable UI gate stay open.
        "support_state": "needs_device_test",
        "handler_id": "d01.f01.health.overview",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_shows_explainable_resource_health_instead_of_an_opaque_score",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_marks_missing_or_invalid_health_values_unknown",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_does_not_call_health_values_fresh_without_request_correlation",
        ],
    },
    "D01-F23": {
        # The regional snapshot has unit coverage on both adapters and bot;
        # retain device acceptance as a separate proof layer.
        "support_state": "needs_device_test",
        "handler_id": "d01.f23.regional.snapshot",
        "test_refs": [
            "XGENT-WDS/tests/test_capabilities.py::test_system_region_snapshot_has_separate_truthful_timezone_and_locale_sources",
            "XGENT-WDS/tests/test_capabilities.py::test_sysinfo_embeds_the_correlated_regional_snapshot",
            "XGENT-MCS/tests/test_capabilities.py::test_system_region_snapshot_has_separate_truthful_timezone_and_locale_sources",
            "XGENT-MCS/tests/test_capabilities.py::test_sysinfo_embeds_the_correlated_regional_snapshot",
            "TG-BOT-SERVER/tests/test_capabilities.py::test_sysinfo_renders_only_reported_timezone_and_locale_as_escaped_values",
        ],
    },
    "P01-F09": {
        # The bot and protocol path are covered locally; physical Settings
        # handoff/permission refresh still needs confirmation on both OSes.
        "support_state": "needs_device_test",
        "handler_id": "p01.f09.permission.recheck",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_capabilities.py::test_permission_settings_success_offers_one_shot_fresh_check_bound_to_permission",
        ],
    },
    "P01-F25": {
        # Unit/integration evidence exists, but no current physical-device
        # acceptance exists; keep this short of the shipped `implemented` state.
        "support_state": "needs_device_test",
        "handler_id": "p01.f25.location.compare",
        "test_refs": [
            "TG-BOT-SERVER/tests/test_location.py::test_comparison_keyboard_and_callback_are_owner_only",
            "XGENT-WDS/tests/test_location.py::test_owner_compare_opt_in_returns_distinct_system_and_ip_sources_without_persisting",
            "XGENT-MCS/tests/test_location.py::test_owner_compare_opt_in_returns_distinct_system_and_ip_sources_without_persisting",
        ],
    },
}

RESULT_TYPE_FEATURES = {
    "text": {
        *(f"B01-F{number:02d}" for number in (1, 2, 3, 4, 5, 18)),
        *(f"D01-F{number:02d}" for number in (*range(1, 8), *range(9, 12), *range(14, 26), *range(27, 31))),
        "D02-F10", "D02-F13", "D02-F22", "D02-F23", "P01-F25",
    },
    "image": {"D02-F01", "D02-F02", "D02-F03", "D02-F04", "D02-F06"},
    "file": {"D01-F29", "D04-F28"},
    "status": {
        "D01-F01", "D01-F07", "D01-F08", "D01-F11", "D01-F12", "D01-F13",
        "D01-F22", "D01-F23", "D01-F26", "D01-F27", "D02-F13", "D02-F23",
        "D02-F22", "P01-F09", "P01-F25",
    },
}
ROW = re.compile(
    r"^\|\s*((?:B01|B02|D0[1-7]|A01|S01|R01|P01|O01|W01|M01|G01)-F\d{2})\s*"
    r"\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$"
)


def _permission_ids(area: str, number: int) -> list[str]:
    if area == "D02":
        if number <= 10:
            return ["screen_recording"]
        if number <= 14:
            return ["camera"]
        if number <= 24:
            return ["microphone"]
        if number <= 29:
            return ["display_control"]
        return []
    if area == "D03":
        return ["owner_file_access"]
    if area == "D05":
        return ["one_time_owner_shell_approval"]
    if area == "D06":
        return ["owner_guardian_or_power_confirmation"]
    if area == "D07":
        return ["explicit_local_effect_consent"]
    if area == "P01":
        return ["permission_status_check"] if number <= 24 else ["one_shot_location_consent"]
    if area == "S01":
        return ["owner_server_action_confirmation"]
    if area == "R01":
        return ["exact_release_and_target_owner_approval"]
    if area == "O01":
        return ["bounded_diagnostic_data_access"]
    if area in {"B01", "B02", "A01"}:
        return ["owner_role_or_explicit_grant"]
    if area == "G01":
        return []
    return []


def build_feature_registry(spec_path: Path | None = None, baseline_commit: str = BASELINE_COMMIT) -> dict:
    """Parse the 510 candidate table rows and attach explicit planned-policy decisions."""
    spec_path = Path(spec_path) if spec_path is not None else ROOT / SPEC
    try:
        spec_text = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read approved feature catalog: {spec_path}") from exc
    rows: dict[str, tuple[str, str]] = {}
    for line in spec_text.splitlines():
        match = ROW.fullmatch(line)
        if match:
            feature_id, title, outcome = (value.strip() for value in match.groups())
            if feature_id in rows:
                raise ValueError(f"Duplicate catalog table row: {feature_id}")
            rows[feature_id] = (title, outcome)

    expected_ids = {
        f"{area}-F{number:02d}"
        for area in AREA_TITLES
        for number in range(1, 31)
    }
    if set(rows) != expected_ids:
        missing = sorted(expected_ids - set(rows))
        unexpected = sorted(set(rows) - expected_ids)
        raise ValueError(f"Catalog must contain exactly 510 unique table rows; missing={missing[:5]} unexpected={unexpected[:5]}")

    areas = [
        {
            "area_id": area,
            "label_id": f"feature.area.{area.lower()}.title",
            "description": bilingual[1],
        }
        for area, bilingual in AREA_TITLES.items()
    ]
    features = []
    for feature_id in sorted(rows):
        area, suffix = feature_id.split("-F", 1)
        number = int(suffix)
        title, outcome = rows[feature_id]
        component_versions = {}
        for component in COMPONENTS:
            if component in AREA_COMPONENTS[area]:
                component_versions[component] = {
                    "state": "planned",
                    "minimum": None,
                    "reason": "not implemented at the selected baseline; set a tested minimum before shipping",
                }
            else:
                component_versions[component] = {
                    "state": "not_applicable",
                    "minimum": None,
                    "reason": f"{area} does not implement behavior in {component}",
                }
        features.append(
            {
                "feature_id": feature_id,
                "area": area,
                "label_id": f"feature.{area.lower()}.f{number:02d}.title",
                "title": title,
                "description": outcome,
                "outcome": outcome,
                "platforms": AREA_PLATFORMS[area],
                "component_min_versions": component_versions,
                "role": ["owner"],
                "grant": {"kind": "owner_only", "key": None},
                "permissions": _permission_ids(area, number),
                "support_state": "planned",
                "baseline_evidence": [
                    {
                        "path": SPEC,
                        "reference": feature_id,
                        "novelty_status": "needs_novelty_audit",
                    }
                ],
                "handler_id": None,
                "test_refs": [],
                "acceptance_level": ["unit", "integration", "device"]
                if area not in {"B01", "B02", "A01", "S01", "R01", "O01", "G01"}
                else ["unit", "integration", "live"],
            }
        )
    for feature in features:
        override = FEATURE_IMPLEMENTATION_OVERRIDES.get(feature["feature_id"])
        if override:
            feature.update(override)
        result_types = [
            kind for kind in ("text", "image", "file", "status")
            if feature["feature_id"] in RESULT_TYPE_FEATURES[kind]
        ]
        if result_types:
            feature["result_types"] = result_types
    navigation = [
        {
            "page_id": page_id,
            "parent": "home",
            "label_id": label_id,
            "callback": callback,
            "roles": roles,
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary" if presentation != "home_admin" else "danger",
            "presentation": presentation,
        }
        for page_id, label_id, callback, roles, presentation in HOME_NAVIGATION
    ] + [
        {
            "page_id": page_id,
            "parent": "device",
            "label_id": label_id,
            "callback": callback,
            "roles": ["owner", "user"],
            "grants": [],
            "platforms": ["windows", "macos"],
            "permissions": [],
            "support_state": "implemented",
            "style": style,
            "presentation": presentation,
        }
        for page_id, label_id, callback, style, presentation in DEVICE_NAVIGATION
    ] + [
        {
            "page_id": "device.tags",
            "parent": "device",
            "label_id": "device_tags",
            "callback": "catalog:tags:list",
            "roles": ["owner"],
            "grants": [],
            "platforms": ["windows", "macos"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "device_tags",
        },
        {
            "page_id": "device.pinned_toggle",
            "parent": "device",
            "label_id": "device_pinned_toggle",
            "callback": "catalog:pinned:toggle:*",
            "roles": ["owner"],
            "grants": [],
            "platforms": ["windows", "macos"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "device_pin_toggle",
        },
    ] + [
        {
            "page_id": page_id,
            "parent": parent,
            "label_id": label_id,
            "callback": callback,
            "roles": ["owner"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": style,
            "presentation": presentation,
        }
        for parent, rows in (("admin", ADMIN_NAVIGATION), ("events.admins", ADMIN_USERS_NAVIGATION), ("server", SERVER_NAVIGATION))
        for page_id, label_id, callback, style, presentation in rows
    ] + [
        {
            "page_id": "catalog.filter.result",
            "parent": "catalog",
            "label_id": "catalog.filter.result",
            "callback": "catalog:filter:result",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.filter.permission",
            "parent": "catalog",
            "label_id": "catalog.filter.permission",
            "callback": "catalog:filter:permission",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.filter.access",
            "parent": "catalog",
            "label_id": "catalog.filter.access",
            "callback": "catalog:filter:access",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.search.exact",
            "parent": "catalog",
            "label_id": "catalog.search.exact",
            "callback": "catalog:search:exact",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.search.outcome",
            "parent": "catalog",
            "label_id": "catalog.search.outcome",
            "callback": "catalog:search:outcome",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.search.fuzzy",
            "parent": "catalog",
            "label_id": "catalog.search.fuzzy",
            "callback": "catalog:search:fuzzy",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.home",
            "parent": "catalog",
            "label_id": "navigation.home",
            "callback": "menu:main",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "nav",
        },
        *[
            {
                "page_id": f"catalog.area.{area.lower()}",
                "parent": "catalog",
                "label_id": f"feature.area.{area.lower()}.title",
                "callback": "catalog:area:*",
                "roles": ["owner", "user", "guest"],
                "grants": [],
                "platforms": ["telegram"],
                "permissions": [],
                "support_state": "implemented",
                "style": "primary",
                "presentation": "catalog_area",
            }
            for area in AREA_TITLES
        ],
        *[
            {
                "page_id": f"catalog.filter.result.{result_type}",
                "parent": "catalog.filter.types",
                "label_id": f"catalog.filter.result.{result_type}",
                "callback": f"catalog:filter:result:{result_type}",
                "roles": ["owner", "user", "guest"],
                "grants": [],
                "platforms": ["telegram"],
                "permissions": [],
                "support_state": "implemented",
                "style": "primary",
                "presentation": "catalog_result_type",
            }
            for result_type in ("text", "image", "file", "status")
        ],
        {
            "page_id": "catalog.filter.types.sections",
            "parent": "catalog.filter.types",
            "label_id": "catalog.sections",
            "callback": "catalog:areas",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.filter.types.home",
            "parent": "catalog.filter.types",
            "label_id": "navigation.home",
            "callback": "menu:main",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "nav",
        },
        *[
            {
                "page_id": page_id,
                "parent": "catalog.area.page",
                "label_id": label_id,
                "callback": callback,
                "roles": ["owner", "user", "guest"],
                "grants": [],
                "platforms": ["telegram"],
                "permissions": [],
                "support_state": "implemented",
                "style": "primary",
                "presentation": "catalog_action",
            }
            for page_id, label_id, callback in (
                ("catalog.page.previous", "navigation.previous", "catalog:area:*"),
                ("catalog.page.next", "navigation.next", "catalog:area:*"),
                ("catalog.page.sections", "catalog.sections", "catalog:areas"),
                ("catalog.page.home", "navigation.home", "menu:main"),
            )
        ],
        {
            "page_id": "catalog.feature.back",
            "parent": "catalog.feature.detail",
            "label_id": "catalog.detail.back",
            "callback": "catalog:back",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_action",
        },
        {
            "page_id": "catalog.feature.home",
            "parent": "catalog.feature.detail",
            "label_id": "navigation.home",
            "callback": "menu:main",
            "roles": ["owner", "user", "guest"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "nav",
        },
        {
            "page_id": "catalog.feature.synonym.add",
            "parent": "catalog.feature.detail",
            "label_id": "catalog.synonym.add",
            "callback": "catalog:synonym:add:*",
            "roles": ["owner"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_synonym",
        },
        {
            "page_id": "catalog.feature.synonym.cancel",
            "parent": "catalog.feature.synonym.prompt",
            "label_id": "catalog.synonym.cancel",
            "callback": "catalog:synonym:cancel",
            "roles": ["owner"],
            "grants": [],
            "platforms": ["telegram"],
            "permissions": [],
            "support_state": "implemented",
            "style": "primary",
            "presentation": "catalog_synonym_cancel",
        },
        *[
            {
                "page_id": page_id,
                "parent": parent,
                "label_id": label_id,
                "callback": callback,
                "roles": ["owner", "user", "guest"],
                "grants": [],
                "platforms": ["telegram"],
                "permissions": [],
                "support_state": "implemented",
                "style": "primary",
                "presentation": presentation,
            }
            for parent, page_id, label_id, callback, presentation in (
                ("catalog.search.prompt", "catalog.search.prompt.sections", "catalog.sections", "catalog:search:cancel", "catalog_action"),
                ("catalog.search.prompt", "catalog.search.prompt.home", "navigation.home", "menu:main", "nav"),
                ("catalog.search.results", "catalog.search.results.sections", "catalog.sections", "catalog:areas", "catalog_action"),
                ("catalog.search.results", "catalog.search.results.home", "navigation.home", "menu:main", "nav"),
                ("catalog.search.results", "catalog.search.results.feature", "feature.catalog.result.title", "catalog:feature:*", "catalog_feature"),
                ("catalog.filter.results", "catalog.filter.results.sections", "catalog.sections", "catalog:areas", "catalog_action"),
                ("catalog.filter.results", "catalog.filter.results.home", "navigation.home", "menu:main", "nav"),
                ("catalog.filter.results", "catalog.filter.results.feature", "feature.catalog.result.title", "catalog:feature:*", "catalog_feature"),
            )
        ],
    ]

    def add_nav(page_id, parent, label_id, callback, style="primary", *, roles=("owner", "user"), grants=(), platforms=("windows", "macos"), when=None, presentation="nav"):
        row = {
            "page_id": page_id, "parent": parent, "label_id": label_id, "callback": callback,
            "roles": list(roles), "grants": list(grants), "platforms": list(platforms), "permissions": [],
            "support_state": "implemented", "style": style, "presentation": presentation,
        }
        if when:
            row["when"] = when
        navigation.append(row)

    for page_id, label_id, callback in (
        ("install.os.windows", "manual.install.windows", "install:windows"),
        ("install.os.macos", "manual.install.macos", "install:macos"),
        ("install.os.back", "manual.install.back", "menu:devices"),
    ):
        add_nav(
            page_id, "install.os", label_id, callback, "primary",
            roles=("owner",), platforms=("telegram",), presentation="install_os",
        )

    for page_id, label_id, callback, roles, presentation in (
        ("devices.row", "devices.device_row", "dev:*", ("owner", "user", "guest"), "devices_row"),
        ("devices.search", "devices.search", "devices:search", ("owner", "user"), "devices_list_action"),
        ("devices.filter_os", "devices.filter_os", "devices:filter-os", ("owner",), "devices_list_action"),
        ("devices.filter_heartbeat", "devices.filter_heartbeat", "devices:filter-heartbeat", ("owner",), "devices_list_action"),
        ("devices.filter_version", "devices.filter_version", "devices:filter-version", ("owner",), "devices_list_action"),
        ("devices.filter_capability", "devices.filter_capability", "devices:filter-capability", ("owner",), "devices_list_action"),
        ("devices.filter_inventory_age", "devices.filter_inventory_age", "devices:filter-inventory-age", ("owner",), "devices_list_action"),
        ("devices.saved_sets", "devices.saved_sets", "catalog:snapshot:list", ("owner",), "devices_list_action"),
        ("devices.collections", "devices.collections", "catalog:collection:list", ("owner",), "devices_list_action"),
        ("devices.saved_filters", "devices.saved_filters", "catalog:saved-filter:list", ("owner",), "devices_list_action"),
        ("devices.sort", "devices.sort", "devices:sort", ("owner",), "devices_list_action"),
        ("devices.order", "devices.order", "catalog:order:edit", ("owner",), "devices_list_action"),
        ("devices.pinned", "devices.pinned", "catalog:pinned:list", ("owner",), "devices_list_action"),
        ("devices.activity_history", "devices.activity_history", "catalog:inactivity:list", ("owner",), "devices_list_action"),
        ("devices.export", "feature.b02.f28.title", "catalog:export", ("owner",), "devices_list_action"),
        ("devices.import", "feature.b02.f29.title", "catalog:import", ("owner",), "devices_list_action"),
        ("devices.archive", "devices_archived", "catalog:archive:list", ("owner",), "devices_list_action"),
        ("devices.all", "all_devices", "dev:all", ("owner",), "devices_list_action"),
        ("devices.add", "devices_add_button", "devmg:manualadd", ("owner",), "devices_list_action"),
        ("devices.install", "manual_install_button", "install:manual", ("owner",), "devices_list_action"),
        ("devices.blocked", "devices_blocked_button", "devmg:blocked", ("owner",), "devices_list_action"),
        ("devices.clear", "devices_clear_button", "devmg:clear_all", ("owner",), "devices_list_action"),
        ("devices.refresh", "refresh", "menu:devices", ("owner", "user", "guest"), "devices_list_action"),
        ("devices.home", "home", "menu:main", ("owner", "user", "guest"), "devices_list_action"),
    ):
        style = {
            "devices.row": "success",
            "devices.install": "success",
            "devices.refresh": "success",
            "devices.blocked": "danger",
            "devices.clear": "danger",
        }.get(page_id, "primary")
        add_nav(
            page_id, "devices", label_id, callback, style,
            roles=roles, platforms=("telegram",), presentation=presentation,
        )

    for page_id, label_id, callback, style in (
        ("all.status", "all_status_button", "all:status", "primary"),
        ("all.screenshot", "all_screenshot_button", "all:screenshot", "success"),
        ("all.location", "geo_system", "cmd:geo_location", "primary"),
        ("all.lock", "all_lock_button", "cmd:lock", "danger"),
        ("all.mute", "all_mute_button", "cmd:volume", "primary"),
        ("all.stop", "all_stop_button", "cfm:stop_all", "danger"),
        ("all.back", "devices_back_list_button", "menu:target", "primary"),
        ("all.home", "home", "menu:main", "primary"),
    ):
        add_nav(
            page_id, "all.devices", label_id, callback, style,
            roles=("owner",), platforms=("telegram",), presentation="all_device_action",
        )

    for page_id, label_id, callback in (
        ("device.response.back", "back_device", "back:device"),
        ("device.response.home", "home", "menu:main"),
    ):
        add_nav(
            page_id, "device.response", label_id, callback, "primary",
            roles=("owner", "user", "guest"), platforms=("telegram",), presentation="nav",
        )

    add_nav("admin.home", "admin", "home", "menu:main", roles=("owner", "user", "guest"), platforms=("telegram",))
    for page_id, label_id, callback, style, when in POWER_NAVIGATION:
        add_nav(page_id, "power", label_id, callback, style, when=when)
    for action in ("shutdown", "reboot", "sleep"):
        add_nav(
            f"power.confirm.{action}", "power.confirm", "power_confirm_button",
            f"power_confirm:{action}", "danger", presentation="power_confirmation",
        )
    add_nav("power.confirm.cancel", "power.confirm", "cancel", "back:device", "primary")
    add_nav("power.confirm.home", "power.confirm", "home", "menu:main", "primary")
    for page_id, label_id, callback, style in GUARDIAN_NAVIGATION:
        add_nav(page_id, "guardian", label_id, callback, style)
    for page_id, label_id, callback, style, roles in DEVICE_SETTINGS_NAVIGATION:
        add_nav(page_id, "device.settings", label_id, callback, style, roles=roles)
    for parent, rows in (
        ("screen", SCREEN_NAVIGATION), ("input", INPUT_NAVIGATION), ("system", SYSTEM_NAVIGATION),
        ("network", NETWORK_NAVIGATION), ("files", FILES_NAVIGATION),
    ):
        for row in rows:
            page_id, label_id, callback, style, *optional = row
            roles = optional[0] if optional and isinstance(optional[0], list) else ("owner", "user")
            presentation = next((value for value in optional if isinstance(value, str)), "screen_action")
            if callback in {"camera:quality", "camera:catalog"}:
                roles = ("owner",)
            platforms = ("windows",) if callback == "cmd:nightlight" else ("windows", "macos")
            if callback in {"filevault:status", "sip:status", "gatekeeper:status"}:
                platforms = ("macos",)
            add_nav(
                page_id, parent, label_id, callback, style, roles=roles,
                platforms=platforms, presentation=presentation,
            )
    add_nav(
        "files.inbox", "files", "feature.d03.f27.title", "files:inbox", "success",
        roles=("owner",), platforms=("windows", "macos"), presentation="file_inbox",
    )
    for page_id, parent, label_id, callback, style, presentation in (
        ("files.inbox.upload", "files.inbox", "file_inbox_upload", "files:inbox:upload", "success", "file_inbox_action"),
        ("files.inbox.row", "files.inbox", "file_inbox_row", "files:inbox:view:*", "primary", "file_inbox_row"),
        ("files.inbox.back", "files.inbox", "back_device", "cat:files", "primary", "nav"),
        ("files.inbox.home", "files.inbox", "home", "menu:main", "primary", "nav"),
        ("files.inbox.detail.place", "files.inbox.detail", "file_inbox_place", "files:inbox:place:*", "success", "file_inbox_action"),
        ("files.inbox.detail.delete", "files.inbox.detail", "file_inbox_delete", "files:inbox:delete:*", "danger", "file_inbox_action"),
        ("files.inbox.detail.back", "files.inbox.detail", "back_device", "files:inbox", "primary", "nav"),
        ("files.inbox.detail.home", "files.inbox.detail", "home", "menu:main", "primary", "nav"),
        ("files.inbox.place.confirm", "files.inbox.place.confirm", "file_inbox_place_confirm", "files:inbox:place:confirm:*", "danger", "file_inbox_action"),
        ("files.inbox.place.cancel", "files.inbox.place.confirm", "file_inbox_cancel", "files:inbox:view:*", "primary", "nav"),
        ("files.inbox.place.home", "files.inbox.place.confirm", "home", "menu:main", "primary", "nav"),
        ("files.inbox.delete.confirm", "files.inbox.delete.confirm", "file_inbox_delete_confirm", "files:inbox:delete:confirm:*", "danger", "file_inbox_action"),
        ("files.inbox.delete.cancel", "files.inbox.delete.confirm", "file_inbox_cancel", "files:inbox:view:*", "primary", "nav"),
        ("files.inbox.delete.home", "files.inbox.delete.confirm", "home", "menu:main", "primary", "nav"),
    ):
        add_nav(
            page_id, parent, label_id, callback, style,
            roles=("owner",), platforms=("telegram",), presentation=presentation,
        )
    add_nav(
        "system.cpu_history", "system", "feature.d01.f02.title", "cpu:history", "success",
        roles=("owner",), presentation="cpu_history",
    )
    for period in (15, 30, 60):
        add_nav(
            f"system.cpu_history.period.{period}", "system.cpu_history", f"cpu_history_{period}s",
            f"cpu:history:{period}", "primary", roles=("owner",), presentation="cpu_history_period",
        )
    add_nav("system.cpu_history.back", "system.cpu_history", "back_device", "cat:system", roles=("owner",))
    add_nav("system.cpu_history.home", "system.cpu_history", "home", "menu:main", roles=("owner",))
    add_nav(
        "system.memory_history", "system", "feature.d01.f03.title", "memory:history", "success",
        roles=("owner",), presentation="memory_history",
    )
    for period in (15, 30, 60):
        add_nav(
            f"system.memory_history.period.{period}", "system.memory_history", f"memory_history_{period}s",
            f"memory:history:{period}", "primary", roles=("owner",), presentation="memory_history_period",
        )
    add_nav("system.memory_history.back", "system.memory_history", "back_device", "cat:system", roles=("owner",))
    add_nav("system.memory_history.home", "system.memory_history", "home", "menu:main", roles=("owner",))
    add_nav(
        "system.memory_pressure_history", "system", "feature.d01.f05.title", "pressure:history", "success",
        roles=("owner",), presentation="memory_pressure_history",
    )
    for period in (15, 30, 60):
        add_nav(
            f"system.memory_pressure_history.period.{period}", "system.memory_pressure_history",
            f"memory_pressure_history_{period}s", f"pressure:history:{period}", "primary",
            roles=("owner",), presentation="memory_pressure_history_period",
        )
    add_nav("system.memory_pressure_history.back", "system.memory_pressure_history", "back_device", "cat:system", roles=("owner",))
    add_nav("system.memory_pressure_history.home", "system.memory_pressure_history", "home", "menu:main", roles=("owner",))
    add_nav(
        "system.thermal_snapshot", "system", "feature.d01.f06.title", "thermal:snapshot", "success",
        roles=("owner",), presentation="thermal_snapshot",
    )
    add_nav(
        "system.battery_health", "system", "feature.d01.f07.title", "battery:health", "success",
        roles=("owner",), presentation="battery_health",
    )
    add_nav(
        "system.battery_cycles", "system", "feature.d01.f08.title", "battery:cycles", "success",
        roles=("owner",), presentation="battery_cycles",
    )
    add_nav(
        "system.energy_usage", "system", "feature.d01.f09.title", "energy:usage", "success",
        roles=("owner",), presentation="energy_usage",
    )
    add_nav(
        "system.sleep_wake_history", "system", "feature.d01.f10.title", "sleep:history", "success",
        roles=("owner",), presentation="sleep_wake_history",
    )
    add_nav(
        "system.startup_duration", "system", "feature.d01.f11.title", "boot:duration", "success",
        roles=("owner",), presentation="startup_duration",
    )
    add_nav(
        "system.pending_restart", "system", "feature.d01.f12.title", "reboot:status", "success",
        roles=("owner",), presentation="pending_restart",
    )
    add_nav(
        "system.os_support", "system", "feature.d01.f13.title", "os:support", "success",
        roles=("owner",), presentation="os_support",
    )
    add_nav(
        "system.hardware_inventory", "system", "feature.d01.f14.title", "hardware:history", "success",
        roles=("owner",), presentation="hardware_inventory_history",
    )
    add_nav(
        "system.driver_inventory", "system", "feature.d01.f15.title", "drivers:history", "success",
        roles=("owner",), presentation="driver_inventory_history",
    )
    add_nav(
        "system.installed_app_inventory", "system", "feature.d01.f16.title", "apps:history", "success",
        roles=("owner",), presentation="installed_app_history",
    )
    add_nav(
        "system.startup_inventory", "system", "feature.d01.f17.title", "startup:history", "success",
        roles=("owner",), presentation="startup_inventory_history",
    )
    add_nav(
        "system.service_inventory", "system", "feature.d01.f18.title", "services:history", "success",
        roles=("owner",), presentation="service_inventory_history",
    )
    add_nav(
        "system.crash_summary", "system", "feature.d01.f19.title", "crash:summary", "success",
        roles=("owner",), presentation="crash_summary",
    )
    add_nav(
        "system.smart_trend", "system", "feature.d01.f20.title", "smart:trend", "success",
        roles=("owner",), presentation="smart_trend",
    )
    add_nav(
        "system.volume_topology", "system", "feature.d01.f21.title", "volume:topology", "success",
        roles=("owner",), presentation="volume_topology",
    )
    add_nav(
        "system.clock_drift", "system", "feature.d01.f22.title", "clock:drift", "success",
        roles=("owner",), presentation="clock_drift",
    )
    add_nav(
        "system.summary_history", "system", "feature.d01.f24.title", "sysinfo:history", "success",
        roles=("owner",), presentation="system_summary_history",
    )
    add_nav(
        "system.summary_comparison", "system", "feature.d01.f25.title", "sysinfo:compare", "success",
        roles=("owner",), presentation="system_summary_comparison",
    )
    add_nav(
        "system.summary_export", "system", "feature.d01.f29.title", "sysinfo:export", "success",
        roles=("owner",), presentation="system_summary_export",
    )
    add_nav(
        "system.summary_interval", "system", "feature.d01.f26.title", "sysinfo:interval", "success",
        roles=("owner",), presentation="system_summary_interval",
    )
    add_nav(
        "system.summary_thresholds", "system", "feature.d01.f27.title", "sysinfo:threshold", "success",
        roles=("owner",), presentation="system_summary_thresholds",
    )
    add_nav(
        "system.summary_aggregate", "system", "feature.d01.f28.title", "sysinfo:aggregate", "success",
        roles=("owner",), presentation="system_summary_aggregate",
    )
    add_nav(
        "system.summary_uncertainty", "system", "feature.d01.f30.title", "sysinfo:uncertainty", "success",
        roles=("owner",), presentation="system_summary_uncertainty",
    )
    add_nav(
        "system.volume_history", "system", "feature.d01.f04.title", "volume:history", "success",
        roles=("owner",), presentation="volume_history",
    )
    for row in TERMINAL_NAVIGATION:
        page_id, label_id, callback, style, *optional = row
        presentation = optional[0] if optional else "screen_action"
        roles = optional[1] if len(optional) > 1 else ("owner", "user", "guest")
        add_nav(page_id, "terminal", label_id, callback, style, roles=roles, presentation=presentation)
    for page_id, label_id, callback, style in EVENTS_NAVIGATION:
        roles = ("owner", "user", "guest") if page_id == "events.home" else ("owner",)
        add_nav(page_id, "events", label_id, callback, style, roles=roles, platforms=("telegram",))
    for page_id, label_id, callback in QUIET_NAVIGATION:
        add_nav(page_id, "events.quiet", label_id, callback, roles=("owner",), platforms=("telegram",))
    for page_id, label_id, callback in DIGEST_NAVIGATION:
        add_nav(page_id, "events.digest", label_id, callback, roles=("owner",), platforms=("telegram",))
    add_nav("admins.back", "events.admins", "events_back_button", "ev:menu", roles=("owner",), platforms=("telegram",))
    add_nav("admins.home", "events.admins", "home", "menu:main", roles=("user", "guest"), platforms=("telegram",))
    for seconds in (5, 15, 30, 60):
        add_nav(
            f"mic.options.duration.{seconds}", "mic.options", "microphone_duration_button",
            f"micdur:{seconds}", "success", roles=("owner",), presentation="mic_duration",
        )
    add_nav(
        "mic.options.level", "mic.options", "microphone_level", "mic:level", "primary",
        roles=("owner",), presentation="microphone_level",
    )
    add_nav(
        "mic.options.balance", "mic.options", "feature.d02.f21.title", "mic:balance", "primary",
        roles=("owner",), presentation="microphone_balance",
    )
    add_nav(
        "mic.options.history", "mic.options", "feature.d02.f24.title", "media:history", "primary",
        roles=("owner",), presentation="microphone_history",
    )
    add_nav(
        "mic.options.outputs", "mic.options", "audio_output_catalog", "audio:outputs", "primary",
        roles=("owner",), presentation="audio_output_catalog",
    )
    add_nav(
        "mic.options.output_switch", "mic.options", "audio_output_switch", "audio:switch", "primary",
        roles=("owner",), presentation="audio_output_switch",
    )
    add_nav(
        "mic.options.test", "mic.options", "microphone_test", "mic:test", "success",
        roles=("owner",), presentation="microphone_test",
    )
    add_nav(
        "mic.options.preview", "mic.options", "feature.d02.f20.title", "mic:preview", "primary",
        roles=("owner",), presentation="microphone_preview",
    )
    add_nav("mic.options.back", "mic.options", "media", "cat:media", "danger", presentation="nav")
    add_nav("mic.options.home", "mic.options", "home", "menu:main", presentation="nav")
    for level in (0, 25, 50, 75, 100):
        add_nav(
            f"volume.options.level.{level}", "volume.options", "volume_level_button",
            f"volq:{level}", presentation="volume_level",
        )
    add_nav("volume.options.back", "volume.options", "media", "cat:media", "danger", presentation="nav")
    add_nav("volume.options.home", "volume.options", "home", "menu:main", presentation="nav")
    for angle, label_id in (
        (0, "rotate_standard_button"), (90, "rotate_right_button"),
        (180, "rotate_inverted_button"), (270, "rotate_left_button"),
    ):
        add_nav(
            f"rotate.options.angle.{angle}", "rotate.options", label_id,
            f"rotate:{angle}", "success" if angle == 0 else "primary",
            presentation="rotate_angle",
        )
    add_nav("rotate.options.back", "rotate.options", "rotate_screen_button", "cat:screen", "danger", presentation="screen_action")
    add_nav("rotate.options.home", "rotate.options", "home", "menu:main", presentation="nav")
    for page_id, label_id, callback, style in (
        ("wallpaper.options.random", "wallpaper_random_meme_button", "cmd:wallpaper_random_meme", "success"),
        ("wallpaper.options.photo", "wallpaper_photo_button", "cmd:wallpaper_photo_guide", "primary"),
        ("wallpaper.options.url", "wallpaper_url_button", "fun:wallpaper", "primary"),
        ("wallpaper.options.restore", "wallpaper_restore_button", "cmd:prank_restore_wallpaper", "danger"),
        ("wallpaper.options.back", "wallpaper_back_screen_button", "cat:screen", "primary"),
    ):
        add_nav(page_id, "wallpaper.options", label_id, callback, style, presentation="screen_action")
    add_nav("wallpaper.options.home", "wallpaper.options", "home", "menu:main", presentation="nav")
    for count in (1, 2, 3, 5):
        add_nav(
            f"url.count.options.{count}", "url.count.options", "url_count_button",
            f"url:count:{count}", "success" if count == 1 else "primary",
            presentation="url_count",
        )
    add_nav(
        "url.count.options.favorite_save", "url.count.options", "url_favorite_save_button",
        "url:fav:save", "primary", roles=("owner",), presentation="url_count_action",
    )
    add_nav(
        "url.count.options.cancel", "url.count.options", "url_cancel_button",
        "url:cancel", "danger", roles=("owner", "user", "guest"),
        presentation="url_count_action",
    )
    add_nav(
        "url.count.options.home", "url.count.options", "home", "menu:main",
        roles=("owner", "user", "guest"), presentation="nav",
    )
    add_nav(
        "url.favorites.open", "url.favorites", "url_favorite_open_button",
        "url:fav:open:*", "success", roles=("owner", "user"),
        platforms=("telegram",), presentation="url_favorite_open",
    )
    add_nav(
        "url.favorites.delete", "url.favorites", "url_favorite_delete_button",
        "url:fav:delete:*", "danger", roles=("owner",),
        platforms=("telegram",), presentation="url_favorite_delete",
    )
    add_nav("url.favorites.back", "url.favorites", "back_device", "back:device", "danger", roles=("owner", "user"), platforms=("telegram",))
    add_nav("url.favorites.home", "url.favorites", "home", "menu:main", roles=("owner", "user"), platforms=("telegram",))
    add_nav(
        "guest.devices.row", "guest.devices", "guest_device_row", "guest:readonly",
        roles=("owner", "user", "guest"), platforms=("telegram",), presentation="guest_device_row",
    )
    add_nav(
        "guest.devices.home", "guest.devices", "guest_home", "menu:main",
        roles=("owner", "user", "guest"), platforms=("telegram",),
    )
    add_nav(
        "admin.users.row", "admin.users", "admin_user_row", "admin:user:*",
        roles=("owner",), platforms=("telegram",), presentation="admin_user_row",
    )
    add_nav(
        "admin.users.empty", "admin.users", "no_users", "noop",
        roles=("owner",), platforms=("telegram",), presentation="admin_users_empty",
    )
    add_nav("admin.users.back", "admin.users", "back", "menu:admin", roles=("owner",), platforms=("telegram",))
    add_nav("admin.user.role", "admin.user.detail", "make_user", "admin:role:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_role")
    add_nav("admin.user.block", "admin.user.detail", "block_user", "admin:block:*", "danger", roles=("owner",), platforms=("telegram",), presentation="admin_user_block")
    add_nav("admin.user.devices", "admin.user.detail", "grant_devices", "admin:devices:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_action")
    add_nav("admin.user.permissions", "admin.user.detail", "grant_buttons", "admin:perms:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_action")
    add_nav("admin.user.temporary_block", "admin.user.detail", "temporary_block", "admin:blocktemp:*", "danger", roles=("owner",), platforms=("telegram",), presentation="admin_user_action")
    add_nav("admin.user.message", "admin.user.detail", "message_user", "admin:message:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_action")
    add_nav("admin.user.role_history", "admin.user.detail", "feature.a01.f11.title", "admin:role-history:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_role_history")
    add_nav("admin.user.grant_history", "admin.user.detail", "feature.a01.f12.title", "admin:grant-history:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_grant_history")
    add_nav("admin.user.revoke_all_grants", "admin.user.detail", "feature.a01.f24.title", "admin:revoke-user:*", "danger", roles=("owner",), platforms=("telegram",), presentation="admin_user_revoke_all_grants")
    add_nav("admin.user.pause_grants", "admin.user.detail", "feature.a01.f25.title", "admin:pause-user:*", "primary", roles=("owner",), platforms=("telegram",), presentation="admin_user_pause_grants")
    add_nav("admin.user.permission_profiles", "admin.user.detail", "feature.a01.f03.title", "admin:profiles:*", "primary", roles=("owner",), platforms=("telegram",), presentation="admin_user_permission_profiles")
    add_nav("admin.permission_profiles.save", "admin.permission_profiles", "feature.a01.f03.title", "admin:profile:save:*", "success", roles=("owner",), platforms=("telegram",), presentation="admin_permission_profile_save")
    add_nav("admin.permission_profiles.row", "admin.permission_profiles", "feature.a01.f03.title", "admin:profile:apply:*", "primary", roles=("owner",), platforms=("telegram",), presentation="admin_permission_profile_row")
    add_nav("admin.permission_profiles.page", "admin.permission_profiles", "feature.a01.f03.title", "admin:profiles:page:*", "primary", roles=("owner",), platforms=("telegram",), presentation="admin_permission_profile_page")
    add_nav("admin.permission_profiles.cancel", "admin.permission_profile_prompt", "back", "admin:profile:cancel:*", "primary", roles=("owner",), platforms=("telegram",), presentation="admin_permission_profile_cancel")
    add_nav("admin.permission_profiles.back", "admin.permission_profiles", "back_users", "admin:user:*", roles=("owner",), platforms=("telegram",))
    add_nav("admin.user.owner_invariant", "admin.user.detail", "feature.a01.f27.title", "admin:owner-invariant", roles=("owner",), platforms=("telegram",), presentation="admin_owner_invariant")
    add_nav("admin.user.policy_snapshot", "admin.user.detail", "feature.a01.f28.title", "admin:policy-snapshot", roles=("owner",), platforms=("telegram",), presentation="admin_rbac_snapshot")
    add_nav("admin.user.policy_compare", "admin.user.detail", "feature.a01.f29.title", "admin:policy-compare", roles=("owner",), platforms=("telegram",), presentation="admin_rbac_compare")
    add_nav("admin.user.grant_undo", "admin.user.detail", "feature.a01.f30.title", "admin:grant-undo:*", roles=("owner",), platforms=("telegram",), presentation="admin_user_grant_undo")
    add_nav("admin.audit.filter", "admin.audit", "audit_filter", "admin:audit:filter", roles=("owner",), platforms=("telegram",), presentation="audit_filter")
    add_nav("admin.audit.export", "admin.audit", "audit_export", "admin:audit:export", roles=("owner",), platforms=("telegram",), presentation="audit_export")
    add_nav("admin.audit.event.row", "admin.audit", "audit_event_row", "admin:audit:event:*", roles=("owner",), platforms=("telegram",), presentation="audit_event_row")
    add_nav("admin.audit.back", "admin.audit", "back", "admin:audit:back", roles=("owner",), platforms=("telegram",))
    add_nav("admin.stale_grants.refresh", "admin.stale_grants", "stale_grants_refresh", "admin:stale-grants:refresh", roles=("owner",), platforms=("telegram",), presentation="stale_grants_refresh")
    add_nav("admin.stale_grants.back", "admin.stale_grants", "back", "menu:admin", roles=("owner",), platforms=("telegram",))
    add_nav("admin.access_review.refresh", "admin.access_review", "access_review_refresh", "admin:access-review", roles=("owner",), platforms=("telegram",), presentation="access_review_refresh")
    add_nav("admin.access_review.page", "admin.access_review", "access_review_page", "admin:access-review:page:*", roles=("owner",), platforms=("telegram",), presentation="access_review_page")
    add_nav("admin.access_review.back", "admin.access_review", "back", "menu:admin", roles=("owner",), platforms=("telegram",))
    add_nav("admin.permission_requests.row", "admin.permission_requests", "access_request_row", "admin:access-request:view:*", roles=("owner",), platforms=("telegram",), presentation="permission_request_row")
    add_nav("admin.permission_requests.refresh", "admin.permission_requests", "access_requests_refresh", "admin:access-requests", roles=("owner",), platforms=("telegram",), presentation="permission_requests")
    add_nav("admin.permission_requests.page", "admin.permission_requests", "access_requests_page", "admin:access-requests:page:*", roles=("owner",), platforms=("telegram",), presentation="permission_requests")
    add_nav("admin.permission_requests.back", "admin.permission_requests", "back", "menu:admin", roles=("owner",), platforms=("telegram",))
    add_nav("admin.permission_request.approve", "admin.permission_request.detail", "access_request_approve", "admin:access-request:approve:*", "success", roles=("owner",), platforms=("telegram",), presentation="permission_request_decision")
    add_nav("admin.permission_request.reject", "admin.permission_request.detail", "access_request_reject", "admin:access-request:reject:*", "danger", roles=("owner",), platforms=("telegram",), presentation="permission_request_decision")
    add_nav("admin.permission_request.back", "admin.permission_request.detail", "back", "admin:access-requests", roles=("owner",), platforms=("telegram",))
    add_nav("admin.audit.event.back", "admin.audit.event", "back", "admin:audit", roles=("owner",), platforms=("telegram",))
    add_nav("admin.user.back", "admin.user.detail", "back_users", "admin:users", roles=("owner",), platforms=("telegram",))
    add_nav(
        "admin.devices.grant.row", "admin.devices.grants", "permission_enabled",
        "admin:device:*", roles=("owner",), platforms=("telegram",),
        presentation="admin_device_grant",
    )
    add_nav(
        "admin.devices.grant.temporary.row", "admin.devices.grants", "temporary_grant",
        "admin:devicetemp:*", roles=("owner",), platforms=("telegram",),
        presentation="admin_device_temporary_grant",
    )
    add_nav(
        "admin.devices.grants.back", "admin.devices.grants", "back_to_user",
        "admin:user:*", roles=("owner",), platforms=("telegram",),
    )
    add_nav(
        "admin.permissions.grant.row", "admin.permissions.grants", "permission_enabled",
        "admin:perm:*", roles=("owner",), platforms=("telegram",),
        presentation="admin_permission_grant",
    )
    add_nav(
        "admin.permissions.grant.temporary.row", "admin.permissions.grants", "temporary_grant",
        "admin:permtemp:*", roles=("owner",), platforms=("telegram",),
        presentation="admin_permission_temporary_grant",
    )
    add_nav(
        "admin.permissions.grants.back", "admin.permissions.grants", "back_to_user",
        "admin:user:*", roles=("owner",), platforms=("telegram",),
    )
    add_nav(
        "admin.text.preview", "admin.texts", "admin_text_preview_button",
        "admin:text:*", roles=("owner",), platforms=("telegram",),
        presentation="admin_text_preview",
    )
    add_nav("admin.text.style", "admin.texts", "change_style", "admin:style", roles=("owner",), platforms=("telegram",))
    add_nav("admin.text.back", "admin.texts", "back", "menu:admin", roles=("owner",), platforms=("telegram",))
    for style_name in ("xtech", "xperson", "xpikmi", "xtarped", "xcore", "xadam"):
        add_nav(
            f"admin.style.choice.{style_name}", "admin.style", "admin_style_button",
            f"admin:style:set:{style_name}", roles=("owner",), platforms=("telegram",),
            presentation="admin_style_choice",
        )
    add_nav("admin.style.back", "admin.style", "back", "menu:admin", roles=("owner",), platforms=("telegram",))
    add_nav(
        "catalog.snapshot.toggle", "catalog.snapshot.selection", "snapshot_device_row",
        "catalog:snapshot:toggle:*", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_device_row",
    )
    add_nav(
        "catalog.snapshot.save", "catalog.snapshot.selection", "snapshot_selection_save",
        "catalog:snapshot:name", "success", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_selection_action",
    )
    add_nav(
        "catalog.snapshot.cancel", "catalog.snapshot.selection", "snapshot_selection_cancel",
        "catalog:snapshot:cancel", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_selection_action",
    )
    add_nav(
        "catalog.snapshot.create", "catalog.snapshot.list", "snapshot_catalog_create",
        "catalog:snapshot:start", "success", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_catalog_action",
    )
    add_nav(
        "catalog.snapshot.row", "catalog.snapshot.list", "snapshot_catalog_row",
        "catalog:snapshot:view:*", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_catalog_row",
    )
    add_nav(
        "catalog.snapshot.back", "catalog.snapshot.list", "back_device", "menu:devices",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.snapshot.home", "catalog.snapshot.list", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.snapshot.detail.delete", "catalog.snapshot.detail", "snapshot_detail_delete",
        "catalog:snapshot:delete:*", "danger", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_detail_action",
    )
    add_nav(
        "catalog.snapshot.detail.back", "catalog.snapshot.detail", "back_device", "catalog:snapshot:list",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.snapshot.detail.home", "catalog.snapshot.detail", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.snapshot.delete.confirm", "catalog.snapshot.delete.confirm", "snapshot_delete_confirm",
        "catalog:snapshot:delete:confirm:*", "danger", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_delete_action",
    )
    add_nav(
        "catalog.snapshot.delete.cancel", "catalog.snapshot.delete.confirm", "snapshot_delete_cancel",
        "catalog:snapshot:view:*", "primary", roles=("owner",), platforms=("telegram",),
        presentation="snapshot_delete_action",
    )
    add_nav(
        "catalog.collection.create", "catalog.collection.list", "collection_create",
        "catalog:collection:create", "success", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.collection.row", "catalog.collection.list", "collection_row",
        "catalog:collection:view:*", roles=("owner",), platforms=("telegram",), presentation="device_collection_row",
    )
    add_nav(
        "catalog.collection.edit", "catalog.collection.detail", "collection_edit",
        "catalog:collection:edit:*", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.collection.back", "catalog.collection.list", "back_device", "menu:devices",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.collection.detail.back", "catalog.collection.detail", "collection_list",
        "catalog:collection:list", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.collection.home", "catalog.collection.list", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.create", "catalog.saved_filter.list", "saved_filter_create",
        "catalog:saved-filter:create", "success", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.row", "catalog.saved_filter.list", "saved_filter_row",
        "catalog:saved-filter:view:*", roles=("owner",), platforms=("telegram",), presentation="saved_filter_row",
    )
    add_nav(
        "catalog.saved_filter.back", "catalog.saved_filter.list", "back_device", "menu:devices",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.home", "catalog.saved_filter.list", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.apply", "catalog.saved_filter.detail", "saved_filter_apply",
        "catalog:saved-filter:apply:*", "success", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.delete", "catalog.saved_filter.detail", "saved_filter_delete",
        "catalog:saved-filter:delete:*", "danger", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.detail.back", "catalog.saved_filter.detail", "saved_filter_list",
        "catalog:saved-filter:list", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.delete.confirm", "catalog.saved_filter.delete", "saved_filter_delete_confirm",
        "catalog:saved-filter:delete:confirm:*", "danger", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.delete.cancel", "catalog.saved_filter.delete", "saved_filter_delete_cancel",
        "catalog:saved-filter:view:*", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.saved_filter.delete.home", "catalog.saved_filter.delete", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    for sort_id, label_id, callback in (
        ("catalog.device_sort.name", "sort_devices_name", "devices:sort:name"),
        ("catalog.device_sort.platform", "sort_devices_platform", "devices:sort:platform"),
        ("catalog.device_sort.version", "sort_devices_version", "devices:sort:version"),
        ("catalog.device_sort.last_seen", "sort_devices_last_seen", "devices:sort:last_seen"),
        ("catalog.device_sort.custom", "sort_devices_custom", "devices:sort:custom"),
    ):
        add_nav(sort_id, "catalog.device_sort", label_id, callback, roles=("owner",), platforms=("telegram",), presentation="device_sort_choice")
    add_nav("catalog.device_sort.back", "catalog.device_sort", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_order.up", "catalog.device_order", "device_order_up", "catalog:order:up:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_order_move")
    add_nav("catalog.device_order.down", "catalog.device_order", "device_order_down", "catalog:order:down:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_order_move")
    add_nav("catalog.device_order.back", "catalog.device_order", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("device.compare", "device", "feature.b02.f13.title", "catalog:compare:start:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_compare_start")
    add_nav("device.duplicate_candidate", "device", "feature.b02.f22.title", "catalog:duplicate:compare:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_duplicate_candidate")
    add_nav("device.completeness", "device", "feature.b02.f14.title", "catalog:completeness:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_completeness")
    add_nav("device.registration_origin", "device", "feature.b02.f15.title", "catalog:registration:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_registration_origin")
    add_nav("device.inventory_wizard", "device", "feature.b02.f16.title", "catalog:wizard:start:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_inventory_wizard")
    add_nav("device.private_note", "device", "feature.b02.f17.title", "catalog:note:view:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_private_note")
    add_nav("catalog.device_private_note.edit", "catalog.device_private_note", "private_note_edit", "catalog:note:edit:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_private_note_action")
    add_nav("catalog.device_private_note.back", "catalog.device_private_note", "back_device", "back:device", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_private_note.home", "catalog.device_private_note", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_private_note.cancel", "catalog.device_private_note.edit_prompt", "private_note_cancel", "catalog:note:cancel:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_private_note_action")
    add_nav("device.place_label", "device", "feature.b02.f19.title", "catalog:place:edit:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_place_label")
    add_nav("catalog.device_place_label.cancel", "catalog.device_place_label.prompt", "place_label_cancel", "catalog:place:cancel:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_place_label_action")
    add_nav("device.review_date", "device", "feature.b02.f24.title", "catalog:review:edit:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_review_date")
    add_nav("catalog.device_review_date.cancel", "catalog.device_review_date.prompt", "review_date_cancel", "catalog:review:cancel:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_review_date_action")
    add_nav("device.maintenance", "device", "feature.b02.f25.title", "catalog:maintenance:edit:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_maintenance")
    add_nav("catalog.device_maintenance.cancel", "catalog.device_maintenance.prompt", "maintenance_cancel", "catalog:maintenance:cancel:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_maintenance_action")
    add_nav("device.archive", "device", "feature.b02.f27.title", "catalog:archive:set:*", "danger", roles=("owner",), platforms=("windows", "macos"), presentation="device_archive")
    add_nav("catalog.device_archive.row", "catalog.device_archive.list", "archive_row", "catalog:archive:restore:*", "success", roles=("owner",), platforms=("telegram",), presentation="device_archive_row")
    add_nav("catalog.device_archive.previous", "catalog.device_archive.list", "previous", "catalog:archive:page:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_archive_page")
    add_nav("catalog.device_archive.next", "catalog.device_archive.list", "next", "catalog:archive:page:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_archive_page")
    add_nav("catalog.device_archive.back", "catalog.device_archive.list", "back_device", "menu:devices", "primary", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("device.links", "device", "feature.b02.f30.title", "catalog:link:manage:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_relations")
    add_nav("catalog.device_links.add", "catalog.device_links.view", "relation_add", "catalog:link:add:*", "success", roles=("owner",), platforms=("telegram",), presentation="device_relation_action")
    add_nav("catalog.device_links.unlink", "catalog.device_links.view", "relation_unlink", "catalog:link:unlink:*", "danger", roles=("owner",), platforms=("telegram",), presentation="device_relation_action")
    add_nav("catalog.device_links.cancel", "catalog.device_links.view", "back_device", "catalog:link:cancel:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_relation_action")
    for field in ("device_id", "name", "os", "version", "online", "last_seen"):
        add_nav(f"catalog.export.field.{field}", "catalog.device_export", "export_field", f"catalog:export:field:{field}", "primary", roles=("owner",), platforms=("telegram",), presentation="device_export_field")
    add_nav("catalog.export.format.json", "catalog.device_export", "export_json", "catalog:export:format:json", "primary", roles=("owner",), platforms=("telegram",), presentation="device_export_format")
    add_nav("catalog.export.format.csv", "catalog.device_export", "export_csv", "catalog:export:format:csv", "primary", roles=("owner",), platforms=("telegram",), presentation="device_export_format")
    add_nav("catalog.export.run", "catalog.device_export", "export_run", "catalog:export:run", "primary", roles=("owner",), platforms=("telegram",), presentation="device_export_run")
    add_nav("catalog.export.cancel", "catalog.device_export", "export_cancel", "catalog:export:cancel", "danger", roles=("owner",), platforms=("telegram",), presentation="device_export_cancel")
    add_nav("catalog.device_import.cancel", "catalog.device_import", "import_cancel", "catalog:import:cancel", "danger", roles=("owner",), platforms=("telegram",), presentation="device_catalog_import_action")
    add_nav("device.purpose", "device", "feature.b02.f20.title", "catalog:purpose:view:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_purpose")
    for purpose in ("personal", "test", "work", "other"):
        add_nav(f"catalog.device_purpose.{purpose}", "catalog.device_purpose", f"purpose_{purpose}", f"catalog:purpose:set:{purpose}:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_purpose_choice")
    add_nav("catalog.device_purpose.clear", "catalog.device_purpose", "purpose_clear", "catalog:purpose:clear:*", "danger", roles=("owner",), platforms=("telegram",), presentation="device_purpose_choice")
    add_nav("catalog.device_purpose.back", "catalog.device_purpose", "back_device", "back:device", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_purpose.home", "catalog.device_purpose", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("device.inventory_template.save", "device", "feature.b02.f21.title", "catalog:template:save:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_inventory_template")
    add_nav("device.inventory_template.apply", "device", "feature.b02.f21.title", "catalog:template:apply:*", "primary", roles=("owner",), platforms=("windows", "macos"), presentation="device_inventory_template")
    add_nav("catalog.inventory_template.select", "catalog.device_inventory_template", "inventory_template_select", "catalog:template:preview:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_inventory_template_action")
    add_nav("catalog.inventory_template.confirm", "catalog.device_inventory_template.preview", "inventory_template_confirm", "catalog:template:confirm:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_inventory_template_action")
    add_nav("catalog.inventory_template.cancel", "catalog.device_inventory_template", "inventory_template_cancel", "catalog:template:cancel:*", "danger", roles=("owner",), platforms=("telegram",), presentation="device_inventory_template_action")
    add_nav("catalog.inventory_template.preview_back", "catalog.device_inventory_template.preview", "inventory_template_back", "catalog:template:back:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_inventory_template_action")
    add_nav("catalog.inventory_template.save_cancel", "catalog.device_inventory_template.prompt", "inventory_template_save_cancel", "catalog:template:save-cancel:*", "danger", roles=("owner",), platforms=("telegram",), presentation="device_inventory_template_action")
    add_nav("catalog.inventory_template.back", "catalog.device_inventory_template", "back_device", "back:device", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.inventory_template.home", "catalog.device_inventory_template", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_inventory_wizard.skip", "catalog.device_inventory_wizard", "wizard_skip", "catalog:wizard:skip:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_inventory_wizard_action")
    add_nav("catalog.device_inventory_wizard.cancel", "catalog.device_inventory_wizard", "wizard_cancel", "catalog:wizard:cancel:*", "danger", roles=("owner",), platforms=("telegram",), presentation="device_inventory_wizard_action")
    add_nav("catalog.device_compare.row", "catalog.device_compare", "compare_device_row", "catalog:compare:second:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_compare_row")
    add_nav("catalog.device_compare.page", "catalog.device_compare", "compare_page", "catalog:compare:page:*", "primary", roles=("owner",), platforms=("telegram",), presentation="device_compare_page")
    add_nav("catalog.device_compare.back", "catalog.device_compare", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_compare.result.back", "catalog.device_compare.result", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_compare.result.home", "catalog.device_compare.result", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_completeness.back", "catalog.device_completeness.result", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_completeness.home", "catalog.device_completeness.result", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_registration.back", "catalog.device_registration.result", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.device_registration.home", "catalog.device_registration.result", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.pinned.row", "catalog.pinned.list", "pinned_device_row", "dev:*", roles=("owner",), platforms=("telegram",), presentation="devices_row")
    add_nav("catalog.pinned.back", "catalog.pinned.list", "back_device", "menu:devices", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav("catalog.pinned.home", "catalog.pinned.list", "home", "menu:main", roles=("owner",), platforms=("telegram",), presentation="nav")
    add_nav(
        "catalog.snapshot.delete.home", "catalog.snapshot.delete.confirm", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.inactivity.capture", "catalog.inactivity.list", "inactivity_capture",
        "catalog:inactivity:capture", "success", roles=("owner",), platforms=("telegram",),
        presentation="inactivity_action",
    )
    add_nav(
        "catalog.inactivity.compare", "catalog.inactivity.list", "inactivity_compare",
        "catalog:inactivity:compare", roles=("owner",), platforms=("telegram",),
        presentation="inactivity_action",
    )
    add_nav(
        "catalog.inactivity.back", "catalog.inactivity.list", "back_device", "menu:devices",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.inactivity.home", "catalog.inactivity.list", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.inactivity.result.back", "catalog.inactivity.compare", "back",
        "catalog:inactivity:list", roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.inactivity.result.home", "catalog.inactivity.compare", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.fields.edit", "catalog.fields.list", "typed_field_edit_row",
        "catalog:fields:edit:*", roles=("owner",), platforms=("telegram",),
        presentation="typed_field_edit",
    )
    add_nav(
        "catalog.fields.create", "catalog.fields.list", "typed_field_create",
        "catalog:fields:create", "success", roles=("owner",), platforms=("telegram",),
        presentation="typed_field_action",
    )
    add_nav(
        "catalog.fields.back", "catalog.fields.list", "typed_field_back",
        "catalog:fields:back", roles=("owner",), platforms=("telegram",),
        presentation="typed_field_action",
    )
    add_nav(
        "catalog.fields.home", "catalog.fields.list", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.tags.edit", "catalog.tags.list", "device_tags_edit",
        "catalog:tags:edit", "primary", roles=("owner",), platforms=("telegram",),
        presentation="device_tags_action",
    )
    add_nav(
        "catalog.tags.back", "catalog.tags.list", "back_device", "back:device",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    add_nav(
        "catalog.tags.home", "catalog.tags.list", "home", "menu:main",
        roles=("owner",), platforms=("telegram",), presentation="nav",
    )
    for parent, label_id, callback, style in PRANK_NAVIGATION:
        supported_platforms = ("windows",) if callback == "cmd:prank_random_clicks" else ("windows", "macos")
        add_nav(
            f"{parent}.{callback.replace(':', '.')}", parent, label_id, callback, style,
            platforms=supported_platforms,
        )
        if callback == "prank:matrix":
            navigation[-1]["support_state"] = "needs_device_test"
    for page_id, label_id, callback in PRANK_TABS:
        add_nav(page_id, "prank.tabs", label_id, callback, "primary")

    return {
        "schema_version": 1,
        "baseline": {"repository": "invinby/XIDER", "commit": baseline_commit},
        "areas": areas,
        "navigation": navigation,
        "features": features,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=ROOT / SPEC)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/features/feature-registry.json")
    parser.add_argument("--baseline-commit", default=BASELINE_COMMIT)
    args = parser.parse_args()
    registry = build_feature_registry(args.spec, args.baseline_commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(registry['features'])} feature entries to {args.output}")
    print("Novelty remains unverified; every candidate is marked needs_novelty_audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
