# Portable execution recipe

The implementation should expose these replaceable interfaces so the skill is portable across dashboards and OpenCrabs profile layouts:

- `load_snapshot(source, credentials) -> Snapshot`: fetch JSON/API data, validate schema, and return the source timestamp. It must never scrape rendered panels.
- `qualify(route, iq_floor) -> (eligible, reasons)`: apply only finite IQ `>= iq_floor`, explicit tool support, and positive finite asks. DeepSeek receives no date exception.
- `rank(candidates, input_weight, output_weight) -> Decision`: calculate ask-weighted value and apply strict hysteresis against the current route.
- `read_state(config_path, database_paths, provider_name) -> State`: read current defaults, provider model list, and active unarchived session scope without writing.
- `apply(decision, state) -> Receipt`: acquire a lock, re-read state, add the route only if absent, atomically update defaults, transact the scoped session migration, and verify the resulting values.
- `notify(receipt, target)`: report the same-run evidence; notification failure is not a reason to retry mutations.

## Phase separation

The read-only phase produces a complete decision record before any side effect:

1. Source URL/path and response status.
2. Source timestamp and freshness limit.
3. Configuration values (IQ floor, weights, threshold, provider scope).
4. Candidate count, qualified count, and exclusion reasons.
5. Current route metrics, selected route metrics, calculated values, and exact gain comparison.
6. `dry_run`, intended config keys, intended session scope, and intended notification target.

The side-effect phase is entered only when the record says `switch` and the finish-reason preflight is verified. It uses a per-profile lock and repeats the source/state read so a stale decision cannot mutate a changed profile. Config replacement and the database transaction are verified before the workflow can emit `switched`. A failed verification emits `mutation_failed`; it must never be upgraded to a switch by a later notification.

## Hourly installation

### Local helper

Run the helper in dry-run mode once, then schedule the same command hourly with an explicit profile configuration and lock. Capture stdout/stderr to the user's scheduler log. The helper must return a non-zero status for blocked or mutation-failed verdicts and zero for a successful switch or safe no-op.

### OpenCrabs hourly cron

Create one hourly OpenCrabs cron for the user's profile. Its prompt should load this skill, read the configured source, perform the read-only phase, and apply the side-effect phase only when all gates pass. Set quiet trigger delivery so the scheduler does not interrupt an interactive session; the workflow itself may deliver the final evidence to `notification_target`. Do not put credentials or fixed chat IDs in the prompt.

Both modes must be idempotent: repeating a no-op makes no writes; repeating a completed switch does not duplicate the provider model entry and migrates zero already-current sessions. Every report says whether the run was `dry_run`, `no_op`, `switched`, or blocked and cites the evidence generated in that same run.
