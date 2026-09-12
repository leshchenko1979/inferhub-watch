"""Floor-ask owner (issue #27): the ONE ladder-point selection rule.

A route's **floor ask** is the cheapest price point in its catalog ladder
that at least one enabled publisher actually offers. The catalog's
``pricePointsIn``/``pricePointsOut`` are ``[price, provider_count]`` pairs;
a point with ``count == 0`` is an advertised-but-unfilled price and must
never be selected — publishing it as the floor advertises a price no
request can be served at.

Two callers used to disagree on exactly that, on the same pull:

- ``probe.pricing._cheapest_point`` required a truthy count (correct), and
  feeds ``data/pricing.json``;
- ``probe.catalog.fetch_models`` took a bare ``min()`` over every point
  with ``len(point) > 1`` (count ignored), and feeds ``route_metrics``
  plus the hourly auto-switcher.

Same route, same minute, two different floors — the "floor-point flip"
that helped drive the #27 thrash. Both now route through ``floor_point``
here, so a route's floor is a single deterministic function of its ladder.

Selection rule (pinned, #27 item 3)::

    floor = min(price) over points with count >= MIN_PUBLISHERS
    ties  = the larger publisher count wins, then the lower price

This module imports nothing from ``probe`` on purpose: it is a leaf, so
both the catalog fetcher and the pricing snapshot can depend on it without
an import cycle.
"""

from __future__ import annotations

MIN_PUBLISHERS = 1
"""A ladder point only counts as a floor when at least this many enabled
publishers offer it. ``count == 0`` points are advertised-but-unfilled."""


def _point(point: object) -> tuple[float, int] | None:
    """Parse one ``[price, count]`` ladder entry, or None when unusable."""
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    price, count = point[0], point[1]
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    if price < 0:
        return None
    if isinstance(count, bool):
        count = int(count)
    elif isinstance(count, (int, float)):
        count = int(count)
    else:
        count = 0
    return float(price), count


def ladder_points(points: object) -> list[tuple[float, int]]:
    """Every usable ``(price, count)`` point, price-ascending.

    Deterministic ordering is the point: a caller that logs the ladder or
    picks from it must see the same sequence for the same input.
    """
    parsed = [p for p in (_point(pt) for pt in points or []) if p is not None]
    parsed.sort(key=lambda pc: (pc[0], -pc[1]))
    return parsed


def floor_point(points: object) -> tuple[float | None, int | None]:
    """``(price, count)`` of the cheapest point with >= MIN_PUBLISHERS.

    Returns ``(None, None)`` when the ladder carries no filled point —
    the caller must treat that as "no floor known", never as zero.
    """
    best: tuple[float, int] | None = None
    for price, count in ladder_points(points):
        if count < MIN_PUBLISHERS:
            continue
        if best is None or price < best[0] or (price == best[0] and count > best[1]):
            best = (price, count)
    if best is None:
        return None, None
    return best


def describe_point(points: object) -> str:
    """One-line ladder receipt for logs: the chosen floor and its context.

    Example: ``floor=0.001350@3p (ladder 17 points, cheapest filled 0.001350@3p)``.
    """
    ladder = ladder_points(points)
    price, count = floor_point(points)
    if price is None:
        return f"floor=none (ladder {len(ladder)} points, none filled)"
    return f"floor={price:.6f}@{count}p (ladder {len(ladder)} points)"


def _legacy_price(legacy: object) -> float | None:
    """Cheapest entry of a legacy ``asksIn``/``asksOut`` array, or None."""
    try:
        price = min(legacy or [])
    except (TypeError, ValueError):
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    return float(price)

def floor_side_detail(model: dict, ladder_key: str, legacy_key: str) -> tuple[float | None, int | None]:
    """One side's floor as ``(price, publishers)`` from a SINGLE selection.

    The price and the publisher count are read off the same chosen point, so
    a caller can never publish a price from one ladder point alongside a
    publisher count from another — the divergence #27 was made of. The
    ladder wins when it carries a filled point; the legacy per-provider
    array is the fallback, and then the publisher count is honestly unknown
    (None) rather than guessed.
    """
    price, count = floor_point(model.get(ladder_key))
    if price is not None:
        return price, count
    legacy = _legacy_price(model.get(legacy_key))
    if legacy is None:
        return None, None
    return legacy, None

def floor_pair_detail(model: dict) -> tuple[tuple[float | None, int | None], tuple[float | None, int | None]]:
    """``((askIn, publishersIn), (askOut, publishersOut))`` for one route."""
    return (
        floor_side_detail(model, "pricePointsIn", "asksIn"),
        floor_side_detail(model, "pricePointsOut", "asksOut"),
    )

def floor_pair(model: dict) -> tuple[float, float] | None:
    """``(floor askIn, floor askOut)`` under either catalog schema.

    Legacy: per-provider ``asksIn``/``asksOut`` arrays (the /pricing page
    JSON). Current: ``pricePointsIn``/``pricePointsOut`` histograms — the
    floor is the cheapest *filled* point (see ``floor_point``). Returns
    None when either side has no filled point.
    """
    (cheap_in, _), (cheap_out, _) = floor_pair_detail(model)
    if cheap_in is None or cheap_out is None:
        return None
    return cheap_in, cheap_out
