# Issue #45 — accumulation vehicle: the two options, and the fake-`n` test

**Design only — nothing applied.** Lane `1122b15e`, 2026-09-12.
Gate constants (`GATE_TOL 0.15` / `GATE_SHARE 0.80` / `GATE_MIN_N 10`) untouched.
The switch stays HELD. No code, no schema, no cost arithmetic.

---

## 0. The constraint both options must pass

The gate's unit of evidence is a **transition**: `official_compare._crown_regrets`
walks `itertools.pairwise(dated)` — one regret per **consecutive snapshot pair** —
and the realized side is the **newer snapshot's trailing `eff_per_mtok`**.

A vehicle that adds snapshots faster than the underlying signal moves therefore
adds *counted* transitions without adding *independent* comparisons. That is the
fake-`n` defect, and it is a property of the gate's own counting rule, not of any
one data source. **Any candidate vehicle must be judged on distinct comparisons,
never on `n`.**

## 1. The measurement (gate's own function, read-only)

`_crown_regrets` run directly on each series, 2026-09-12 ~17:5xZ.
Recomputation of the hourly series reproduces the published
`data/scoreboard.json` → `hourly_cumulative.ordering.cost_regret.n = 247`
exactly, so the measurement is validated against the artifact.

| Series | pairs | scored `n` | distinct crown pairs | pair changes | max verbatim repeat |
|---|---|---|---|---|---|
| **daily** (`data/pricing/*.json`, 17 files) | 16 | 15 | **10** | 9 | 12 (value `0.0`) |
| **hourly cumulative** (306 snapshots) | 305 | 247 | **10** | 13 | 152 (`0.0`), then **70× `0.221246`** |
| hourly slice (true per-hour realized) | 305 | 34 | — | — | 115 pairs have **0** comparables |

The hourly cumulative series scores **247 transitions from 10 distinct crown
pairs**. A non-zero regret repeated **70×** verbatim is the signature — no
independent series lands on `0.221246` seventy times by chance.

### 1a. What a 10-hour accumulation actually buys

Trailing windows of the live hourly series, gate semantics:

| window | pairs | scored `n` | distinct crown pairs | changes |
|---|---|---|---|---|
| 10 snapshots (10 h) | 9 | 9 | **3** | 2 |
| 11 snapshots | 10 | **10** | **3** | 2 |
| 24 snapshots (1 day) | 23 | 23 | **3** | 2 |
| 48 snapshots (2 days) | 47 | 47 | **3** | 3 |

**48 hours of hourly snapshots still yields 3 distinct crown pairs.** `n` grows
linearly with hours; the information does not grow at all. An 11-hour
accumulation would report `n = 10` — *satisfying* `GATE_MIN_N` — on **3 distinct
comparisons**. That is a fake certification, and it is the exact failure mode the
"backtest before the production switch" ruling exists to prevent.

For contrast the **daily** vehicle returns 10 distinct crown pairs from 16 pairs.
That is the honest ratio, and it is why the committed daily snapshots — not an
hourly series — are the gate's vehicle.

## 2. Option A — PG `route_book_history` (owner-gated; prepared, NOT applied)

Persist the full ladder additively, keyed `(route, ladder_at)`, from the
`live_models` the hourly sync **already holds and discards**.

**Why it is free.** `scripts/sync_usage_logs.py:330` calls `fetch_models(key)`
hourly and `:353` hands `live_models` to `sync_route_metrics`, which persists
only the book *point* into one overwritten row (`on conflict (route) do update`).
The full ladder is in memory at that moment. Writing it costs **zero new API
calls and zero new spend**.

### DDL (prepared, not run)

```sql
create table if not exists route_book_history (
    route      text        not null,
    ladder_at  timestamptz not null,
    book_in    numeric(12,6),
    book_out   numeric(12,6),
    ladder_in  jsonb,
    ladder_out jsonb,
    primary key (route, ladder_at)
);
create index if not exists route_book_history_ladder_at_idx
    on route_book_history (ladder_at);
```

`route_metrics` (the live table) keeps its current shape — this is purely
additive, and `book_in`/`book_out`/`ladder_at` there stay the point-in-time view.

### Writer

In `sync_route_metrics` (`scripts/sync_usage_logs.py:192`), beside the existing
upsert at `:246`: insert one row per route per `ladder_at`. Must be guarded by
`probe.pgstore.write_permitted(conn)` (`probe/pgstore.py:213`) — the CI identity
is read-only by design, and a writer that raises there would leave the job red
for a DDL it was never allowed to run. `ensure_schema` (`:311`) carries the DDL.

### Read path

