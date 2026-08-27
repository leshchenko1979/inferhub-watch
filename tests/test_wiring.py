from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from probe.registry import load_registry, repo_root

import sys

sys.path.insert(0, str(repo_root() / "site"))

import rundata  # noqa: E402


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
        spec = next(s for s in load_registry() if s["id"] == "stream_tools")
        html = gen.check_page(spec)
        self.assertIn("stream: true", html)
        self.assertIn("get_weather", html)
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
        self.assertIn('class="site-nav"', header)
        self.assertIn('href="#probe"', header)
        self.assertIn('href="#earlier"', header)
        self.assertIn('href="#method"', header)
        self.assertNotIn('href="#report"', header)
        self.assertNotIn('href="#today"', header)
        self.assertNotIn('href="#notes"', header)
        self.assertIn("Ask for another", html)
        self.assertIn("/issues/new", html)
        self.assertIn("models.toml", html)
        self.assertIn("github.com/leshchenko1979/inferhub-watch", rest)
        self.assertIn("Run locally", rest)
        self.assertNotIn("<footer", rest)
        self.assertIn("INFERHUB_API_KEY", rest)
        self.assertNotIn(os.environ.get("INFERHUB_API_KEY") or "sk-never", html)
        self.assertIn('class="matrix"', html)
        self.assertNotIn('class="rank-table"', html)
        self.assertIn('class="check-col col-score"', html)
        self.assertIn('href="#check-stream_tools"', html)
        self.assertIn('id="check-stream_tools"', html)
        self.assertIn("<details", html)
        self.assertIn("<summary>", html)
        self.assertIn("get_weather", html)
        self.assertIn("Prompt cache hit", html)
        self.assertIn("not part of Safe to use", html)
        self.assertIn("platform.openai.com", html)
        thead, _, after_head = html.partition("</thead>")
        self.assertNotIn("checks/stream_tools.html", thead)
        self.assertNotIn("checks/stream_tools.html", rest[rest.find('id="method"') :])
        self.assertNotIn("OpenCrabs", html)
        self.assertNotIn("Seven-day", html)
        self.assertNotIn("Who should care", html)
        self.assertIn("Prompt cache", html)
        self.assertIn("<h2>Latest results</h2>", html)
        self.assertIn("<h2>Past runs</h2>", html)
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
        self.assertIn('class="alias-cell"', html)
        self.assertIn('class="report"', html)
        self.assertIn('class="explanations"', html)
        self.assertNotIn('class="notes"', html)
        self.assertNotIn('class="about"', html)
        self.assertNotIn('class="hero"', html)
        self.assertIn("The endpoint", html)
        self.assertNotIn("What we probe", html)
        self.assertIn("ClinePass", html)
        self.assertLess(
            html.find('class="report"'),
            html.find('class="history"'),
        )
        self.assertLess(
            html.find('id="earlier"'),
            html.find('id="method"'),
        )
        self.assertLess(
            rest.find('class="verdict"'),
            rest.find("dispatch-meta"),
        )
        self.assertLess(rest.find("dispatch-meta"), rest.find('class="matrix"'))
        self.assertIn("Last probe:", html)
        report = rest[rest.find('id="probe"') : rest.find('id="earlier"')]
        self.assertNotIn("Actions", report)
        latest = gen.load_runs()[-1]
        expected_first = rundata.aliases_safe_first(
            gen.load_aliases(), latest, rundata.scoring_ids(load_registry())
        )[0]
        row_at = report.find('class="alias-cell"')
        self.assertIn(f'<span class="alias">{expected_first}</span>', report[row_at : row_at + 400])
        self.assertIn("<h1>Safe to use</h1>", html)
        self.assertNotIn("Safe to use:", html)
        self.assertIn("3/3: tools + cache + mojibake", html)
        self.assertNotIn("Scoring ", html)
        self.assertIn(
            "No alias is safe to use this run.",
            gen.index_html(
                [
                    {
                        "started_at": "2026-08-21T00:00:00",
                        "origin": "local-seed",
                        "cells": [
                            {
                                "alias": "x",
                                "check_id": "stream_tools",
                                "status": "fail",
                                "summary": "miss",
                                "resolved_model": "x",
                            },
                            {
                                "alias": "x",
                                "check_id": "cache_tools",
                                "status": "fail",
                                "summary": "miss",
                                "resolved_model": "x",
                            },
                            {
                                "alias": "x",
                                "check_id": "usage_pricing",
                                "status": "info",
                                "summary": "No price field.",
                                "resolved_model": "x",
                            },
                        ],
                    }
                ],
                ["x"],
                gen.load_registry(),
            ),
        )
        self.assertIn("check-col col-score", html)
        self.assertIn("check-col col-info", html)
        self.assertIn("info · not ranked", html)
        self.assertNotIn('class="st-info"><span class="pill"', html)
        self.assertIn('class="timeline"', html)
        self.assertIn("Actions · CI", html)
        self.assertIn("seed · fixture", html)
        self.assertIn("<caption>", html)
        self.assertIn('<details class="nav-menu">', html)
        self.assertIn("aria-expanded", html)
        self.assertIn("On this page", html)
        self.assertNotIn('id="nav-toggle"', html)
        self.assertIn("Run locally", html)
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
        self.assertIn('class="absent"', page)
        self.assertIn("not probed", page)
        self.assertNotIn('class="bad"', page)
        self.assertIn("was not on the", page)

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
        self.assertNotIn("cb/gpt-5.6-luna", page)
        self.assertIn("ocg/deepseek-v4-flash", page)


