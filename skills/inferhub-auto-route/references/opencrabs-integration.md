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
5. In one SQLite transaction, update only `archived_at IS NULL` sessions in the configured provider scope.
6. Re-read and verify config and session rows.
7. Emit the switch notification with the verified counts and route.

On any failure, report `mutation_failed` and never claim a switch. Repeated execution is idempotent: no duplicate model-list entry and no additional changes for sessions already on the selected route.
