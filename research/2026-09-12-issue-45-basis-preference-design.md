# Issue #45 — basis preference: teaching the projection to read the book

**Design only — nothing applied.** Lane `1122b15e`, 2026-09-12.
Gate constants (`GATE_TOL 0.15` / `GATE_SHARE 0.80` / `GATE_MIN_N 10`) untouched.
The switch stays HELD. No code, no schema, no cost arithmetic.

Prepared in response to HQ's ask of 2026-09-12 ~17:5xZ: *"Prepare the
basis-preference change the same way you prepared Option A… Apply nothing. If the
owner says yes, we execute immediately; if no, we have lost nothing."*

---

## 0. The question this doc answers

`research/2026-09-12-issue-45-accumulation-vehicle-options.md` established that
**any** accumulation vehicle changes the gate's verdict by exactly zero, because
the projection does not read the book at all. This doc designs the change that
would make it read the book — the change that has to be decided *before* the
vehicle question matters.

It is a **basis-preference** change: the money basis the gate prices on. It is on
the owner's scope guard (`SCOPE GUARD: no touch to basis policy`) and is therefore
**prepared, not applied**.

## 1. Where the change would sit

The single change point is one line in the projection's price resolver:

```python
# probe/official_compare.py:66
def inferhub_eff(stats: dict, hit: float | None = None) -> float | None:
    ...
    ask_in, ask_out = stats.get("ask_in"), stats.get("ask_out")   # <- :72
```

The chain that reaches it:

```
site/board.py / auto_route_switch.py / site/ticket.py
  -> probe.basis.projected            (probe/basis.py:35)
       -> official_compare.inferhub_eff   (probe/official_compare.py:66)
            -> stats.get("ask_in")        <- the FLOOR, never the book
```

`stats` is a snapshot route entry (daily vehicle) or a `route_metrics` row (live
vehicle). The book is available in the second and **absent from all 17 committed
daily snapshots** (`book/ladder keys found: NONE`).

**The shape of the change:** prefer `book_in`/`book_out` when the stats carry a
non-null book, else fall back to `ask_in`/`ask_out`. That is deliberately
**additive and reversible** — a snapshot without a book prices exactly as it does
today, so the change is a no-op on the entire committed history and on the daily
vehicle until the book is carried there too.

The contract it must honour is already written down (`probe/pgstore.py:545-548`):

