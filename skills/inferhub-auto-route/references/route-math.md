# Qualification and value math

## Qualification

A route is eligible only when all conditions hold:

1. `iq` exists, is finite, and is greater than or equal to `iq_floor`.
2. Tool calling is supported. Explicit `supports_tools = false` always rejects; a known non-chat task type rejects when capability metadata is absent.
3. `ask_in` and `ask_out` are finite and strictly positive.
4. The telemetry snapshot is fresh and structurally valid.

DeepSeek has no special date filter. A DeepSeek route with IQ at or above the configured floor and tool support is treated exactly like any other route.

## North Star Value & True TPS

Value is computed using the canonical North Star formulation, scaling IQ per effective price by True TPS (corrected for timeout delays, retries, and failures) and penalizing operational unreliability:

$$\text{Value} = \left(\frac{\text{IQ}}{\text{eff\_price}}\right) \times \left(\frac{\text{True TPS}}{\text{TPS}_{\text{ref}}}\right)^{0.50} \times \text{Reliability Penalty}$$

Where:
- **$\text{eff\_price}$**: Effective blended price per token based on traffic profile:
  $$\text{eff\_price} = w_{\text{in}} \cdot \text{ask}_{\text{in}} + w_{\text{out}} \cdot \text{ask}_{\text{out}}$$
  The default empirical weights are $w_{\text{in}} = 0.99$ and $w_{\text{out}} = 0.01$, reflecting observed prompt/generation traffic.
- **$\text{True TPS}$**: Realized throughput accounting for provider timeouts, retry waits, and failed requests over a 24-hour rolling window:
  $$\text{True TPS} = \frac{\text{Total Tokens Produced}}{\text{Total Generation Time} + \text{Total Timeout Wait} + \text{Retry Overhead}}$$
  If a provider experiences recurring timeouts or failures, its effective generation speed is penalized accordingly. Routes without observed failures use baseline measured TPS.
- **$\text{TPS}_{\text{ref}}$**: Reference speed constant ($50.0\text{ TPS}$).
- **$\text{Reliability Penalty}$**: Penalty multiplier ($\le 1.0$) applied for error rates exceeding operational thresholds.

Record the exact weights, True TPS metrics, and penalties in every verdict so runs remain reproducible and auditable.

## Hysteresis and no-op

Select the highest-value qualified route. Switch only if it differs from the current route and:

`(best_value - current_value) / current_value > switch_threshold`

If the current route is absent or has no valid value, select the best qualified route but label the reason `current_route_unpriced`, not a normal gain. If no route qualifies, or the gain is at/below the threshold, report `no_op` and preserve all state.

Every decision record includes the source timestamp, configuration digest or values, candidate exclusions, current metrics, best metrics, threshold comparison, and intended side effects.
