# Configuration contract

The portable skill is named `inferhub-auto-route`. It assumes an OpenCrabs installation whose configuration and session database are writable by the scheduled helper, a machine-readable InferHub Watch route snapshot or Grafana/API endpoint, and Python 3.12+ for the reference helper. The endpoint must return route/catalog records; the workflow never scrapes rendered dashboard HTML.

## Keys

Configuration is a TOML table named `[inferhub_auto_route]` (or an equivalent JSON/YAML object when a host helper uses another format):

| Key | Type and validation | Default | Meaning |
|---|---|---:|---|
| `iq_floor` | finite number, `0 <= value <= 100` | `35.0` | Inclusive Artificial Analysis IQ floor. This is the only model-quality filter. |
| `input_weight` | finite number, `> 0` | `0.99` | Weight for projected input ask in value denominator. |
| `output_weight` | finite number, `> 0` | `0.01` | Weight for projected output ask. The two weights must sum to `1.0 +/- 1e-9`. |
| `switch_threshold` | finite number, `>= 0` | `0.15` | Required relative value gain. A candidate must exceed current value by `>` this fraction. |
| `dashboard_url` | HTTPS URL (HTTP allowed only for loopback/private explicitly configured endpoints) | none | Machine-readable route snapshot/API URL. Required unless `snapshot_path` is set. |
| `dashboard_token_env` | non-empty environment-variable name | none | Optional name of the environment variable containing the bearer token; never store the token in the skill or repo. |
| `snapshot_path` | existing readable JSON path | none | Optional local snapshot alternative to `dashboard_url`. Exactly one source must be configured. |
| `provider_name` | non-empty OpenCrabs provider identifier | none | Provider scope for config and session updates, e.g. `custom:inferhub`. Required for live mode. |
| `config_path` | existing writable TOML path | OpenCrabs profile config | OpenCrabs `config.toml` to update. |
| `database_paths` | non-empty list of existing writable SQLite paths | OpenCrabs profile DB | Session databases to migrate. |
| `notification_target` | provider-specific destination, or `none` | `none` | Where the run verdict is delivered. Credentials are resolved outside the skill. |
| `schedule` | scheduler expression or documented scheduler name | hourly | Requested cadence; installation remains the user's responsibility. |
| `dry_run` | boolean | `true` on first run | Evaluate and emit evidence without config/session writes. |
| `active_only` | boolean | `true` | Restrict migration to unarchived sessions belonging to `provider_name`. |
| `max_data_age_seconds` | integer, `> 0` | `93600` | Freshness limit; stale telemetry blocks switching. |

`input_weight + output_weight` must equal `1.0` within the stated tolerance. `switch_threshold = 0` is valid but still uses a strict `>` comparison. Relative paths are resolved from the host configuration directory, never from the skill package.

## Failure behavior

- **Missing dashboard credentials:** return `blocked: missing_credentials`; do not fall back to anonymous access or mutate anything.
- **Unavailable dashboard data:** return `blocked: dashboard_unavailable` with endpoint and transport status; retain the current route.
- **Malformed snapshot:** return `blocked: malformed_data` and identify the rejected field; never partially evaluate records.
- **Stale telemetry:** return `blocked: stale_telemetry` with observed and allowed timestamps; do not switch.
- **Missing IQ:** exclude that route with reason `missing_iq`; do not infer IQ from another benchmark or model name.
- **Unsupported tool calling:** exclude routes with explicit `supports_tools = false`; also exclude known non-chat task types when the source provides no capability declaration. Do not switch to a route that cannot execute the agent's tools.
- **Invalid/missing ask:** exclude the route with reason `invalid_ask`; positive finite input and output asks are required.
- **No qualified candidates:** emit a no-op verdict with exclusion reasons and leave all state unchanged.
- **Already-configured route:** do not duplicate it in the provider model list. If it is already the current default, emit `no_op: already_current` unless the strict hysteresis comparison selects a different route.
- **Missing provider/config/database:** fail closed with `blocked: integration_missing`; dry-run may still emit a read-only candidate report if no writes are needed.
- **Write failure:** stop the mutation phase, report `blocked: mutation_failed`, and include which step completed. Use atomic replacement for config and a transaction for session updates; do not claim a switch unless post-write verification proves it.

## Side-effect boundary

Evaluation is read-only: fetch, validate, freshness-check, qualify, calculate value, rank, and produce a decision record. Only after a live run passes the prerequisite gate and the candidate exceeds hysteresis may the workflow mutate config and active sessions. `dry_run = true` always stops before mutation. Notifications are best-effort reporting and never authorize a switch.
