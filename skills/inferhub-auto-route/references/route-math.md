# Qualification and value math

## Qualification

A route is eligible only when all conditions hold:

1. `iq` exists, is finite, and is greater than or equal to `iq_floor`.
2. Tool calling is supported. Explicit `supports_tools = false` always rejects; a known non-chat task type rejects when capability metadata is absent.
3. `ask_in` and `ask_out` are finite and strictly positive.
4. The telemetry snapshot is fresh and structurally valid.

DeepSeek has no special date filter. A DeepSeek route with IQ at or above the configured floor and tool support is treated exactly like any other route.

## Value

For asks expressed in the same units as the snapshot:

`value = iq / (input_weight * ask_in + output_weight * ask_out)`

The default empirical weights are `0.99` and `0.01`, reflecting observed traffic. Users may change them, but they must be positive and sum to one. Record the exact weights in every verdict so a later run is reproducible.

## Hysteresis and no-op

Select the highest-value qualified route. Switch only if it differs from the current route and:

`(best_value - current_value) / current_value > switch_threshold`

If the current route is absent or has no valid value, select the best qualified route but label the reason `current_route_unpriced`, not a normal gain. If no route qualifies, or the gain is at/below the threshold, report `no_op` and preserve all state.

Every decision record includes the source timestamp, configuration digest or values, candidate exclusions, current metrics, best metrics, threshold comparison, and intended side effects.
