from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from probe.registry import load_registry, repo_root

sys.path.insert(0, str(repo_root() / "site"))

import rundata


def _load_generate():
    path = repo_root() / "site" / "generate.py"
    spec = importlib.util.spec_from_file_location("watch_generate", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WiringTests(unittest.TestCase):
    def test_every_registered_check_has_page_runner_and_fixtures(self) -> None:
        root = repo_root()
        for spec in load_registry():
            folder = root / "checks" / spec["id"]
            self.assertTrue((folder / "check.py").is_file(), spec["id"])
            self.assertTrue((folder / "page.md").is_file(), spec["id"])
            self.assertTrue((folder / "test.py").is_file(), spec["id"])
            self.assertIn("run", (folder / "check.py").read_text())

    def test_verdict_helpers_do_not_import_opencrabs(self) -> None:
        text = (repo_root() / "probe" / "sse.py").read_text()
        self.assertNotIn("is_some", text)
        self.assertNotIn("opencrabs", text.lower())

    def test_generate_includes_page_md_and_no_secrets(self) -> None:
        gen = _load_generate()
        spec = next(s for s in load_registry() if s["id"] == "core")
        html = gen.check_page(spec)
        self.assertIn("stream: true", html)
        self.assertIn("report_answer", html)
        self.assertLessEqual(html.lower().count("is_some"), 1)
        self.assertNotIn(os.environ.get("INFERHUB_API_KEY") or "sk-never", html)
        self.assertNotIn("OpenCrabs", html)
        self.assertIn("platform.openai.com", html)
        self.assertNotIn('class="site-nav"', html)

    def test_homepage_puts_github_in_footer_not_header(self) -> None:
        gen = _load_generate()
        html = gen.index_html(gen.load_runs(), gen.load_aliases(), gen.load_registry())
        header, _, rest = html.partition("</header>")
        self.assertNotIn("github.com/leshchenko1979/inferhub-watch", header)
        self.assertNotIn("dispatch-meta", header)
        self.assertIn('class="probe-meta"', header)
        self.assertIn("Last probe:", header)
        self.assertIn('class="site-nav"', header)
        self.assertIn('href="#results"', header)
        self.assertIn('href="#method"', header)
        self.assertNotIn('href="#report"', header)
        self.assertNotIn('href="#today"', header)
        self.assertNotIn('href="#notes"', header)
        # explanatory prose moved to the docs page (owner 2026-09-08):
        # the board carries only the reading-guide entry points.
        self.assertIn('href="/inferhub-watch/docs.html"', html)
        self.assertNotIn("Ask for another", html)
        self.assertNotIn("/issues/new", html)
        self.assertNotIn("models.toml", html)
        self.assertIn("models.toml", gen.docs_html())
        self.assertIn("github.com/leshchenko1979/inferhub-watch", rest)
        self.assertIn("board-footer", rest)
        self.assertIn("<footer", rest)
        self.assertIn("INFERHUB_API_KEY", rest)
        self.assertNotIn(os.environ.get("INFERHUB_API_KEY") or "sk-never", html)
        # the score matrix is retired; collapsible probe-results groups rule
        self.assertNotIn('class="matrix"', html)
        self.assertNotIn('class="rank-table"', html)
        self.assertNotIn("check-col", html)
        self.assertNotIn('href="#check-core"', html)
        # check explainers moved to the docs page
        docs = gen.docs_html()
        self.assertIn('id="check-core"', docs)
        self.assertIn("<details", docs)
        self.assertIn("<summary>", docs)
        self.assertIn("report_answer", docs)
        self.assertNotIn('class="explainers"', html)
        self.assertIn('title="Prompt-cache share', html)
        self.assertNotIn("Safe to use", html)
        self.assertNotIn("Latest results", html)
        self.assertNotIn("No alias is safe to use this run.", html)
        self.assertIn("platform.openai.com", docs)
        self.assertNotIn("platform.openai.com", html)
        thead, _, _after_head = html.partition("</thead>")
        self.assertNotIn("checks/core.html", thead)
        self.assertNotIn("checks/core.html", rest[rest.find('id="method"') :])
        self.assertNotIn("OpenCrabs", html)
        self.assertNotIn("Seven-day", html)
        self.assertNotIn("Who should care", html)
        self.assertIn("<h2>Probe results</h2>", html)
        self.assertIn("<h2>Cost per M tokens</h2>", html)
        self.assertNotIn("<h2>Past runs</h2>", html)
        self.assertIn("<h2>How we test</h2>", html)
        self.assertNotIn("<h2>This probe</h2>", html)
        self.assertNotIn("<h2>Last report</h2>", html)
        self.assertNotIn("<h2>Today</h2>", html)
        self.assertNotIn("<h2>Explanations</h2>", html)
        self.assertNotIn("<h2>History</h2>", html)
        self.assertNotIn("<h2>Method</h2>", html)
        self.assertNotIn("Failed scoring checks", html)
        self.assertNotIn("<h2>Mornings</h2>", html)
        self.assertNotIn("<th>Resolved</th>", html)
        # route cells render as <code> route names with the publisher/ask
        # line underneath (alias-cell th retired with the results-table era)
        self.assertIn('scope="row"><code>', html)
        self.assertIn('class="route-ask"', html)
        self.assertIn('class="explainers"', gen.docs_html())
        self.assertNotIn('class="explainers"', html)
        self.assertNotIn('class="notes"', html)
        self.assertNotIn('class="about"', html)
        self.assertNotIn('class="hero"', html)
        self.assertIn('class="docs"', gen.docs_html())
        self.assertNotIn("The endpoint", html)
        self.assertIn("The endpoint", gen.docs_html())
        self.assertNotIn("What we probe", html)
        # publisher labels render — dated-only board: cp/cline-pass left the
        # board, z.ai carries the check now
        self.assertIn("z.ai · zai/glm-5.3-flash", html)
        # family chip carries "alias · ok/total"; check names live on the
        # drill links and the check pages now
        self.assertIn(" · 2/2", html)
        self.assertIn("chip ok", html)
        # the decision surface (board) leads; probe depth and history follow
        self.assertLess(html.find('id="pricing"'), html.find('id="results"'))
        self.assertLess(html.find('id="pricing"'), html.find('id="method"'))
        # chart sits just below the hero verdict (owner 2026-09-07)
        self.assertLess(html.find('class="scatter"'),
                        html.find('id="pricing"'))
        results = rest[rest.find('id="results"') : rest.find('id="method"')]
        self.assertNotIn("Actions", results)
        groups = gen.run_groups(gen.load_runs()[-1])
        aliases = gen.load_aliases()
        first_group = next(
            g for g in groups if rundata.incumbent_aliases(aliases, g["model"])
        )
        expected_first = rundata.incumbent_aliases(aliases, first_group["model"])[0]
        self.assertIn(f"<code>{expected_first}</code>", results)
        self.assertIn('class="fam-head"', results)
        self.assertIn('class="chip', results)
        # in-use marker renders on every ranked route; the exact variant is
        # data-driven (full pill at >= IN_USE_MIN_REQS billed reqs in the
        # newest 24h, muted 'light use' below it), so assert the family.
        self.assertIn('class="pill in-use', results)
        self.assertIn('data-label="tests"', results)
        self.assertNotIn("info · not ranked", html)
        self.assertNotIn('class="timeline"', html)
        self.assertIn('<details class="nav-menu">', html)
        self.assertIn("aria-expanded", html)
        self.assertIn("On this page", html)
        self.assertNotIn('id="nav-toggle"', html)
        self.assertNotIn("Clone and run", html)
        self.assertNotIn("Run it yourself", html)

    def test_unprobed_runs_render_blank_not_red(self) -> None:
        gen = _load_generate()
        registry = gen.load_registry()
        scoring = gen.rundata.scoring_ids(registry)
        newer = {
            "started_at": "2026-08-27T21:00:00",
            "origin": "local",
            "cells": [
                {
                    "alias": "new/a",
                    "check_id": cid,
                    "status": "pass",
                    "summary": "ok",
                    "resolved_model": "new/a",
                }
                for cid in scoring
            ],
        }
        older = {
            "started_at": "2026-08-27T06:00:00",
            "origin": "Actions · CI",
            "cells": [],
        }
        with mock.patch.object(gen.rundata, "load_pricing", return_value=None):
            page = gen.index_html([older, newer], ["new/a"], registry)
        # history grid is gone; what matters is the page still renders and
        # the older unprobed run contributes no red cells anywhere
        self.assertIn("<h2>Probe results</h2>", page)
        self.assertNotIn('class="bad"', page)

    def test_candidate_cells_do_not_render_on_board(self) -> None:
        gen = _load_generate()
        registry = gen.load_registry()
        scoring = gen.rundata.scoring_ids(registry)
        run = {
            "started_at": "2026-08-27T22:00:00",
            "origin": "local",
            "cells": [
                {
                    "alias": "ocg/deepseek-v4-flash",
                    "check_id": cid,
                    "status": "pass",
                    "summary": "ok",
                    "resolved_model": "ocg/deepseek-v4-flash",
                }
                for cid in scoring
            ]
            + [
                {
                    "alias": "cb/gpt-5.6-luna",
                    "check_id": cid,
                    "status": "pass",
                    "summary": "ok",
                    "candidate": True,
                    "model": "gpt-5.6-luna",
                }
                for cid in scoring
            ],
        }
        with mock.patch.object(gen.rundata, "load_pricing", return_value=None):
            page = gen.index_html([run], ["ocg/deepseek-v4-flash"], registry)
        # candidate cells render only in the results section, never on the board
        board = page[: page.find('id="results"')]
        self.assertNotIn("cb/gpt-5.6-luna", board)
        results = page[page.find('id="results"') :]
        self.assertIn("cb/gpt-5.6-luna", results)
        self.assertIn("ocg/deepseek-v4-flash", page)

    def test_board_alias_still_in_old_shortlist_renders_once(self) -> None:
        # dated-only transition: a route that just joined the board can still
        # sit in the previous run's candidate list; the board row wins and
        # the duplicate candidate row is dropped.
        gen = _load_generate()
        registry = gen.load_registry()
        scoring = gen.rundata.scoring_ids(registry)
        run = {
            "started_at": "2026-08-28T21:20:29",
            "origin": "Actions · CI",
            "cells": [
                {
                    "alias": "ali/deepseek-v4-pro-0813",
                    "check_id": cid,
                    "status": "fail",
                    "summary": "x",
                    "candidate": True,
                    "model": "deepseek-v4-pro",
                }
                for cid in scoring
            ],
            "candidates": ["ali/deepseek-v4-pro-0813"],
        }
        with mock.patch.object(gen.rundata, "load_pricing", return_value=None):
            page = gen.index_html([run], ["ali/deepseek-v4-pro-0813"], registry)
        results = page[page.find('id="results"') : page.find('id="method"')]
        self.assertEqual(results.count("<code>ali/deepseek-v4-pro-0813</code>"), 1)
        # pill derives from 24h usage now — fixture has no perf block, so the
        # freshly-boarded route reads "light use" (0 reqs/24h)
        self.assertIn('class="pill in-use--light"', results)
        # the board row carries the audition probe evidence, not "not probed"
        self.assertIn("tests-bad", results)
        self.assertIn("missed: core, cache", results)
        self.assertNotIn("Not probed in the latest run", results)
        # the incumbent summary chip agrees (probed via the audition cells),
        # so it is scored, not the gray "unprobed" dim
        self.assertNotIn("chip dim", results)

    def test_tooltip_engine_covers_every_data_tip_cell(self) -> None:
        # the board.js tip engine binds EVERY [data-tip] element (hover on
        # pointer devices, tap-toggle on touch, swipe-safe), and style.css
        # outlines any focused/open tip cell
        js = (repo_root() / "site" / "templates" / "board.js").read_text()
        css = (repo_root() / "site" / "style.css").read_text()
        self.assertIn('querySelectorAll("[data-tip]")', js)
        self.assertIn("pointerup", js)  # touch tap-toggle
        self.assertIn("pointerdown", js)  # swipe-vs-tap discrimination
        self.assertIn("td[data-tip]:focus-visible", css)
        self.assertIn("td.tip-open", css)

    def test_viz_bar_fill_aligns_with_cell_content(self) -> None:
        # numeric cells right-align their content at every width, so the
        # colored fill hugs the right edge of its track on desktop AND in
        # the mobile fold — no mobile override may left-reset the bar
        css = (repo_root() / "site" / "style.css").read_text()
        fill = css[css.index(".viz-bar i {") :]
        fill = fill[: fill.index("}")]
        self.assertIn("margin-left: auto", fill)
        mobile = css[css.index("@media (max-width: 720px)") :]
        self.assertNotIn(".pricing .viz-bar", mobile)


class PricingSectionTests(unittest.TestCase):
    PAYLOAD: ClassVar[dict] = {
        "generated_at": "2026-08-27T20:00:00+00:00",
        "range": "30d",
        "requests_scanned": 5700,
        "perf": {"window_hours": 24,
                 "models": {"ali/qwen3.8-max": {"reqs": 8908}}},
        "routes": {
            "ali/qwen3.8-max": {
                "ask_in": 0.014,
                "ask_out": 0.042,
                "eff_per_mtok": 0.0201,
                "cache_pct": 61.4,
                "reqs": 12,
                "tok_in": 90000,
                "tok_out": 3000,
                "cost_usdc": "0.001870",
                "last_ts": "2026-08-27T19:00:00",
                "source": "usage-logs",
            },
            "nous/deepseek-v4-flash": {
                "ask_in": 0.011,
                "ask_out": 0.032,
                "eff_per_mtok": None,
                "cache_pct": None,
                "reqs": 0,
                "tok_in": 0,
                "tok_out": 0,
                "cost_usdc": None,
                "last_ts": None,
                "source": "catalog",
            },
            "ghost/route": {
                "ask_in": None,
                "ask_out": None,
                "eff_per_mtok": None,
                "cache_pct": None,
                "reqs": 0,
                "tok_in": 0,
                "tok_out": 0,
                "cost_usdc": None,
                "last_ts": None,
                "source": "none",
            },
        },
    }

    def test_absent_pricing_omits_section_and_nav(self) -> None:
        gen = _load_generate()
        with mock.patch.object(gen.rundata, "load_pricing", return_value=None):
            page = gen.index_html(
                gen.load_runs(), gen.load_aliases(), gen.load_registry()
            )
            nav = gen.board_nav()
        self.assertNotIn('id="pricing"', page)
        self.assertNotIn('href="#pricing"', page)
        self.assertNotIn('href="#pricing"', nav)
        self.assertIn('href="#method"', nav)

    def test_present_pricing_renders_before_history(self) -> None:
        gen = _load_generate()
        with mock.patch.object(
            gen.rundata, "load_pricing", return_value=self.PAYLOAD
        ):
            page = gen.index_html(
                gen.load_runs(), gen.load_aliases(), gen.load_registry()
            )
            nav = gen.board_nav()
        self.assertIn('id="pricing"', page)
        self.assertIn('href="#pricing"', nav)
        # the board now leads the probe-results depth section
        self.assertLess(page.find('id="pricing"'), page.find('id="results"'))
        self.assertLess(page.find('id="pricing"'), page.find('id="method"'))
        self.assertIn("ali/qwen3.8-max", page)
        self.assertIn("$0.0140", page)
        self.assertIn("$0.0420", page)
        self.assertIn("$0.0201", page)
        self.assertIn("61%", page)
        self.assertIn("$0.0019", page)
        self.assertIn("12 req · 93k tok", page)
        self.assertIn("30d window · 5700 billed requests", page)
        # routes with no rate data at all are filtered out
        self.assertNotIn("ghost/route", page)
        # catalog-fallback route is marked with the list-price asterisk
        self.assertIn("nous/deepseek-v4-flash", page)
        self.assertIn("ask-mark", page)
        # ask rates fold under the route; the rest render as value + bar
        self.assertIn("ask $0.0140 / $0.0420 per M", page)
        self.assertNotIn("ask in $/M", page)
        self.assertIn('class="viz-bar"', page)
        self.assertIn('class="route-ask"', page)
        # every decision cell carries a fold label for the mobile card view;
        # plumbing (cache/traffic/cost/failures/source) folds under each route
        self.assertIn('data-label="effective $/M"', page)
        # rate cells pair the ranking basis (projection, crown gate passed on
        # this history) with the realized 30d as the dimmed alternate
        self.assertIn('class="pair-main"', page)
        self.assertIn('class="pair-alt"', page)
        self.assertIn("crown gate", page)
        self.assertIn("transitions crowned within", page)
        self.assertIn('data-label="cache hit"', page)  # candidates table
        # plumbing: 'more' sits in its own route-row cell (owner 2026-09-07
        # 21:xxZ — never shares the iq/$ line); on open the word flips to
        # 'less' (no + glyph) and the grid reveals as a SEPARATE full-width
        # sibling tr, via CSS :has() — zero JS
        self.assertIn("Show plumbing", page)
        self.assertIn('<td class="plumb-cell"><details class="plumb">', page)
        self.assertIn('<span class="plumb-word">more</span>', page)
        self.assertNotIn("plumb-mark", page)
        self.assertIn('<tr class="plumb-row">', page)
        # every pricing metric carries a native tooltip; failures wrap
        self.assertIn('class="plumb-fail"', page)
        self.assertIn("<dt>30d traffic</dt>", page)
        self.assertIn("<dt>30d cost</dt>", page)
        self.assertIn("<dt>ttft p50</dt>", page)
        self.assertIn("<dt>tps mean</dt>", page)
        self.assertIn("<dt>failures</dt>", page)
        self.assertIn("<dt>ask source</dt>", page)
        # cost cells explain themselves on hover and tap
        self.assertIn("Billed cost per M tokens over all traffic", page)
        self.assertIn("vs the previous daily snapshot", page)

    def test_mobile_fold_css_covers_pricing_but_not_timeline(self) -> None:
        css = (repo_root() / "site" / "style.css").read_text()
        # slice the mobile media block that holds the pricing fold (line
        # ~1044; the scatter's own 720px block lives later for the mobile
        # SVG, and a smaller 720px timeline block sits just before it)
        start = css.rindex("@media (max-width: 720px)", 0, css.index(".pricing tbody,"))
        end = css.index("@media (min-width: 721px)", start)
        mobile = css[start:end]
        # cost + candidates tables fold like the matrix
        self.assertIn(".pricing tbody,", mobile)
        self.assertIn(".pricing td[data-label]::before", mobile)
        # folded cards lay their fields out in two columns: candidates pair
        # tests + cache hit in the left column, ask + window in the right,
        # placed by explicit order so DOM order stays semantic
        self.assertIn(".pricing tr {", mobile)
        self.assertIn("grid-template-columns: 1fr 1fr;", mobile)
        self.assertIn(".candidates tr > td:nth-of-type(2) { order: 3; }", mobile)
        self.assertIn(".candidates tr > td:nth-of-type(3) { order: 2; }", mobile)
        # an odd fifth cell (the cost card's billed total) spans both columns
        self.assertIn(".pricing tr > td:nth-of-type(5) {", mobile)
        self.assertIn("grid-column: 1 / -1;", mobile)
        # column-flow auto-placement mis-placed cells beside the spanning name
        self.assertNotIn("grid-auto-flow", mobile)
        # past runs keep the table layout (no .timeline fold in this block)
        self.assertNotIn(".timeline tbody,", mobile)


class ProbeOnlyMarginalTests(unittest.TestCase):
    """Marginal $/M dims to probe-only when every fresh request for the
    route falls inside a sweep window that probed it — real money,
    unrepresentative workload."""

    MARGINAL: ClassVar[dict] = {
        "marginal_per_mtok": 0.006,
        "marginal_reqs": 2,
        "marginal_since": "2026-09-03T10:50:43+00:00",
        "marginal_ts": ["2026-09-03T10:45:50Z", "2026-09-04T10:41:00Z"],
        "marginal_ts_truncated": False,
    }
    RUNS: ClassVar[list] = [
        {"started_at": "2026-09-03T10:45:00Z", "finished_at": "2026-09-03T10:46:00Z",
         "aliases": ["ali/qwen3.8-max"]},
        {"started_at": "2026-09-04T10:40:54Z", "finished_at": "2026-09-04T10:41:30Z",
         "aliases": ["ali/qwen3.8-max"]},
    ]

    def _payload(self, marginal: dict) -> dict:
        entry = dict(PricingSectionTests.PAYLOAD["routes"]["ali/qwen3.8-max"])
        entry.update(marginal)
        payload = json.loads(json.dumps(PricingSectionTests.PAYLOAD))
        payload["routes"]["ali/qwen3.8-max"] = entry
        return payload

    def _page(self, payload: dict, runs: list) -> str:
        gen = _load_generate()
        with mock.patch.object(gen.rundata, "load_pricing", return_value=payload):
            return gen.index_html(runs, gen.load_aliases(), gen.load_registry())

    def test_probe_only_traffic_dims_marginal_cell(self) -> None:
        page = self._page(self._payload(self.MARGINAL), self.RUNS)
        self.assertIn("<dt>marginal $/M", page)
        self.assertIn('class="dim"', page)
        self.assertIn("probe-only traffic", page)

    def test_organic_traffic_keeps_marginal_undimmed(self) -> None:
        marginal = dict(self.MARGINAL)
        marginal["marginal_ts"] = [
            "2026-09-03T10:45:50Z", "2026-09-04T18:00:00Z",  # 2nd outside windows
        ]
        page = self._page(self._payload(marginal), self.RUNS)
        self.assertIn("<dt>marginal $/M", page)
        self.assertNotIn('class="dim"', page)
        self.assertNotIn("probe-only traffic", page)

    def test_marginal_tooltip_sits_on_value(self) -> None:
        # Owner 2026-09-08: the tiny ℹ beside the label was too small a
        # tap target — the tooltip must also wrap the VALUE itself.
        page = self._page(self._payload(self.MARGINAL), self.RUNS)
        self.assertRegex(
            page, r'<dd><span data-tip="Billed cost per M over requests[^"]*">'
        )

    def test_probe_only_helper_guards(self) -> None:
        gen = _load_generate()
        row = {"route": "ali/qwen3.8-max", **self.MARGINAL}
        self.assertTrue(gen._probe_only(row, self.RUNS))
        # truncated ts list: cannot prove probe-only
        trunc = dict(row, marginal_ts_truncated=True)
        self.assertFalse(gen._probe_only(trunc, self.RUNS))
        # a run window missing -> nothing provable
        self.assertFalse(gen._probe_only(row, []))
        # window that did not probe this route
        runs_other = [dict(self.RUNS[0], aliases=["zai/glm-5.3-flash"])]
        self.assertFalse(gen._probe_only(row, runs_other))

    def test_probe_only_compares_datetimes_not_strings(self) -> None:
        """A1 (review 2026-09-04): usage-log stamps end in Z, run-window
        stamps end in +00:00. Lexically Z sorts above '+', so string
        comparison misjudged a request inside a window as outside."""
        gen = _load_generate()
        row = {
            "route": "ali/qwen3.8-max",
            "marginal_reqs": 1,
            "marginal_ts": ["2026-09-03T10:45:30Z"],  # Z-style usage stamp
        }
        runs = [  # +00:00-style run-window stamps, same moment
            {"started_at": "2026-09-03T10:45:00+00:00",
             "finished_at": "2026-09-03T10:46:00+00:00",
             "aliases": ["ali/qwen3.8-max"]},
        ]
        self.assertTrue(gen._probe_only(row, runs))
        # one second outside the window must stay organic
        row_late = dict(row, marginal_ts=["2026-09-03T10:46:01Z"])
        self.assertFalse(gen._probe_only(row_late, runs))


def _load_run():
    import probe.run as run_mod

    return run_mod


class BalanceAbortTests(unittest.TestCase):
    def test_detector_matches_balance_markers_only(self) -> None:
        run_mod = _load_run()
        self.assertTrue(
            run_mod.balance_too_low(
                'HTTP 402: {"error":{"message":"balance too low",'
                '"type":"insufficient_balance"}}'
            )
        )
        self.assertTrue(run_mod.balance_too_low("gateway says insufficient_balance"))
        self.assertFalse(run_mod.balance_too_low("HTTP 500: upstream timeout"))

    def test_collect_cells_raises_on_balance_cell(self) -> None:
        run_mod = _load_run()
        cell = {
            "check_id": "core",
            "alias": "x",
            "status": "error",
            "summary": 'HTTP 402: {"error":{"message":"balance too low"}}',
        }
        stub = types.SimpleNamespace(run=lambda client, alias: dict(cell))
        with mock.patch.object(run_mod, "load_check_module", return_value=stub), \
                self.assertRaises(run_mod.BalanceTooLow):
                run_mod.collect_cells(object(), ["a"], [{"id": "core"}])

    def test_collect_cells_raises_on_balance_exception(self) -> None:
        run_mod = _load_run()

        def boom(client, alias):
            raise RuntimeError("insufficient_balance for key")

        stub = types.SimpleNamespace(run=boom)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub), \
                self.assertRaises(run_mod.BalanceTooLow):
                run_mod.collect_cells(object(), ["a"], [{"id": "core"}])

    def test_collect_cells_tags_model_and_fail_fast_skip(self) -> None:
        run_mod = _load_run()
        stub = types.SimpleNamespace(
            run=lambda client, alias: {
                "check_id": "core",
                "alias": alias,
                "status": "fail",
                "summary": "bad stream",
            }
        )
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            cells, errors = run_mod.collect_cells(
                object(),
                ["cmc/deepseek/deepseek-v4-pro"],
                [{"id": "core"}, {"id": "cache"}],
            )
        self.assertEqual(errors, [])
        self.assertEqual(
            [c["model"] for c in cells], ["deepseek-v4-pro", "deepseek-v4-pro"]
        )
        self.assertEqual(cells[1]["status"], "skipped")

    def test_main_aborts_without_writing_a_run(self) -> None:
        run_mod = _load_run()
        cell = {
            "check_id": "core",
            "alias": "x",
            "status": "error",
            "summary": 'HTTP 402: {"error":{"message":"balance too low"}}',
        }
        stub = types.SimpleNamespace(run=lambda client, alias: dict(cell))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                run_mod, "load_check_module", return_value=stub
            ), mock.patch.object(
                run_mod, "load_aliases", return_value=["x"]
            ), mock.patch.object(
                run_mod, "load_registry", return_value=[{"id": "core"}]
            ), mock.patch.object(
                run_mod, "InferHubClient", return_value=object()
            ), mock.patch.object(
                run_mod, "repo_root", return_value=Path(tmp)
            ), mock.patch.object(
                run_mod.market, "fetch_catalog", return_value={}
            ), mock.patch(
                "probe.costs.fetch_log_rows", return_value=[]
            ), mock.patch.dict(
                os.environ, {"INFERHUB_API_KEY": "test-key"}
            ):
                code = run_mod.main()
            runs = list((Path(tmp) / "data" / "runs").glob("*.json")) if (
                Path(tmp) / "data" / "runs"
            ).exists() else []
        self.assertEqual(code, 3)
        self.assertEqual(runs, [])

    def test_main_writes_run_when_no_balance_error(self) -> None:
        run_mod = _load_run()
        cell = {
            "check_id": "core",
            "alias": "x",
            "status": "pass",
            "summary": "Named tools: get_weather.",
        }
        stub = types.SimpleNamespace(run=lambda client, alias: dict(cell))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                run_mod, "load_check_module", return_value=stub
            ), mock.patch.object(
                run_mod, "load_aliases", return_value=["x"]
            ), mock.patch.object(
                run_mod, "load_registry", return_value=[{"id": "core"}]
            ), mock.patch.object(
                run_mod, "InferHubClient", return_value=object()
            ), mock.patch.object(
                run_mod, "repo_root", return_value=Path(tmp)
            ), mock.patch.object(
                run_mod.market, "fetch_catalog", return_value={}
            ), mock.patch(
                "probe.costs.fetch_log_rows", return_value=[]
            ), mock.patch.dict(
                os.environ, {"INFERHUB_API_KEY": "test-key"}
            ):
                code = run_mod.main()
            runs = sorted((Path(tmp) / "data" / "runs").glob("*.json"))
            payload = json.loads(runs[0].read_text()) if len(runs) == 1 else {}
        self.assertEqual(code, 0)
        self.assertEqual(len(runs), 1)
        self.assertEqual(payload["cells"][0]["status"], "pass")
        self.assertEqual(payload["cost"]["source"], "usage-logs")
        self.assertEqual(payload["cost"]["matched"], 0)


