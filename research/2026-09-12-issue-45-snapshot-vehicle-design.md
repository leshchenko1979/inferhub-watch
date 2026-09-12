# Issue #45 — the snapshot vehicle: putting the book where the gate actually reads

**Status: DESIGN ONLY, UNAPPLIED.** No code, no schema, no switch flip, no cost
arithmetic. Prepared from HQ's thread-2 finding and verified first-hand against
the live tree at HEAD `ea51a66`.

This doc supersedes the *vehicle* recommendation in
`2026-09-12-issue-45-accumulation-vehicle-options.md` (§2, §5). Option A there —
the PG `route_book_history` table — stays on the record as prepared/unapplied
evidence, but it is **not** the path to the gate, and the earlier sequence
("basis policy first, then Option A, then ~11 days") would have landed a schema
change the backtest still cannot read.

---

## 0. Why this doc exists

HQ's thread-2 finding: **the gate reads FILES, not a table.** If that holds, the
vehicle I prepared is structurally invisible to the thing it was meant to feed,
and the minimal path is smaller than the one I prepared. Verified first-hand
below, and it holds.

---

## 1. The gate's vehicle, verified

```python
# scripts/auto_route_switch.py:1059
dated_snapshots = pricing.dated_snapshots(root_dir)
# scripts/auto_route_switch.py:1064
gate = official_compare.projection_gate(dated_snapshots)
```

`probe/pricing.py:390 dated_snapshots` — its own docstring calls it *"the single
owner of the dated-snapshot read (site.rundata delegates here)"* — is a **file
reader**:

```python
directory = root / "data" / "pricing"
for path in sorted(directory.glob("*.json")):
    payload = json.loads(path.read_text())
    dated.append((path.stem, payload))
```

It globs `data/pricing/*.json` and keys each payload by `path.stem`.

**Consequence, and it is structural:** a PG table — however well-keyed — is
invisible to `dated_snapshots`. Option A as designed changes the gate's verdict
by exactly zero, for the same reason the ladder in `catalog.json` does: wrong
vehicle. Feeding a PG table to the gate would mean a *second* reader and a
fork of the single-owner contract at `:390`, not a table.

---

## 2. The ladder is already in hand — and thrown away

The snapshot writer already makes the pull that carries the full book.

```python
# probe/pricing.py:89
def fetch_catalog(key: str) -> dict[str, tuple[float, float]]:
    body = _get(f"{MANAGEMENT}/catalog", key)
    ...
    pair = _model_asks(model)          # :107
```

and `_model_asks` (`:77`) is a one-point reduction:

```python
# probe/pricing.py:86
return floor.floor_pair(model)
```

So `pricePointsIn` / `pricePointsOut` — the whole order book — are fetched on
every snapshot build and reduced to a floor pair. Same pattern already found in
`scripts/sync_usage_logs.py:330` (holds `live_models`, `:353` discards them).

**The contrast is exact.** `probe/catalog.py:56 fetch_models` reads the *same*
endpoint (`_get_json(f"{MANAGEMENT}/catalog", key)`, `:58`) and **does** keep the
book — `ladder_json` (`:97-98`), `book_point` (`:104-105`), `ladder_at` (`:110`).
That writer landed in `51e042b` (2026-09-12 16:20:33Z); the last `catalog.json`
refresh was `d490b1e` (2026-09-12 12:12:24Z). Measured: **27 commits touch
`data/catalog.json`; 0 carry `ladder_in`.** So the book is written to a vehicle
the gate never reads, and the gate's own vehicle carries no book at all.

---

## 3. Ask 3 — the two-pull question, ANSWERED

> *"Does the daily sweep's snapshot carry the SAME pull as `catalog.json`, or two
> independent pulls that could disagree on the floor for the same day?"*

**Two independent pulls — and a third quantity nobody had named.**

**(a) Two independent HTTP GETs of the same endpoint.**
`pricing.snapshot()` calls `pricing.fetch_catalog(key)` (`probe/pricing.py:769`);
`probe/catalog.py` calls its own `fetch_models` → `_get_json(...)` (`:58`).
Same `/catalog` endpoint, separate calls, separate code paths.

**(b) In practice they are ~1 second apart, not minutes.**
Today: `data/catalog.json` `generated_at` = `2026-09-12T12:12:23+00:00`;
`data/pricing/2026-09-12.json` `generated_at` = `2026-09-12T12:12:22+00:00`.
The workflow runs the two probe jobs back-to-back, so the pulls are adjacent.