class PricingSectionTests(unittest.TestCase):
    PAYLOAD = {
        "generated_at": "2026-08-27T20:00:00+00:00",
        "range": "30d",
        "requests_scanned": 5700,
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
        self.assertIn('href="#earlier"', nav)

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
        self.assertLess(page.find('id="probe"'), page.find('id="pricing"'))
        self.assertLess(page.find('id="pricing"'), page.find('id="earlier"'))
        self.assertIn("ali/qwen3.8-max", page)
        self.assertIn("$0.014", page)
        self.assertIn("$0.042", page)
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
        self.assertIn("ask $0.014 / $0.042 per M", page)
        self.assertNotIn("ask in $/M", page)
        self.assertIn('class="viz-bar"', page)
        self.assertIn('class="route-ask"', page)


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
            "check_id": "stream_tools",
            "alias": "x",
            "status": "error",
            "summary": 'HTTP 402: {"error":{"message":"balance too low"}}',
        }
        stub = types.SimpleNamespace(run=lambda client, alias: dict(cell))
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with self.assertRaises(run_mod.BalanceTooLow):
                run_mod.collect_cells(object(), ["a"], [{"id": "stream_tools"}])

    def test_collect_cells_raises_on_balance_exception(self) -> None:
        run_mod = _load_run()

        def boom(client, alias):
            raise RuntimeError("insufficient_balance for key")

        stub = types.SimpleNamespace(run=boom)
        with mock.patch.object(run_mod, "load_check_module", return_value=stub):
            with self.assertRaises(run_mod.BalanceTooLow):
                run_mod.collect_cells(object(), ["a"], [{"id": "stream_tools"}])

    def test_main_aborts_without_writing_a_run(self) -> None:
        run_mod = _load_run()
        cell = {
            "check_id": "stream_tools",
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
                run_mod, "load_registry", return_value=[{"id": "stream_tools"}]
            ), mock.patch.object(
                run_mod, "InferHubClient", return_value=object()
            ), mock.patch.object(
                run_mod, "repo_root", return_value=Path(tmp)
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
            "check_id": "stream_tools",
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
                run_mod, "load_registry", return_value=[{"id": "stream_tools"}]
            ), mock.patch.object(
                run_mod, "InferHubClient", return_value=object()
            ), mock.patch.object(
                run_mod, "repo_root", return_value=Path(tmp)
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
    PAYLOAD = {
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
    PRIOR = {
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
        self.assertEqual(page.count('class="spark-zero"'), 27)
        self.assertIn("Aug 27", page)           # sparkline end label

    def test_delta_column_em_dashes_without_prior_snapshot(self) -> None:
        page = self._page(self.PAYLOAD, [("2026-08-27", self.PAYLOAD)])
        self.assertIn("&#916; ask in / out", page)
        self.assertIn("no earlier snapshot for this route", page)
        self.assertNotIn("delta-down", page)
        self.assertNotIn("delta-up", page)

    def test_delta_column_against_prior_snapshot(self) -> None:
        dated = [("2026-08-26", self.PRIOR), ("2026-08-27", self.PAYLOAD)]
        page = self._page(self.PAYLOAD, dated)
        # ask_in fell 0.020 -> 0.014, ask_out held 0.042
        self.assertIn("delta-down", page)
        self.assertIn('title="ask unchanged"', page)
        self.assertNotIn("no earlier snapshot for this route", page)
        self.assertNotIn("delta-up", page)

    def test_payload_without_days_skips_spend_block(self) -> None:
        legacy = {k: v for k, v in self.PAYLOAD.items() if k != "days"}
        page = self._page(legacy, [])
        self.assertIn('id="pricing"', page)
        self.assertNotIn('class="spend-block"', page)
        self.assertIn("ali/qwen3.8-max", page)


if __name__ == "__main__":
    unittest.main()

