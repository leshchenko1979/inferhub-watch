"""Site generator — composition root (P2b).

generate.py used to be a 1,670-line god-module; the P2b carve split it into
section modules, each owning one reader-facing surface:

- chrome.py    page shell, nav, shared table-cell builders
- spend.py     spend dashboard (sparkline, MTD/today/probe, ask deltas)
- ticket.py    verdict ticket (dispatch recommendation)
- board.py     cost board + scatter chart
- results.py   merged probe-results table (incumbents + audition routes)

This file is the thin composition root: loads data, calls the sections in
reading order, writes site/dist. It also re-exports the module surface the
render-pinning tests exercise (they exec this file by path and reach the
sections through it) — the test contract survives the carve unchanged.

The render-pinning tests exec this module by path and patch
`gen.rundata.*`; section modules read data through the same rundata module
object, so a patch on it is seen by every section.
"""
from __future__ import annotations

import html
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SITE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if str(_SITE) not in sys.path:
    sys.path.insert(0, str(_SITE))

import mdhtml  # noqa: E402
import tmpl  # noqa: E402
import freshness as fresh  # noqa: E402
import rundata  # noqa: E402

import board as board_mod  # noqa: E402
import chrome as chrome_mod  # noqa: E402
import results as results_mod  # noqa: E402
import spend as spend_mod  # noqa: E402
import ticket as ticket_mod  # noqa: E402
from probe.registry import load_registry  # noqa: E402
from probe.registry import load_aliases as _load_aliases  # noqa: E402

# ── test-facade re-exports (the render-pinning tests' module surface) ──
base_href = chrome_mod.base_href
results_available = chrome_mod.results_available
run_groups = rundata.run_groups
board_nav = chrome_mod.board_nav
section_title = chrome_mod.section_title
shell = chrome_mod.shell
_viz_cell = chrome_mod._viz_cell
spend_sparkline = spend_mod.spend_sparkline
spend_block = spend_mod.spend_block
ask_delta_bits = spend_mod.ask_delta_bits
_ask_spark = spend_mod._ask_spark
verdict_section = ticket_mod.verdict_section
pricing_section = board_mod.pricing_section
scatter_section = board_mod.scatter_section
usage_color = board_mod.usage_color
usage_radius = board_mod.usage_radius
_iq_cells = board_mod._iq_cells
_pair_cell = board_mod._pair_cell
_probe_only = board_mod._probe_only
probe_results_section = results_mod.probe_results_section
_price_chip_html = results_mod._price_chip_html

_html_escape = html.escape


def load_runs() -> list[dict]:
    return rundata.load_runs(ROOT)


def load_aliases() -> list[str]:
    return _load_aliases()


def check_page(spec: dict) -> str:
    md = (ROOT / "checks" / spec["id"] / "page.md").read_text()
    body = tmpl.render("check.html", article=mdhtml.md_to_html(md))
    return chrome_mod.shell(
        spec["title"], body, crumb=spec["title"], nested=True, page_class="brief"
    )


def index_html(runs: list[dict], aliases: list[str], registry: list[dict]) -> str:
    if not runs:
        return chrome_mod.shell("InferHub Watch", tmpl.render("empty.html"))

    latest = runs[-1]

    explainers = []
    for spec in registry:
        brief = mdhtml.check_brief_html(ROOT, spec)
        explainers.append(
            f'<details id="check-{_html_escape(spec["id"])}">'
            f"<summary>{_html_escape(spec['title'])}</summary>"
            f'<div class="check-brief">{brief}</div></details>'
        )

    started_raw = (latest.get("started_at") or "")[:19]
    started = _html_escape(started_raw.replace("T", " ") + " UTC")
    run_cost = rundata.run_total_cost(latest)
    cost_bit = f' · run cost <span class="run-cost">{run_cost}</span>' if run_cost else ""
    payload = rundata.load_pricing(ROOT)
    header_meta = (
        f'<p class="probe-meta">{fresh.chip_html(payload)} · Last probe: '
        f'<time datetime="{_html_escape(started_raw)}">{started}</time>'
        f"{cost_bit}</p>"
    )
    body = tmpl.render(
        "board.html",
        method_title=chrome_mod.section_title("method"),
        scatter_section=scatter_section(payload, rundata.load_intelligence(ROOT), runs),
        verdict_section=verdict_section(payload),
        pricing_section=pricing_section(payload, runs),
        probe_results_section=probe_results_section(
            runs, aliases, registry, payload
        ),
        explainers="".join(explainers),
        github=chrome_mod.GITHUB,
        clone=chrome_mod.CLONE,
    )
    return chrome_mod.shell(
        "InferHub Watch",
        body,
        page_class="board",
        with_footer=False,
        page_nav=chrome_mod.board_nav(runs),
        header_meta=header_meta,
    )


def main() -> int:
    dist = ROOT / "site" / "dist"
    aliases = load_aliases()
    registry = load_registry()
    runs = load_runs()
    # P0b freshness gate: warn always, fail the pages job when the data the
    # site is about to publish is stale beyond STALE_HOURS (env-gated so the
    # validate job and local builds still succeed on old fixtures). The gate
    # runs BEFORE any dist work — a red gate must not publish anything.
    payload = rundata.load_pricing(ROOT)
    fail_stale = os.environ.get("FRESHNESS_FAIL", "") == "1"
    rc = fresh.gate(payload, fail_stale=fail_stale)
    if rc:
        return rc
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    shutil.copy(ROOT / "site" / "style.css", dist / "style.css")
    (dist / "index.html").write_text(index_html(runs, aliases, registry))
    checks_dir = dist / "checks"
    checks_dir.mkdir()
    for spec in registry:
        (checks_dir / f"{spec['id']}.html").write_text(check_page(spec))
    print(dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