class SpendDashboardTests(unittest.TestCase):
    PAYLOAD: ClassVar[dict] = {
        "generated_at": "2026-08-27T20:00:00+00:00",
        "range": "30d",
        "requests_scanned": 100,
        "days": [
            {"date": "2026-07-31", "cost_usdc": "5.000000", "requests": 9},
            {"date": "2026-08-01", "cost_usdc": "1.500000", "requests": 20},
            {"date": "2026-08-27", "cost_usdc": "0.250000", "requests": 30},
        ],
        "routes": {
            "ali/qwen3.8-max": {
                "ask_in": 0.014,
                "ask_out": 0.042,
                "eff_per_mtok": 0.0201,
                "cache_pct": 61.4,
                "reqs": 12,
                "tok_in": 90000,
                "tok_out": 3000,
                "cost_usdc": "0.001870",
                "last_ts": "2026-08-27T19:00:00",
                "source": "usage-logs",
            },
        },
    }
    PRIOR: ClassVar[dict] = {
        "generated_at": "2026-08-26T06:00:00+00:00",
        "routes": {
            "ali/qwen3.8-max": {"ask_in": 0.020, "ask_out": 0.042},
        },
    }

    def _page(self, payload: dict, dated: list) -> str:
        gen = _load_generate()
        with mock.patch.object(gen.rundata, "load_pricing", return_value=payload), \
                mock.patch.object(gen.rundata, "load_dated_pricing", return_value=dated), \
                mock.patch.object(gen.rundata, "prior_pricing",
                                  wraps=rundata.prior_pricing):
            return gen.index_html(
                gen.load_runs(), gen.load_aliases(), gen.load_registry()
            )

    def test_spend_block_mtd_today_and_sparkline(self) -> None:
        page = self._page(self.PAYLOAD, [("2026-08-27", self.PAYLOAD)])
        self.assertIn('class="spend-block"', page)
        self.assertIn("$1.7500", page)          # MTD: Aug 01 + Aug 27 only
        self.assertIn("$0.2500", page)          # today so far
        self.assertIn("month to date", page)
        self.assertIn("today so far", page)
        self.assertIn("probe runs", page)
        self.assertIn('class="spend-spark"', page)
        self.assertEqual(page.count('class="spark-bar"'), 3)
        # 2-day fetched series → 27 of 30 slots render as window-missing
        # stubs, not "no billed traffic" (owner 2026-09-07 honest-stub law).
        self.assertEqual(page.count('class="spark-missing"'), 27)
        self.assertEqual(page.count('class="spark-zero"'), 0)
        self.assertIn("outside the fetched window", page)
        self.assertIn("Aug 27", page)           # sparkline end label

    def test_delta_column_em_dashes_without_prior_snapshot(self) -> None:
        page = self._page(self.PAYLOAD, [("2026-08-27", self.PAYLOAD)])
        self.assertIn("&#916; ask in / out", page)
        self.assertIn("no earlier snapshot for this route", page)
        self.assertIn("no earlier snapshot. In = prompt tokens", page)  # legend
        # no route cell shows a movement arrow; legend spans carry no title
        self.assertNotIn('class="delta-down" title', page)
        self.assertIn('data-tip="no earlier snapshot for this route"', page)
        self.assertNotIn('class="delta-up" title', page)

    def test_delta_column_against_prior_snapshot(self) -> None:
        dated = [("2026-08-26", self.PRIOR), ("2026-08-27", self.PAYLOAD)]
        page = self._page(self.PAYLOAD, dated)
        # ask_in fell 0.020 -> 0.014, ask_out held 0.042
        self.assertIn("delta-down", page)
        self.assertIn('data-tip="ask unchanged"', page)
        self.assertNotIn("no earlier snapshot for this route", page)
        # delta-up appears only in the visible legend line (color key),
        # not in any route's delta cell
        self.assertNotIn('class="delta-up" title', page)

    def test_payload_without_days_skips_spend_block(self) -> None:
        legacy = {k: v for k, v in self.PAYLOAD.items() if k != "days"}
        page = self._page(legacy, [])
        self.assertIn('id="pricing"', page)
        self.assertNotIn('class="spend-block"', page)
        self.assertIn("ali/qwen3.8-max", page)


