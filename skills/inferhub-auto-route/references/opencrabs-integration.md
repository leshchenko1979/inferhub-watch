# OpenCrabs integration and finish_reason prerequisite

## Required streaming fix

Some OpenAI-compatible gateways emit an empty-string `finish_reason` between content or tool-call deltas. That marker is non-terminal. OpenCrabs must ignore it; flushing the accumulated tool call at that point can break tool execution before the real terminal chunk arrives.

Before enabling automatic switching, merge or cherry-pick this exact commit into the OpenCrabs source:

- https://github.com/leshchenko1979/opencrabs/commit/f0a00eb1ded1d4de1f4f17aa94828d2d84f03b7c

Then run the repository's required verification (at minimum the relevant streaming regression test and the normal project test/format checks), rebuild the OpenCrabs binary using the project's documented build procedure, and restart or hot-reload that installation. This skill cannot patch source or rebuild a user's binary automatically.

Post-rebuild smoke check: send one normal text request and one tool-calling request through the affected gateway, with an empty intermediate `finish_reason` chunk if the gateway can reproduce it. Confirm the text remains complete, the tool call is emitted once with all arguments, and the final terminal finish reason—not the empty intermediate marker—closes the stream. Do not enable live mutation if either smoke check fails.

## Mutation scope and order

Use the configured provider name and profile paths; never assume a global or another user's profile. The safe order is:

1. Acquire a per-profile lock and re-read config/database state.
2. Revalidate the same snapshot and decision evidence.
3. Add the selected route to the provider model list only if absent.
4. Atomically replace config values for `agent.default_model`, `agent.subagent_model`, and the selected provider's `default_model` as supported by the installed schema.
5. If fallback chain pinning is enabled, configure the runner-up (2nd-best) candidate as `default_model` on `[providers.custom.inferhub-alt]` and pin `inferhub-alt` first in `[providers.fallback].providers`.
6. In one SQLite transaction, update only `archived_at IS NULL` sessions in the configured provider scope.
7. Re-read and verify config and session rows.
8. Emit the switch notification with the verified counts and route.

On any failure, report `mutation_failed` and never claim a switch. Repeated execution is idempotent: no duplicate model-list entry and no additional changes for sessions already on the selected route.

## Fallback chain pinning (Runner-up configuration #49)

OpenCrabs supports automatic provider failover via `[providers.fallback]`. However, `[providers.fallback].providers` accepts a list of *provider names*, not model identifiers. When a primary route degrades, OpenCrabs tries the fallback providers in order using each provider's `default_model`.

To automatically pin the runner-up (2nd-best) InferHub route into the fallback chain without requiring OpenCrabs binary modifications:

1. Configure a secondary provider entry in `config.toml` sharing the same API credentials:
   ```toml
   [providers.custom.inferhub-alt]
   base_url = "https://inferhub.dev/api/v1"
   api_key = "env:INFERHUB_API_KEY"
   default_model = "<runner-up-model>"
   ```
2. Pin `inferhub-alt` as the first entry in `[providers.fallback]`:
   ```toml
   [providers.fallback]
   enabled = true
   providers = ["inferhub-alt", "other-backup-provider"]
   ```
3. When the auto-route workflow switches models:
   - The best route is written to `[providers.custom.inferhub].default_model` and `agent.default_model`.
   - The runner-up candidate is written to `[providers.custom.inferhub-alt].default_model`.
   This ensures high availability: if the top route experiences provider downtime or rate limits, OpenCrabs seamlessly drops to the runner-up without falling back to expensive proprietary models.
