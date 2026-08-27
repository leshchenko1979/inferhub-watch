# InferHub Watch

Daily probes of [InferHub](https://inferhub.dev/) Chat Completions streaming shapes. Public report: [leshchenko1979.github.io/inferhub-watch](https://leshchenko1979.github.io/inferhub-watch/).

A red cell means InferHub’s JSON did not match the documented OpenAI Chat Completions shape for that check. It does not mean InferHub was down.

Calls run on GitHub-hosted runners with `INFERHUB_API_KEY` (site owner’s InferHub key). Every cell shows the **resolved publisher** InferHub returned.

Scoring checks: streaming **tool names**, and **prompt cache** on a streaming completion **without** `tools` (~2k-token prefix, pause between retries). Pricing is informational.

## Layout

| Path | Role |
| --- | --- |
| [CONTEXT.md](CONTEXT.md) | Alias, resolved publisher, pass/fail language |
| [models.toml](models.toml) | Aliases to probe (the table is the list; open a GitHub issue to propose another) |
| [candidates.toml](candidates.toml) | Audition routes per model, probed after each board sweep and ranked in the Candidates section |
| [checks/registry.toml](checks/registry.toml) | Check order, titles, scoring |
| [checks/](checks/) | One folder per check (`check.py`, `page.md`, `test.py`) |
| [tests/](tests/) | Cross-cutting wiring only |
| [probe/run.py](probe/run.py) | Writes [data/runs/](data/runs/) |
| [probe/routes.py](probe/routes.py) | Provider discovery: probe arbitrary route strings and print a comparison table — no run JSON written |
| [probe/pricing.py](probe/pricing.py) | Writes [data/pricing.json](data/pricing.json) — cost per M tokens per route from the Management API |
| [site/generate.py](site/generate.py) | Builds HTML from [site/templates/](site/templates/) plus the registry and run files |

## Report sections

- **Spend dashboard** — above Latest results: month-to-date cost, today’s cost, and probe’s share, all from [data/pricing.json](data/pricing.json) (30-day usage logs), plus a 30-day sparkline (log-scaled bars, hover for the day total).
- **Cost per M tokens** — board routes only. Ask in/out billed from usage; the **Δ ask** column compares with the previous day’s snapshot ([data/pricing/](data/pricing/), one file per day) — ↓ is cheaper, ↑ pricier.
- **Candidates** — routes from [candidates.toml](candidates.toml), one table per model. The in-use board route leads; audition routes rank by checks passed, then cache hit, then blended ask. Candidate asks are billed on probe traffic; candidate cache hit comes from the probe, incumbents’ from the billing window. The section and its nav link disappear when there is no config or no candidate cells in the latest run.

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
python3 -m probe.routes cb/gpt-5.6-luna cmc/deepseek/deepseek-v4-pro   # sweep arbitrary routes
PAGES_BASE= python3 site/generate.py
```