A new `probe/pricing.book_dated_snapshots()` returning the same
`list[tuple[str, dict]]` shape `dated_snapshots` returns, assembled from
`route_book_history` joined to the live `route_metrics` point values, so
`official_compare.projection_gate` consumes it unchanged. Selection between the
two sources belongs in the two existing call sites — `probe/basis.py:77`
(`gate_state`) and `scripts/auto_route_switch.py:1059` — not inside the gate.

### Tests

- `tests/test_pgstore.py`: round-trip a `(route, ladder_at)` pair; assert the
  second write at a new stamp ADDS a row (the whole point — the live table
  overwrites, this one must not).
- `tests/test_pgstore.py`: `write_permitted` False ⇒ the writer skips cleanly and
  writes nothing (mirrors the existing `WritePermissionTests`).
- `tests/test_pricing.py`: `book_dated_snapshots()` returns oldest-first and
  tolerates a route with a null book (must read as "no book known", never free —
  the same contract `load_route_metrics` states at `probe/pgstore.py:545-548`).

### Transition count in 10 hours

**9 consecutive pairs, ~3 distinct crown pairs** (measured above). So the honest
number is **not 10 transitions** — it is 10 *counted* transitions over ~3 real
comparisons. To reach 10 genuinely distinct comparisons you need **~11 days**,
and only going forward: the book columns landed 2026-09-12, so nothing can be
retro-filled.

## 3. Option B — no-PG: hourly dated snapshots under the gate's own glob

The gate's vehicle is `data/pricing/*.json` (`probe/pricing.py:390`
`dated_snapshots`). The candidate is to write hourly files there.

**It fails the fake-`n` test, decisively.** Written as-of cumulatively — the only
way that reproduces the switcher's hourly view — it is the same input that scores
247 transitions from 10 distinct crown pairs, and 3 distinct pairs over 48 hours.
An hourly file series *is* the `hourly_cumulative` series with a different
backing store.

It also breaks three things independently of `n`:

1. **The stem is the date.** `dated_snapshots` keys by `path.stem` (`:410`) and
   consumers compare that key to ISO dates: `prior_snapshot_cutoff` (`:424`,
   `path.stem < today`), `site/rundata.py:280` (`for day, payload in dated`),
   `:434` (`prior_pricing`), `tests/test_official.py:524` (`pair[0] <= day`). An
   hourly stem must still sort and still compare as a date.
2. **The files are git-tracked** (`git ls-files data/pricing/` → 17 files). Hourly
   is 24 data commits/day, ~8,760 files/year, into a repo that already carries a
   data-churn discipline.
3. **The realized side stays trailing.** `_crown_regrets` reads the newer
   snapshot's `eff_per_mtok`, a trailing aggregate, so consecutive hourly
   snapshots differ negligibly. Using true per-hour realized instead is the
   `hourly_slice` semantics: `n = 34` with **115 of 305 pairs having zero
   comparables** — thinner, not better.

**Verdict: rule Option B out.** A bigger `n` that is fake is worse than a small
real one.

## 4. The finding that sits UPSTREAM of both options

**Persisting the book does not make the gate read it.**

`basis.projected` (`probe/basis.py:35`) → `official_compare.inferhub_eff`
(`probe/official_compare.py:66`), which prices on `stats.get("ask_in")`.
`probe/basis.py` contains **zero** references to `book` (grep rc=1), and neither
does `site/board.py`.

The only demonstrated path from the book into the gate is the sensitivity probe's
own line — `/tmp/oc-41-gate.py:65`, `st["ask_in"] = bi` — i.e. it **overwrites
the ask with the book**. `ask` / `floor ask` are codified ontology terms
(`ONTOLOGY.md`), and `route_metrics` states the contract explicitly at
`probe/pgstore.py:545`: *"`ask_in`/`ask_out` are the FLOOR (unchanged meaning);
`book_in`/`book_out` are the book price stamped beside it."*

So there are two ways to make the book reach the crown, and both are guarded:

- **(a) overwrite `ask_in` with the book** — destroys the codified floor
  semantics. Not available.
- **(b) teach the projection to prefer a `book_in` key** — a change to the money
  basis, i.e. **basis policy**, which is on the owner's scope guard.

Consequence: **the vehicle question is downstream of a larger one.** Accumulating
the book in either vehicle changes the gate's verdict by exactly zero until (b)
is decided. That is the owner's actual decision, and it is bigger than the
schema.

## 5. Recommendation

1. **Do not build Option B.** Measured: 3 distinct crown pairs from 48 hours.
2. **Option A is mechanically free and worth having** — but as *durable evidence*
   of the book, not as a gate input. It changes no verdict on its own.
3. **The gate's real unblock is the daily vehicle reaching 10 distinct
   comparisons with the book present**, which needs (b) decided first, then ~11
   days of accumulation.
4. The book's missing consumer (§4 of the #45 finding) and the missing
   projection input are the **same gap seen from two ends**.

**Nothing here is applied. Owner's call is the gate.**
