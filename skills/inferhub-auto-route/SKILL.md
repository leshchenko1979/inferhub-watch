---
name: inferhub-auto-route
description: Evaluate InferHub Watch routes by Artificial Analysis IQ, True TPS (corrected for timeouts and failures), and empirical ask-weighted North Star Value, consume the public Grafana board analysis, then optionally switch OpenCrabs defaults and configure runner-up fallback chains on a safe hourly cadence. Use when configuring automatic model-route selection, comparing Value, or installing a scheduled route-switch workflow.
compatibility: Requires OpenCrabs with writable profile config/session storage, a machine-readable InferHub Watch snapshot/API, and Python 3.12+ for the reference helper.
metadata:
  version: "1.2.0"
  source: "https://github.com/leshchenko1979/inferhub-watch"
---

# InferHub automatic route selection

Use this skill to select a better route, not merely to check service health. The policy is user-configured: an inclusive IQ floor plus tool support qualifies candidates; empirical ask weighting and hysteresis decide whether a switch is worthwhile.

External users (including OpenCrabs operators like Adi) can leverage the empirical route analysis published live on our public Grafana board (`https://grafana.l1979.ru/public-dashboards/79b4145fd2f44396908e96e4368906ac`) without running their own scraping infrastructure.

## Hard preflight

1. Read [configuration](references/configuration.md) and create a profile-specific `[inferhub_auto_route]` configuration. Point `dashboard_url` to the public Grafana board or snapshot API. Keep credentials in environment variables or the user's secret store.
2. Complete the OpenCrabs streaming prerequisite in [OpenCrabs integration](references/opencrabs-integration.md). Do not enable live switching until the required `finish_reason` fix is merged, verified, rebuilt, and smoke-tested.
3. Run the workflow in dry-run mode first. Confirm the report contains source freshness, every exclusion reason, current and best route metrics, gain, and the proposed mutation set.

## Hourly workflow

1. Load configuration and validate every key. Refuse live mode for missing credentials, invalid weights, missing provider paths, or a missing data source.
2. Read the machine-readable dashboard/API snapshot. Reject unavailable, malformed, or stale telemetry; never scrape rendered Grafana HTML.
3. Resolve each route's IQ, input/output ask, and tool capability. Exclude missing IQ, non-positive asks, and routes with `supports_tools = false`. Do not apply a separate DeepSeek date rule: DeepSeek routes qualify or fail on the configured IQ floor and tool-support check like every other route.
4. Calculate North Star Value using the configured weights and True TPS (scaling by realized throughput and reliability penalties), rank qualified routes, and compare the best route with the current route. Switch only when `best_value > current_value * (1 + switch_threshold)`; emit a no-op verdict otherwise.
5. In live mode:
   - Update the primary provider's `default_model` and `agent.default_model` to the best route.
   - Configure the runner-up (2nd-best) candidate as `default_model` on `[providers.custom.inferhub-alt]` and pin it first in `[providers.fallback].providers` per [OpenCrabs integration](references/opencrabs-integration.md).
   - Atomically update defaults and transactionally migrate only active surface sessions (`session_bindings`) in the configured provider scope, keeping headless background sessions untouched. Verify each post-write value before reporting a switch.
6. Emit a notification containing the decision evidence, data timestamp, old/new route, runner-up fallback route, IQ, asks, values, gain, session count, and whether the run was dry-run. A notification failure never changes the decision.

## Deployment modes

- **Local helper:** invoke the repository/helper with `--dry-run` during installation, then schedule the live command hourly using the host scheduler. The helper owns file locking and atomic writes.
- **OpenCrabs cron:** install an hourly profile cron whose prompt loads this skill, performs the read-only evaluation, and applies mutations only after the preflight gate. Use quiet delivery for the trigger and configure the result destination per the user's profile.

The package does not install a scheduler, choose a provider, select a chat destination, patch OpenCrabs, or rebuild binaries. See [execution recipe](references/execution-recipe.md) for portable interfaces, phase boundaries, locks, atomic/idempotent ordering, and scheduler details. See [integration](references/opencrabs-integration.md), [route math](references/route-math.md), and [troubleshooting](references/troubleshooting.md) only when those phases are needed.

This package follows the [Agent Skills specification](https://agentskills.io/specification) and its progressive-disclosure guidance: concise frontmatter and workflow here, detailed material in one-level-deep references.

## Example dry run

Invoke the user's configured helper in dry-run mode, for example: `python3 /path/to/route-helper.py --dry-run --config /path/to/profile/config.toml --floor-iq 35 --threshold 0.15`. The bundled `scripts/validate_skill.py --dry-run` validates the package and demonstrates the no-write decision shape; it is not a production mutator.

Expected output is an auditable JSON verdict with `dry_run: true`, `should_switch`, current/best route, runner-up route, source timestamp, gain, and no config/session writes.
