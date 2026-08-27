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
| [checks/registry.toml](checks/registry.toml) | Check order, titles, scoring |
| [checks/](checks/) | One folder per check (`check.py`, `page.md`, `test.py`) |
| [tests/](tests/) | Cross-cutting wiring only |
| [probe/run.py](probe/run.py) | Writes [data/runs/](data/runs/) |
| [probe/pricing.py](probe/pricing.py) | Writes [data/pricing.json](data/pricing.json) — cost per M tokens per route from the Management API |
| [site/generate.py](site/generate.py) | Builds HTML from [site/templates/](site/templates/) plus the registry and run files |

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
PAGES_BASE= python3 site/generate.py
```