class ProbeResultsSectionTests(unittest.TestCase):
    PAYLOAD: ClassVar[dict] = {
        "generated_at": "2026-08-27T20:00:00+00:00",
        "perf": {"window_hours": 24,
                 "models": {"ali/qwen3.8-max": {"reqs": 8908}}},
        "routes": {
            "ali/qwen3.8-max": {
                "ask_in": 0.014, "ask_out": 0.042, "eff_per_mtok": 0.02,
                "cache_pct": 60.0, "reqs": 1, "tok_in": 100, "tok_out": 10,
                "cost_usdc": "0.001000", "last_ts": "t", "source": "usage-logs",
            },
            "cp/cline-pass/qwen3.8-max": {
                "ask_in": 0.01, "ask_out": 0.03, "candidate": True,
                "source": "usage-logs",
            },
            "cx/qwen3.8-max": {
                "ask_in": 0.05, "ask_out": 0.15, "candidate": True,
                "source": "usage-logs",
            },
        },
    }

    def _run(self, scoring, cand_results):
        cells = [
            {"alias": "ali/qwen3.8-max", "check_id": cid, "status": "pass",
             "summary": "ok", "resolved_model": "ali/qwen3.8-max"}
            for cid in scoring
        ]
        for route, ok_count, cached in cand_results:
            for i, cid in enumerate(scoring):
                cell = {
                    "alias": route, "check_id": cid,
                    "status": "pass" if i < ok_count else "fail",
                    "summary": "ok", "candidate": True, "model": "qwen3.8-max",
                    "resolved_model": route,
                }
                if cid == "cache":
                    cell["evidence"] = {
                        "cached_tokens": cached, "usage": {"prompt_tokens": 100},
                    }
                cells.append(cell)
        return {"started_at": "2026-08-27T22:00:00", "origin": "local", "cells": cells}

    def _page(self, gen, run):
        with mock.patch.object(gen.rundata, "load_pricing", return_value=self.PAYLOAD), \
                mock.patch.object(gen.rundata, "load_runs", return_value=[run]):
            page = gen.index_html([run], ["ali/qwen3.8-max"], gen.load_registry())
            nav = gen.board_nav()
        return page, nav

    def test_board_only_family_renders_group_without_candidates(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [])  # board cells only, no audition routes
        groups = gen.run_groups(run)
        self.assertEqual([g["model"] for g in groups], ["qwen3.8-max"])
        self.assertEqual(groups[0]["routes"], [])
        section = gen.probe_results_section(
            [run], ["ali/qwen3.8-max"], gen.load_registry(), self.PAYLOAD
        )
        self.assertIn('class="fam-head"', section)
        self.assertIn("qwen3.8-max", section)
        self.assertIn("ali/qwen3.8-max", section)
        self.assertIn("chip price", section)

    def test_candidate_row_field_order_matches_card_columns(self) -> None:
        # mobile cards fill column-first (tests + cache hit left, ask +
        # window right), so markup order must stay tests, cache, ask, window
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        section = gen.probe_results_section(
            [run], ["ali/qwen3.8-max"], gen.load_registry(), self.PAYLOAD
        )
        row = section[section.index('data-label="tests"'):]
        cache = row.index('data-label="cache hit"')
        ask = row.index('data-label="ask in / out"')
        window = row.index('data-label="window"')
        self.assertTrue(0 < cache < ask < window)

    def test_section_renders_incumbents_then_ranked_routes(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        # cp: 3/3 with 93% cache; cx: 3/3 with 40% cache -> cp ranks first
        run = self._run(scoring, [
            ("cp/cline-pass/qwen3.8-max", 2, 93),
            ("cx/qwen3.8-max", 2, 40),
        ])
        page, nav = self._page(gen, run)
        self.assertIn('id="results"', page)
        self.assertIn('href="#results"', nav)
        self.assertIn("<h2>Probe results</h2>", page)
        self.assertIn('class="fam-head"', page)
        self.assertIn('class="pill in-use"', page)
        pos_inc = page.find("ali/qwen3.8-max", page.find('id="results"'))
        pos_cp = page.find("cp/cline-pass/qwen3.8-max", page.find('id="results"'))
        pos_cx = page.find("cx/qwen3.8-max", page.find('id="results"'))
        self.assertTrue(pos_inc < pos_cp < pos_cx)
        self.assertIn('class="chip ok">ali/qwen3.8-max · 2/2', page)
        self.assertIn('class="chip ok">cp/cline-pass/qwen3.8-max · 2/2 · 93%', page)
        self.assertIn('id="fam-qwen3.8-max"', page)

    def test_failed_checks_rank_last_and_show_missed(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [
            ("cp/cline-pass/qwen3.8-max", 1, 93),   # missed one -> amber
            ("cx/qwen3.8-max", 2, 40),              # all pass -> ranks first
        ])
        page, _ = self._page(gen, run)
        pos_cp = page.find("cp/cline-pass/qwen3.8-max", page.find('id="results"'))
        pos_cx = page.find("cx/qwen3.8-max", page.find('id="results"'))
        self.assertTrue(pos_cx < pos_cp)
        self.assertIn("missed:", page)
        self.assertIn('class="tests-mid"', page)
        self.assertIn('class="tests-ok"', page)

    def test_tests_column_colors_fail_and_unprobed(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        # cp: 0/2 all fail -> red chip + tests-bad; cx: no cells -> unprobed dash
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 0, 93)])
        run["candidates"] = ["cp/cline-pass/qwen3.8-max", "cx/qwen3.8-max"]
        page, _ = self._page(gen, run)
        seg = page[page.find('id="results"'):page.find('id="method"')]
        self.assertIn('class="tests-bad"', seg)
        self.assertIn('class="chip bad">cp/cline-pass/qwen3.8-max · 0/2', seg)
        self.assertIn('class="tests-none"', seg)      # cx unprobed
        self.assertIn('&#8212;', seg)
        self.assertIn('data-tip="Not probed in the latest run"', seg)

    def test_candidates_stay_out_of_pricing_table(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        page, _ = self._page(gen, run)
        # probe results now follow the board, so slice the pricing block
        # between its own anchors
        seg = page[page.find('id="pricing"'):page.find('id="results"')]
        self.assertIn("ali/qwen3.8-max", seg)          # incumbent has rate data
        self.assertNotIn("cp/cline-pass/qwen3.8-max", seg)
        self.assertNotIn("cx/qwen3.8-max", seg)

    def test_board_only_run_still_renders_results(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        board_only = self._run(scoring, [])  # no candidate cells in the run
        page, nav = self._page(gen, board_only)
        self.assertIn('id="results"', page)
        self.assertIn("ali/qwen3.8-max", page)
        self.assertIn('href="#results"', nav)
        # A run with no cells at all still omits the section entirely.
        empty, _empty_nav = self._page(
            gen, {"started_at": "2026-08-27T22:00:00", "origin": "local", "cells": []}
        )
        self.assertNotIn('id="results"', empty)
        self.assertNotIn("<h2>Probe results</h2>", empty)

    def test_rows_carry_fold_labels_and_column_hints(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        page, _ = self._page(gen, run)
        seg = page[page.find('id="results"'):page.find('id="method"')]
        # One merged table: family bands as full-width head rows — no
        # per-family <details> grids anymore.
        self.assertNotIn('class="model-group"', seg)
        self.assertIn('<tr class="fam-head">', seg)
        # Exactly ONE probe-results table on the page (was 8).
        self.assertEqual(seg.count('class="pricing candidates"'), 1)
        for label in ("tests", "cache hit", "ask in / out", "window"):
            self.assertIn(f'data-label="{label}"', seg)
        for hint in (
            'title="Provider route;',
            'title="Scoring checks passed in the latest probe',
            'title="Prompt-cache share',
            'title="Ask price per M tokens',
            'title="All-pass runs',
        ):
            self.assertIn(hint, seg)

    def test_candidate_cells_carry_hover_and_touch_explanations(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 2, 93)])
        section = gen.probe_results_section(
            [run], ["ali/qwen3.8-max"], gen.load_registry(), self.PAYLOAD
        )
        start = section.index('data-label="tests"')
        row = section[start:section.index("</tr>", start)]
        for tip in (
            "Scoring checks passed in the latest probe",
            "Prompt-cache share",
            "Ask price per M tokens",
            "All-pass runs / probed runs since the route was first seen.",
        ):
            self.assertIn(tip, row)
        self.assertNotIn("title=", row)

    def test_failed_probe_cell_names_the_missed_check_in_its_tip(self) -> None:
        gen = _load_generate()
        scoring = gen.rundata.scoring_ids(gen.load_registry())
        run = self._run(scoring, [("cp/cline-pass/qwen3.8-max", 1, None)])
        section = gen.probe_results_section(
            [run], ["ali/qwen3.8-max"], gen.load_registry(), self.PAYLOAD
        )
        self.assertIn("failed:", section)


class PairCellTest(unittest.TestCase):
    """B4 gap: _pair_cell's two eras — the projection branch had no direct
    test (the gate never passes on current fixtures, so render tests only
    exercise the realized side)."""

    def setUp(self):
        self.gen = _load_generate()

    def test_projection_basis_pair(self) -> None:
        pair, label, _tip = self.gen._pair_cell("0.0114", "0.0005", True)
        self.assertEqual(label, "projected $/M")
        self.assertIn('<span class="pair-main">0.0005', pair)
        self.assertIn("<span class=\"pair-tag\">now</span>", pair)
        self.assertIn('<span class="pair-alt">0.0114', pair)
        self.assertIn("<span class=\"pair-tag\">30d</span>", pair)

    def test_realized_basis_pair(self) -> None:
        pair, label, _tip = self.gen._pair_cell("0.0114", "0.0005", False)
        self.assertEqual(label, "effective $/M")
        self.assertIn('<span class="pair-main">0.0114', pair)
        self.assertIn("<span class=\"pair-tag\">30d</span>", pair)
        self.assertIn('<span class="pair-alt">0.0005', pair)
        self.assertIn("<span class=\"pair-tag\">now</span>", pair)

    def test_no_projection_drops_the_alt_cell(self) -> None:
        pair, label, _tip = self.gen._pair_cell("0.0114", None, True)
        self.assertEqual(label, "effective $/M")
        self.assertNotIn("pair-alt", pair)


if __name__ == "__main__":
    unittest.main()


class DocsPageTests(unittest.TestCase):
    """The reading-guide page (owner order 2026-09-08: all explanatory
    prose lives on docs.html; the board stays numbers-only)."""

    def test_docs_page_renders_sections_explainers_and_anchors(self) -> None:
        gen = _load_generate()
        docs = gen.docs_html()
        self.assertIn("<h1>", docs)
        for slug in (
            "how-to-read-the-board",
            "the-scatter-chart",
            "probe-results-the-candidates-table",
            "how-we-test",
        ):
            self.assertIn(f'id="{slug}"', docs)
        for check_id in ("core", "cache"):
            self.assertIn(f'id="check-{check_id}"', docs)
        self.assertIn("report_answer", docs)
        self.assertIn("Ask for another", docs)
        self.assertIn("/issues/new", docs)
        self.assertIn("models.toml", docs)

    def test_board_links_to_docs_anchors(self) -> None:
        gen = _load_generate()
        html = gen.index_html(gen.load_runs(), gen.load_aliases(), gen.load_registry())
        for anchor in (
            "how-to-read-the-board",
            "the-scatter-chart",
            "probe-results-the-candidates-table",
            "how-we-test",
        ):
            self.assertIn(f"docs.html#{anchor}", html)
        self.assertIn("Reading guide", html)