**(c) But the book churns in seconds** — `probe/catalog.py:60` says so in its own
comment: *"two pulls 26 s apart moved 35 of 115 ladders"*. A 1-second gap is not
a guarantee, and two runs of the same sweep can disagree on any given ladder.

**(d) The finding that matters more than the timing: the snapshot's `ask_in` is
NOT the catalog floor.** `route_entry` (`probe/pricing.py:350`) sources it two
different ways:

```python
if not stats:
    ask_in, ask_out = catalog.get(alias) or (None, None)   # catalog floor
    ...
    "source": "catalog" if ask_in is not None else "none",
else:
    "ask_in": stats["ask_in"],                             # billed median
    ...
    "source": "usage-logs",
```

and `stats["ask_in"]` comes from `_median_ask` (`probe/pricing.py:262`), the
median of the newest `ASK_MEDIAN_DAYS = 7` days of billed asks.

Measured today, `data/pricing/2026-09-12.json` against `data/catalog.json`:

| | |
|---|---|
| snapshot routes | 25 |
| `source: usage-logs` | **24** |
| `source: catalog` | 1 |
| routes with both asks | 24 |
| **snapshot ask == catalog floor** | **9** |
| **snapshot ask != catalog floor** | **15** |

And the disagreements are not rounding: `cb/gpt-5.6-sol` **0.045 vs 0.005**
(9×), `cb/gpt-5.6-terra` **0.028 vs 0.002** (14×), `zai/glm-5.3-flash`
**0.0672 vs 0.01035** (6.5×).

**So, plainly, and without papering it:** the snapshot's `ask_in` is a 7-day
median of *billed* asks; the catalog's `ask_in` is the *live floor*. They are
different quantities over different time windows, and they disagree on 15 of 24
routes today. Writing a book point beside the snapshot's `ask_in` would put a
**7-day median** and an **instantaneous book point** in the same row.

The resolution is clean but must be stated: the design writes the book from the
**same `fetch_catalog` pull the snapshot already makes**, so within one row the
book and the *catalog floor* share one instant. The billed median is a third
quantity and stays labelled as such. The design must never claim the snapshot's
`ask_in` is the floor.

---

## 4. The minimal path (3a) — unapplied design

No `route_book_history`. No owner schema call. No new API call. No new spend.
The ladder is already in hand at `probe/pricing.py:769`.

### 4.1 Writer — where the book is joined in

`aggregate_rows` (`:209`) has a fixed key set
(`reqs/tok_in/tok_out/cached/cost/ask_in/ask_out/last_ts`) and **no catalog
argument** — its input is usage rows only, so the join does not belong there
without changing its signature.

The clean join point is the `_entry()` closure inside `snapshot()` (`:792`),
where `catalog` is already in scope from `:769`:

```python
entry = route_entry(entry_stats, catalog, alias, candidate=alias in cand)
entry["book_in"], entry["book_out"], entry["ladder_at"] = books.get(alias, (None, None, None))
```

**Why `_entry()` and not `route_entry()`:** `route_entry`'s catalog contract is
`dict[str, tuple[float, float]]` — `tests/test_pricing.py:140` passes
`{"zai/glm-5.3": (0.045, 0.15)}` literally, and `scripts/predictor_scoreboard.py:313`
and `scripts/ask_precision_receipt.py:95` pass `{}`. Stamping the book *after*
`route_entry` returns leaves that contract, those callers and those tests
untouched.

**The pull must widen.** `fetch_catalog` returns only the floor pair, and it has
a second live consumer — `probe/market.py:433,461`. So do **not** change its
return type. Add a sibling (`fetch_catalog_book(key)` → per-route
`(book_in, book_out, fetched_at)`) and have `snapshot()` call that instead, or
have `fetch_catalog` delegate to it and keep returning the tuple. Either way
`market.py` and the existing `fetch_catalog` mocks stay valid.

`book_point` returns `None`, **never `0.0`**, when a ladder carries no filled
point (`probe/stack.py:139-148`) — so a route with no book reads as "no book
known", and any consumer must fall back explicitly rather than treat it as free.

### 4.2 Size cost — MEASURED, not guessed

Measured by injecting the real ladders from a live catalog dump
(`/tmp/oc-41-catalog-raw.json`, 11 entries / 115 enabled models) into all 17
committed snapshots and re-serializing each with the writer's own
`json.dumps(payload, indent=2) + "\n"`.