> *"`ask_in`/`ask_out` are the FLOOR (unchanged meaning); `book_in`/`book_out`
> are the book price stamped beside it (issue #41). The book fields are `None` on
> any row written before the book columns existed, which callers must read as
> **'no book known', never as free**."*

So the fallback direction is fixed by the contract: **null book ⇒ the floor**, and
never a zero/cheap substitution. A naive `stats.get("book_in") or ask_in` is
correct here precisely *because* the contract says None means "no book known" —
but it must be written so that a legitimate `0` is impossible to confuse with
absent (the ladder rule owns that, `probe/floor.py`; #27 is not re-litigated).

## 2. Blast radius — why this is basis policy, not a patch

`inferhub_eff` is the money basis for **every** forward-priced surface, not just
the gate. Verified call sites:

| Surface | Call path |
|---|---|
| board decision price | `site/board.py:140`, `:305`, `:336-337` via `basis.board_basis` |
| automated switcher | `scripts/auto_route_switch.py:203` |
| board ticket | `site/ticket.py:45`, `:86` |
| Grafana panels | `scripts/sync_usage_logs.py:299` → `basis.route_bases` |
| scoreboard | `scripts/predictor_scoreboard.py:133` |
| **gate crown selection, BOTH legs** | `probe/official_compare.py:280`, `:361` |
| catalog comparison table | `probe/official_compare.py:529` |

Two consequences that make this an owner call rather than a lane call:

1. **It moves the board's published ranking**, not only the gate's verdict. The
   `ask` / `floor ask` are codified ontology terms (`ONTOLOGY.md`); pricing the
   board on the book changes what the board's "price" column *means*.
2. **It moves the crown selection inside the gate**, so the gate's `n`, `land` and
   `share` all shift together — the measured quantity changes, not just its label.

There is also a **second, unavailable** way to make the book reach the crown:
overwrite `ask_in` with the book (what the sensitivity probe does at
`/tmp/oc-41-gate.py:65`). That destroys the codified floor semantics and is **not
on the table**. The design above is path (b), never path (a).

## 3. It changes what the gate MEASURES, not the thresholds

This is the load-bearing distinction, and it is the codified law:

> *"when an owner ruling conflicts with a codified gate or threshold, never
> satisfy it by loosening the threshold — that launders the ruling into a fake
> certification. Change what the gate MEASURES so it passes honestly."*

- `GATE_TOL = 0.15`, `GATE_SHARE = 0.80`, `GATE_MIN_N = 10`
  (`probe/official_compare.py:233-235`) are **untouched**. They are owner property.
- The change moves **what price the projection computes**, i.e. the input to
  `_crown_regrets` / `_crown_value_regrets` — the MEASURED quantity.
- The same constants then judge a different measurement. That is the honest
  direction: the ruling changes the measurement, never the bar.

If the owner declines, **nothing changes and nothing is lost** — the doc is the
only artifact and it is inert.

## 4. What the 0.800-exactly sensitivity result implies for the pass leg

The sensitivity probe (`/tmp/oc-41-gate.py`, today's live book held constant and
substituted into every historical snapshot — explicitly anachronistic, labelled as
such) gives:

| leg | shipped (snapshot billed-median ask) | book input |
|---|---|---|
| cost | n=15, land=13, **share 0.867**, pass=True | n=15, land=12, **share 0.800**, pass=True |
| value | n=10, land=9, **share 0.900**, pass=True | n=10, land=8, **share 0.800**, pass=True |

**Both book-input legs land exactly on `GATE_SHARE = 0.80`.** Under
`pass = n >= GATE_MIN_N and share >= GATE_SHARE`, that is a pass **by zero
margin** — one further transition landing short flips either leg red.

Three things this implies, and none of them is "loosen the bar":

1. **The margin is the finding.** A basis change that consumes the entire
   tolerance is a decision the owner should take knowing the result is on the
   boundary, not a lane-level tweak that happens to still pass.
2. **The sensitivity number is NOT a backtest.** It substitutes today's book into
   past days, so it cannot be the basis for the ship-or-hold call. It is evidence
   *about the magnitude* of the basis effect, and that magnitude is large.
3. **Do not rescue it by widening anything.** The correct response to a
   zero-margin measurement is either (i) accept it as the measured result and let
   the gate report exactly what it measures, or (ii) accumulate more DISTINCT
   transitions so the share is estimated on more information — which is the
   timeline in §5, not a threshold change.

## 5. The honest expected timeline

**~10 days of DISTINCT transitions, not 10 hours.**

Measured with the gate's own `_crown_regrets`, live hourly series:

| window | scored `n` | distinct crown pairs |
|---|---|---|
| 10 h | 9 | **3** |
| 11 h | 10 | **3** |
| 24 h | 23 | **3** |
| 48 h | 47 | **3** |

Daily vehicle, for contrast: 16 pairs → `n=15` from **10 distinct crown pairs**.

`n` grows linearly with snapshot cadence; the information does not. So the
timeline to a **decision-grade** book backtest is set by the *distinct-transition*
rate (~1/day on the honest ratio), i.e. **~10 days**, and only going forward —
the book columns landed 2026-09-12, so nothing can be retro-filled.

This doc therefore changes **no** timeline. It removes the reason the timeline was
moot: without it, ten days of accumulation still leaves the gate reading the floor.

## 6. Tests the change would carry

- `tests/test_official.py`: `inferhub_eff` with a non-null `book_in`/`book_out`
  prices on the book; with **null** book prices on the floor (the contract:
  "no book known", never free); with the keys **absent** entirely (pre-book
  snapshots) on the floor — so the committed 17 snapshots stay byte-identical.
- `tests/test_official.py`: `projection_gate` over a synthetic pair where the
  book and the floor crown **different** routes — proving the change actually
  moves the crown, rather than being silently inert.
- `tests/test_basis.py`: `basis.projected` passes the book through, and
  `board_basis` falls back to realized exactly as today when the projection is
  unresolvable.
- A guard test asserting `GATE_TOL`/`GATE_SHARE`/`GATE_MIN_N` are unchanged by
  the commit — the constants are owner property and a basis change must never
  move them.

## 7. Recommendation

1. **This is the real unblock, and it is the owner's call** — it changes the money
   basis for the board, the switcher, the ticket, the panels and the gate, and it
   moves the board's published ranking. Not a lane decision.
2. **Prepared additively and reversibly:** book when present, floor when absent.
   A no-op on all committed history and on the daily vehicle.
3. **Do not take path (a)** (overwrite `ask_in`) — it destroys codified floor
   semantics.
4. **If declined, nothing is lost.** The gate keeps measuring the floor honestly;
   the vehicle work (Option A) remains available as durable evidence of the book
   regardless, and is worth having on its own terms.
5. **Whichever way it goes, the constants do not move.**

**Nothing here is applied. Owner's call is the gate.**
