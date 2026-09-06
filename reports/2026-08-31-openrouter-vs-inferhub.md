# Report: OpenRouter vs InferHub — price comparison

**Prepared:** 2026-08-31 04:23Z · lane `776a1fe5` (inferhub-watch)
**Data:** inferhub usage-logs 08-29 09:25 → 08-30 22:15 (4,000 rows) + live refresh 04:24Z; OpenRouter live API (6 keys, 396-model catalog)

---

## 1. Account state

### OpenRouter (live)

| Key | Monthly usage | Note |
|---|---|---|
| `opencrabs` | $16.06 ($0.0083 today) | ops fallback lane — light recent use |
| `Yanina OC Key` | $4.73 | idle recently, $50 limit |
| `ai-gateway` | $0.28 | idle |
| `n8n`, `picoclaw`, `ai-antispam` | ~$0 | unused |

Account total: **$26.38 spent of $65 credits**. OpenRouter is a rounding error in fleet burn — doing its fallback job cheaply.

### InferHub

Last 24h (live pull 04:24Z): **only `zai/glm-5.3-flash` traffic** (200 rows), ask steady at $0.0672/M in — no overnight price moves on any active route. 08-29 was the expensive day: $13.06 on qwen3.8* alone after ali's 10.7× hike.

## 2. Price face-off — sticker $/M in/out

| Model | OpenRouter | InferHub | Verdict |
|---|---|---|---|
| `qwen3.8-max` | **2.00 / 6.00** | 0.14 / 0.42 | inferhub **14× cheaper** — beats even OR's *flash* tier (0.15) |
| `glm-5.3-flash` | 0.075 / 0.25 | 0.0672 / 0.224 | inferhub ~11% cheaper, both fine |
| `ds-v4-flash-0731` | 0.065 / 0.18 | 0.011 / 0.033 | inferhub **6× cheaper** (post-rollback 08-30) |
| `ds-v4-pro-0813` | 0.66 / 1.98 | 0.033 / 0.099 | inferhub **20× cheaper** |
| `ds-v4-flash` (unpinned) | 0.0886 / 0.177 | — | no direct inferhub twin |

Stack inferhub's billing-side cache on top (glm 87%, qwen 76%, from real billed rows) and the billed gap widens to ~6–100×.

## 3. Billed reality at inferhub (cache-adjusted eff $/M)

| Route | Ask in/out | Billed eff $/M | Cache | Note |
|---|---|---|---|---|
| `ali/qwen3.8-max` | 0.15/0.45 | $0.0497 | 76% | still 10.7× hiked, no rollback as of 22:15Z 08-30 |
| `zai/glm-5.3-flash` | 0.0672/0.224 | **$0.0128** | 87% | cheapest board-proven seat, caches at any size |
| `ali/deepseek-v4-flash-0731` | 0.011/0.033 | $0.0180 | 44% | rolled back 08-30 evening; per-replica cache lottery |
| `ali/deepseek-v4-pro-0813` | 0.033/0.099 | $0.0762 | 26% | rolled back; still most expensive billed |
| `cbcn/deepseek-v4-flash` | 0.0051/0.0152 | $0.0115 | ? | cheapest sticker; unproven on board, worth a probe |

## 4. Conclusions

1. **InferHub wins every comparable route**, decisively on qwen3.8-max (14×) and the DeepSeek pair (6–20×). No price case for migrating primary traffic to OpenRouter — even post-hike.
2. **Keep OpenRouter as failover lane.** $16/mo for resilience is cheap insurance: had 08-29's failover routed to OR's qwen at $2.00/M in, that day would have cost ~$190 instead of $13.
3. **Switch advice within inferhub** (unchanged from 08-30 delivery): route bulk/mechanical load to `glm-5.3-flash` now (4× cheaper billed than qwen, same 2/2 board grade); keep qwen for quality-critical primary work until the next board run grades glm on real workload. ds-flash back in play but cache dice-roll makes eff volatile.
4. OpenRouter `:batch` variants run ~2× cheaper — useful for non-urgent batch work if ever needed.

## 5. Open items

- **Price-watch alert**: design approved-pending (`projects/inferhub-watch/files/price-watch-design.md`) — detector on the 06:00Z sweep's free usage rows, notify via verified local transport (agents-box cron → `opencrabs session notify`) or GitHub Actions secrets as box-down fallback.
- `ocg/deepseek-v4-flash` current price unknown (zero traffic since 08-29) — re-measure needs a paid probe, on owner's word.
- Board pricing generator wart: ask taken from latest row instead of mode-of-recent-rows (caused the false "zai cheaper" reading 08-30 morning).
