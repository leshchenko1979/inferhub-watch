"""Book estimator (issue #41) — the price the BOOK clears at, beside the floor.

ONTOLOGY.md keeps ``floor`` codified, and ``probe/floor.py`` stays the single
owner of the ladder selection rule (issue #27). This module does NOT re-litigate
that rule: it computes a SECOND price off the same ladder, so a projection can
be priced on the stack instead of on one cheap offer.

WHY THIS EXISTS (owner ruling 2026-09-12, "we price the book"). The catalog
carries each route's whole order book as ``pricePointsIn`` /
``pricePointsOut`` — ``[price, provider_count]`` points, 1–34 per route (mean
10.7, median 7, over 113 routes). This repo used to reduce each ladder to its
floor and drop the rest, then price the forward view on that floor. Measured
2026-09-12 against 24h of billed rows, the floor is a minority position: on 82
of 113 ladder routes the mass point sits ABOVE the floor (none below it, 31
equal), and 48.0% of billed volume clears above the live floor. The floor is
what the cheapest single offer costs; the book is what the workload pays.

The estimator is computed from a ladder and nothing else — no clock, no I/O —
so it is pure and directly testable. It MUST be recomputed per fetch: two live
catalog pulls 26 s apart moved 35 of 115 ladders, so a book price is only as
good as the fetch time stamped beside it (see ``probe.catalog``'s
``ladder_at``). Never cache a book point across a cycle.

NOT REIMPLEMENTED HERE. The floor is read through ``probe.floor.floor_point``
so this module and the catalog fetcher can never disagree about where the
ladder's cheapest filled point is.
"""

from __future__ import annotations

from probe import floor

#: Which estimator ``book_point`` returns, and why.
#:
#: Across 113 ladder routes the mass point and the provider-weighted median are
#: the SAME number on 108 (95.6%) and differ on 5. Where they differ the
#: median is the conservative, robust choice: the disagreements include
#: ``cbcn/minimax-m3`` at mass/pmed = 14.71x, where the mass point IS a single
#: huge-count point that the median smooths over. The mass point is a mode of a
#: long-tailed distribution — one provider-count spike moves it arbitrarily far.
#: The median cannot be moved by any single point, so it is what we price on.
#:
#: This is a ROBUSTNESS argument, not an accuracy one: the two estimators are
#: within noise of each other on the accuracy comparison (volume-weighted mean
#: |log error| 1.461 for both). Do not re-open it as an accuracy question.
BOOK_ESTIMATOR = "provider_weighted_median"

#: Estimators that exist for measurement but must NEVER be selected.
#:
#: ``supply_weighted_mean`` was measured 80.3% WORSE than the floor at
#: predicting the billed ask (mean |log(est/billed)| 2.674 vs 1.547
#: volume-weighted), because a few huge-count points dominate it. It is kept
#: here so the measurement is recorded in code and nobody re-proposes it as a
#: candidate — see issue #41 item (d).
BANNED_ESTIMATORS = {"supply_weighted_mean"}


def _clean(points: object) -> list[tuple[float, int]]:
    """The ladder's FILLED points, price-ascending.

    ``floor.ladder_points`` parses every usable point but deliberately keeps
    zero-count ones — ``floor_point`` is what filters them, because a point with
    no publishers is advertised-but-unfilled and must never be selected (#27).
    The estimators here do the same filtering up front: a price nobody offers is
    not a book price, and letting one through would price a route at a rate no
    request can be served at.
    """
    return [pc for pc in floor.ladder_points(points) if pc[1] >= floor.MIN_PUBLISHERS]


def mass_point(points: object) -> float | None:
    """The price carrying the MOST providers — the book's modal point.

    Ties break to the LOWER price: two prices equally populated means the
    cheaper one is the one a buyer reaches first, and a deterministic tie rule
    keeps two pulls of the same book comparable. ``None`` when no filled point
    exists (never 0.0 — an absent book is not a free one).
    """
    clean = _clean(points)
    if not clean:
        return None
    best_price, best_count = clean[0]
    for price, count in clean[1:]:
        if count > best_count or (count == best_count and price < best_price):
            best_price, best_count = price, count
    return best_price


def provider_weighted_median(points: object) -> float | None:
    """The price at which cumulative provider count reaches half the book.

    Every provider is one vote, so this is the price the median provider
    offers — the point at which a buyer walking the book up from the floor has
    covered half the supply. Immune to any single point's count: no one
    provider-count spike can move it past its neighbours.
    """
    clean = _clean(points)
    if not clean:
        return None
    total = sum(count for _, count in clean)
    if total <= 0:
        return None
    seen = 0
    for price, count in clean:
        seen += count
        if seen * 2 >= total:
            return price
    return clean[-1][0]


def supply_weighted_mean(points: object) -> float | None:
    """Mean price weighted by provider count. BANNED — see BANNED_ESTIMATORS.

    Kept only so the 80.3%-worse measurement is reproducible from code.
    """
    clean = _clean(points)
    total = sum(count for _, count in clean)
    if not clean or total <= 0:
        return None
    return sum(price * count for price, count in clean) / total


def estimators(points: object) -> dict[str, float | None]:
    """Every estimator off one ladder, for publication and measurement.

    ``floor`` is the codified rule (``probe.floor``); the other three are this
    module's. Returning them together is what makes the mass-vs-median
    comparison in issue #41 reproducible from a single call.
    """
    floor_price, _ = floor.floor_point(points)
    return {
        "floor": floor_price,
        "mass": mass_point(points),
        "provider_weighted_median": provider_weighted_median(points),
        "supply_weighted_mean": supply_weighted_mean(points),
    }


def book_point(points: object) -> float | None:
    """The book price we price on — ``BOOK_ESTIMATOR`` over one ladder.

    ``None`` (never 0.0) when the ladder carries no filled point: a route with
    no book has no book price, and a caller must fall back explicitly rather
    than treat it as free.
    """
    if BOOK_ESTIMATOR in BANNED_ESTIMATORS:  # pragma: no cover — guard, not path
        raise AssertionError(f"BOOK_ESTIMATOR {BOOK_ESTIMATOR!r} is banned")
    return estimators(points)[BOOK_ESTIMATOR]


def _count_at(points: list[tuple[float, int]], price: float) -> int:
    """Providers offering ``price`` in a cleaned ladder (0 when absent)."""
    return sum(count for point_price, count in points if point_price == price)


def describe(points: object) -> str:
    """One-line receipt for logs: the book point and the floor it sits above.

    Example: ``book=0.005850@378p (floor 0.000150@6p, 39.0x, 19 points)``.
    """
    clean = _clean(points)
    book = book_point(points)
    floor_price, _ = floor.floor_point(points)
    if book is None:
        return f"book=none (ladder {len(clean)} points, no filled point)"
    ratio = f"{book / floor_price:.1f}x" if floor_price else "n/a"
    book_count = _count_at(clean, book)
    floor_count = _count_at(clean, floor_price) if floor_price else 0
    return (f"book={book:.6f}@{book_count}p (floor {floor_price:.6f}@{floor_count}p, "
            f"{ratio}, {len(clean)} points)")
