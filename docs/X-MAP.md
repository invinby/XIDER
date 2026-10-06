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

The current snapshot records 119 worker commands and five Guard Keeper commands
for each platform. Mac screenshot and IP-geolocation rows retain the known
unretested reports; the rest remain explicitly source-only until device tests
are recorded. IP geolocation is approximate and is not GPS.

The JSON snapshot is tested against the current source in
`ops/tests/test_x_map_audit.py`, so a changed handler inventory cannot silently
leave the committed matrix stale.
