# X-MAP — command support and permission register

X-MAP keeps the source-level Windows/macOS support, permission profile, and
verification state for every worker command and every Guard Keeper command.
The generated snapshot is [`x-map-command-matrix.json`](x-map-command-matrix.json).

Regenerate it after changing bot actions, agent handlers, or Guardian branches:

```powershell
py -3.12 ops/x_map_audit.py --matrix-only --output docs/x-map-command-matrix.json
```

Run the full static compatibility report with:

```powershell
py -3.12 ops/x_map_audit.py
```

## Reading the matrix

- `source_support` describes only what the Python source declares: a worker
  command must be present in both `SUPPORTED_COMMANDS` and the handler map;
  Guardian commands must have a literal command branch. This is not proof that
  a button works on a real device.
- `permission_profile_ids` points into the matrix's `permission_profiles`
  catalogue. Profiles describe expected OS/session gates visible from source;
  they do not inspect local Privacy settings, ACLs, camera availability, or
  whether the user granted a permission. XIDER does not elevate around an OS
  denial.
- `source_behavior` records known implementation limits found during source
  review, such as an explicitly unsupported action, a joke-only fallback, or a
  missing optional executable. `handler_present` means only that a handler
  exists; it is not a success claim.
- `expected_result` describes the evidence required before the bot can present
  an outcome: correlated typed result, explicit media success state, OS request
  outcome (not a permission grant), or request/launch acceptance without claiming
  a visible effect. A worker/Guardian receipt alone is never device success.
- `stability.state = source_only_unverified` means source wiring exists but
  there is no current physical-device acceptance record for that command.
- `known_source_limited` and `dependency_gated_unverified` distinguish known
  source limitations from commands whose outcome depends on optional software
  or permissions.
- `stability.state = reported_issue_not_retested` preserves a previous device
  report without pretending it has been reproduced or fixed in the current
  release.
- `stability.state = source_gap` means the declared command and handler/branch
  are inconsistent or missing.
- `source_support = unsupported_by_design` and `stability.state =
  known_source_limited` mean a platform-specific command is intentionally
  absent on this OS; they are not implementation gaps.

The current snapshot records 195 worker commands and seven Guard Keeper commands
for each platform. `agent_files` reports bounded inventory metadata; `agent_file_get`
returns only an explicitly requested, fixed allowlisted text file, and its result
is shown only after a separate owner action in the bot. Neither command exposes
configuration, logs, secrets, or arbitrary paths. Mac screenshot and IP-geolocation
rows retain the known unretested reports; the rest remain explicitly source-only
until device tests are recorded. IP geolocation is approximate and is not GPS.

## Outcome and evidence coverage

The generated snapshot has 404 platform rows (202 worker/Guardian commands ×
Windows and macOS). Every row contains both `expected_result` evidence and a
`stability` verification state. The 2026-10-30 snapshot contains 389
`source_only_unverified`, 3 `dependency_gated_unverified`, 10
`known_source_limited`, and 2 `reported_issue_not_retested` rows; there are no
rows without evidence text. These counts describe source audit coverage, not a
successful device trial.

Across command flows, outcomes mean:

- A successful MQTT publish is accepted for delivery only. The bot continues
  waiting for the matching device/request response; delivery alone is not a
  confirmed device effect.
- A confirmed result requires the response appropriate to that command. Media
  requires an explicit successful capture state; launch-only actions remain
  unconfirmed; OS permission requests are not permission grants.
- A correlated failure is shown as failure. An explicit unsupported result is
  shown as unsupported. No matching response after confirmed delivery is a
  timeout; an ambiguous publish, malformed response, or correlation mismatch
  remains unknown.

The matrix records the per-command evidence requirement and whether the
implementation is source-only, dependency-gated, limited, or has a reported
unretested issue. It does not claim every command has been physically exercised
through success, failure, timeout, unsupported, and unknown branches. Those
branches must be checked on-device before claiming runtime acceptance.

`permission_settings` is owner-triggered on both agents. On Windows, Location
uses a visible local confirmation and `Geolocator.RequestAccessAsync` before
any coordinate read; after an existing decision it opens the fixed Location
privacy page. Camera and Microphone open fixed Windows privacy pages because
this unpackaged EXE has no per-app switches for them. macOS requests consent
through OS APIs for Camera, Microphone, Location, Screen Recording, and
Accessibility, and opens fixed System Settings panes when needed. Files still
require a specific file operation or a Settings change. The agent never grants
access itself and reports the OS result separately from whether a prompt was
merely requested.

The JSON snapshot is tested against the current source in
`ops/tests/test_x_map_audit.py`, so a changed handler inventory cannot silently
leave the committed matrix stale.
