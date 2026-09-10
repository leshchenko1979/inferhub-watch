# Troubleshooting

| Verdict | Likely cause | Action |
|---|---|---|
| `missing_credentials` | Bearer token environment variable is unset | Set the configured secret outside the skill and retry dry-run. |
| `dashboard_unavailable` | API timeout, non-success response, or unreachable host | Restore the machine-readable endpoint; do not switch from stale local data. |
| `malformed_data` | Required fields or timestamp cannot be parsed | Fix the exporter/schema and rerun validation. |
| `stale_telemetry` | Snapshot is older than `max_data_age_seconds` | Wait for the next sweep or repair the observation pipeline. |
| `no_qualified_candidates` | IQ, tool support, asks, or freshness rejected every route | Inspect per-route exclusion reasons; lower `iq_floor` only as an explicit user decision. |
| `no_op` | No candidate beats hysteresis, or the selected route is already current | Keep state unchanged; this is a successful safe verdict. |
| `integration_missing` | Config/provider/database path is absent or not writable | Correct profile-specific paths and permissions; do not use another profile. |
| `mutation_failed` | Atomic config replacement or database transaction failed | Inspect the named completed step, restore only from a verified backup if needed, and rerun dry-run. |
| `finish_reason_preflight_failed` | OpenCrabs binary lacks or fails the empty-marker smoke check | Merge commit `f0a00eb1ded1d4de1f4f17aa94828d2d84f03b7c`, verify, rebuild, and smoke-test before retrying. |

For support reports, include the run timestamp, configuration keys (not secret values), source timestamp, verdict, and exclusion reasons. Never include bearer tokens or write a credential into a report.