Ladder shape (measured): **113 of 115** models carry a ladder; `ladder_in`
points **min 1, median 6, mean 10.6, max 34** (same for `ladder_out`).

| Variant | Delta across 17 files | Growth |
|---|---|---|
| **Book point only** — `book_in`/`book_out`/`ladder_at` (this design) | **+18,184 B (+17.8 KiB)** | **1.082×** |
| Full ladder too — `ladder_in`/`ladder_out` | +282,558 B (+276 KiB) | 2.27× |

Baseline: 222,673 B (217 KiB) → 240,857 B (235 KiB). About **97 B per route**.

The earlier guess of *"17 files × ~115 routes × 17-33 ladder points"* is wrong on
both axes: routes per snapshot run **4-25** (not 115 — only routes with usage,
aliases and candidates land), and ladder points are **median 6 / max 34** (not
17-33). The full ladder is where the bytes are; the book *point* is ~8% growth.

### 4.3 Read path the gate would use

**None is needed.** That is the whole point of this vehicle: `dated_snapshots`
(`probe/pricing.py:390`) already reads these files, `auto_route_switch.py:1059`
already calls it, and `projection_gate` already consumes the result. The book
arrives at the gate by being in the file the gate already opens.

The projection then reads it at the one line already scoped in
`2026-09-12-issue-45-basis-preference-design.md` §1 —
`probe/official_compare.py:72`. Verified to flow to both consumers: the crown
selection at `:280` and `:361` both call `basis.projected(...)` → `route_stats`
→ `inferhub_eff`, which prices on `stats.get("ask_in")`.

### 4.4 Tests the change would carry

- `tests/test_pricing.py`: a route with a book gets `book_in`/`book_out`/
  `ladder_at`; a route absent from the catalog pull gets `None`, not `0.0`.
- `tests/test_pricing.py`: `fetch_catalog`'s tuple contract is unchanged
  (existing mocks at `:218/:229/:247/:426/:455` still pass).
- `tests/test_pricing.py`: `route_entry(stats, {}, alias)` still returns no book
  keys — the join lives in `_entry()`, not in `route_entry`.
- `tests/test_official.py`: a payload carrying `book_in` prices on it after the
  `:72` change; a payload without one falls back to `ask_in` unchanged — the
  no-op-on-history property that makes this additive.

---

## 5. The sequence, corrected

| Step | Gated? | Status |
|---|---|---|
| 1. Book columns on the snapshot writer (§4) | No — additive, inert until step 2 | **prepared, unapplied** |
| 2. `official_compare.py:72` prefers `book_in` | **Yes — basis policy** | prepared, unapplied |
| 3. ~10 days of daily **distinct** transitions | No — it is just time | not started |

**Step 1 alone changes no verdict** — nothing reads the key until step 2, and
step 2 is a change to what the gate MEASURES. `GATE_TOL 0.15` / `GATE_SHARE 0.80`
/ `GATE_MIN_N 10` are untouched by both. `floor` stays codified and
`probe/floor.py` stays the single owner of the ladder rule (#27, not
re-litigated).

The owner's decision therefore collapses from *"schema + ~10 days"* to *"two
small code changes + ~10 days"* — only the basis preference itself stays his.

**Timeline, honestly:** ~10 days of **distinct** transitions, not 10 hours. The
~10-hour figure was the raw transition count and is exactly the fake-`n` trap
measured in the companion doc (11 snapshots → `n=10` scored on **3** distinct
crown pairs; 48 hours still yields **3**). Accumulation cannot be compressed;
only its honesty can be preserved.

---

## 6. Recommendation

1. **Name the snapshot vehicle as the one that feeds the gate.** Option A (PG
   `route_book_history`) stays prepared/unapplied as durable evidence, but it is
   not the unblock: the gate is a file reader (§1).
2. **The minimal path is §4** — book columns on the writer the gate already
   reads, zero new API calls, +17.8 KiB across the whole history.
3. **Both steps stay unapplied.** Step 2 is basis policy and is the owner's call;
   HQ is putting it to him.
4. **Never claim the snapshot's `ask_in` is the floor** — it is a 7-day median of
   billed asks, and it disagrees with the live floor on 15 of 24 routes (§3d).

**Nothing here is applied. Owner's call is the gate.**
