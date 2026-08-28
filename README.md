# InferHub Watch

Daily probes of [InferHub](https://inferhub.dev/) Chat Completions streaming shapes. Public report: [leshchenko1979.github.io/inferhub-watch](https://leshchenko1979.github.io/inferhub-watch/).

A red cell means InferHub’s JSON did not match the documented OpenAI Chat Completions shape for that check. It does not mean InferHub was down.

Calls run on GitHub-hosted runners with `INFERHUB_API_KEY` (site owner’s InferHub key). Every cell shows the **resolved publisher** InferHub returned.

Scoring checks: **core** (one request asserts the stream shape as the consuming runtime parses it — `"finish_reason": ""` fails, empty-string tool-name deltas are tolerated — a named `report_answer` tool call, and a clean Russian answer — text or tool argument; any failure stops the route) and **cache** (the core payload sent again byte-for-byte; pass requires `cached_tokens > 0` on that first repeat). Pricing is informational.

## Layout

| Path | Role |
| --- | --- |
| [CONTEXT.md](CONTEXT.md) | Alias, resolved publisher, pass/fail language |
| [models.toml](models.toml) | Aliases to probe (the table is the list; open a GitHub issue to propose another) |
| [checks/registry.toml](checks/registry.toml) | Check order, titles, scoring |
| [checks/](checks/) | One folder per check (`check.py`, `page.md`, `test.py`) |
| [tests/](tests/) | Cross-cutting wiring only |
| [probe/run.py](probe/run.py) | Writes [data/runs/](data/runs/) |
| [probe/market.py](probe/market.py) | Candidate shortlist from the catalog: predicted $/M under the in-use bar, 7-day no-reprobe |
| [probe/radar.py](probe/radar.py) | Advisory verdict: cheapest passing candidate vs the in-use billed $/M, per family — `python3 -m probe.radar` |
| [probe/routes.py](probe/routes.py) | Provider discovery: probe arbitrary route strings and print a comparison table — no run JSON written |
| [probe/pricing.py](probe/pricing.py) | Writes [data/pricing.json](data/pricing.json) — cost per M tokens per route from the Management API |
| [site/generate.py](site/generate.py) | Builds HTML from [site/templates/](site/templates/) plus the registry and run files |

## Report sections

- **Probe results** — first on the page. One collapsible group per board model: the in-use board route leads, audition routes from the market shortlist (catalog asks, predicted $/M, cheaper-than-in-use only, 7-day no-reprobe) rank by checks passed, then cache hit, then blended ask. Per-model summary chips show the best incumbent, the ranking leader (pass / total; ok green, partial amber, none red, unprobed dim), and the **best-price chip**: in-use $/M vs the cheapest passing challenger (green = none undercuts, amber = under 15% cheaper, red = 15%+). Columns: **tests** (scoring checks passed in the latest run, colored; hover a value for the failed list), cache hit, ask in / out, window (all-pass / probed runs since first seen). Candidate asks are billed on probe traffic; candidate cache hit comes from the probe, incumbents’ from the billing window. The section and its nav link disappear when there are no candidate cells in the latest run.
- **Cost per M tokens** — board routes only, with the **spend dashboard** at the top of the section: month-to-date cost, today’s cost, and probe’s share, all from [data/pricing.json](data/pricing.json) (30-day usage logs), plus a 30-day sparkline (log-scaled bars, hover for the day total). Ask in/out billed from usage; the **Δ ask** column compares with the previous day’s snapshot ([data/pricing/](data/pricing/), one file per day) — ↓ is cheaper, ↑ pricier.
- **Past runs** — the 14-run timeline grid, with the probe origin and per-check tooltips.
- **How we test** — the endpoint, the results-table columns, and one explainer per check.

## Add a check

1. Create `checks/<id>/check.py` with `run(client, alias) -> dict` using [probe/result.py](probe/result.py).
2. Write `checks/<id>/page.md` in plain language (what / pass / fail / who cares), including the request JSON when it matters.
3. Put fixtures in `checks/<id>/test.py`. Keep [tests/](tests/) for registry and site wiring only.
4. Append a `[[checks]]` block to [checks/registry.toml](checks/registry.toml). Set `scores_rank = true` only if the check should rank aliases.
5. Keep the runner on the Python 3.12 standard library.

## Local

```bash
python3 -m unittest discover -s . -q
export INFERHUB_API_KEY=…
python3 -m probe.run
python3 -m probe.pricing
python3 -m probe.market --dry-run   # rank the catalog, print the shortlist, no probe calls
python3 -m probe.radar              # advisory: is the in-use route still the best price?
# Radar notifications (opt-in, local runs): due alerts are delivered to an
# OpenCrabs session instead of stdout only. CI runs stay silent.
#   INFERHUB_RADAR_SESSION=<session-uuid>  target session
#   INFERHUB_RADAR_PROFILE=<profile>       opencrabs profile (optional -p)
INFERHUB_RADAR_SESSION=… python3 -m probe.radar
python3 -m probe.routes cb/gpt-5.6-luna cmc/deepseek/deepseek-v4-pro   # sweep arbitrary routes
PAGES_BASE= python3 site/generate.py
```
