import io
import unittest
from unittest import mock

from probe import routes

REGISTRY = [
    {"id": "core", "scores_rank": True},
    {"id": "cache", "scores_rank": True},
    {"id": "info", "scores_rank": False},
]


def _cell(check_id, status="pass", latency=123, summary="ok", evidence=None):
    return {
        "check_id": check_id,
        "status": status,
        "latency_ms": latency,
        "summary": summary,
        "evidence": evidence or {},
    }


class ScoringSpecsTests(unittest.TestCase):
    def test_filters_to_scores_rank(self):
        specs = routes.scoring_specs(REGISTRY)
        self.assertEqual([s["id"] for s in specs], ["core", "cache"])


class ProbeRoutesTests(unittest.TestCase):
    def test_sweeps_routes_and_records_errors(self):
        core = mock.Mock()
        core.run.side_effect = [_cell("core"), RuntimeError("boom")]
        cache = mock.Mock()
        cache.run.side_effect = [_cell("cache", status="fail", summary="no cache")]
        modules = {"core": core, "cache": cache}
        with mock.patch.object(
            routes, "load_check_module", side_effect=lambda cid: modules[cid]
        ):
            cells = routes.probe_routes(object(), ["a/x", "b/y"], REGISTRY)
        self.assertEqual(len(cells), 4)
        self.assertEqual(cells[0]["route"], "a/x")
        self.assertEqual(cells[0]["check_id"], "core")
        self.assertEqual(cells[0]["status"], "pass")
        # a/x: core passed, cache failed -> a/x stops probing after the fail
        self.assertEqual(cells[1]["route"], "a/x")
        self.assertEqual(cells[1]["check_id"], "cache")
        self.assertEqual(cells[1]["status"], "fail")
        # b/y: core raised -> error cell, cache is skipped by fail-fast
        self.assertEqual(cells[2]["route"], "b/y")
        self.assertEqual(cells[2]["check_id"], "core")
        self.assertEqual(cells[2]["status"], "error")
        self.assertIn("boom", cells[2]["summary"])
        self.assertEqual(cells[3]["route"], "b/y")
        self.assertEqual(cells[3]["check_id"], "cache")
        self.assertEqual(cells[3]["status"], "skipped")
        self.assertIn("not run", cells[3]["summary"])
        for cell in cells:
            self.assertNotEqual(cell["check_id"], "info")
        self.assertEqual(cache.run.call_count, 1)  # fail-fast: no cache probe of b/y

    def test_balance_too_low_stops_sweep(self):
        core = mock.Mock()
        core.run.side_effect = RuntimeError("balance too low for this request")
        modules = {"core": core, "cache": mock.Mock()}
        stderr = io.StringIO()
        with mock.patch.object(
            routes, "load_check_module", side_effect=lambda cid: modules[cid]
        ), mock.patch("sys.stderr", stderr):
            cells = routes.probe_routes(object(), ["a/x", "b/y"], REGISTRY)
        self.assertEqual(cells, [])
        self.assertIn("balance too low", stderr.getvalue())
        modules["cache"].run.assert_not_called()


class FormatTableTests(unittest.TestCase):
    def test_renders_status_latency_cache_summary(self):
        text = routes.format_table(
            [
                {
                    "route": "cmc/deepseek/deepseek-v4-pro",
                    **_cell(
                        "cache",
                        evidence={"hit_ratio": 0.94},
                        summary="cached prefix reused",
                    ),
                },
                {
                    "route": "nous/x",
                    "check_id": "core",
                    "status": "error",
                    "latency_ms": None,
                    "summary": "HTTP 503",
                },
            ]
        )
        self.assertIn("cmc/deepseek/deepseek-v4-pro", text)
        self.assertIn("cache", text)
        self.assertIn("cache=94%", text)
        self.assertIn("cached prefix reused", text)
        self.assertIn("HTTP 503", text)
        self.assertIn("-", text)


class MainTests(unittest.TestCase):
    def test_no_routes_is_usage_error(self):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            rc = routes.main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage", err.getvalue())

    def test_no_key_is_usage_error(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch(
            "sys.stderr", io.StringIO()
        ) as err:
            rc = routes.main(["a/x"])
        self.assertEqual(rc, 2)
        self.assertIn("INFERHUB_API_KEY", err.getvalue())

    def test_sweep_prints_table(self):
        module = mock.Mock()
        module.run.return_value = _cell("core", summary="tool call streamed")
        with mock.patch.dict(
            "os.environ", {"INFERHUB_API_KEY": "sk-test"}, clear=True
        ), mock.patch.object(
            routes, "load_registry", return_value=REGISTRY
        ), mock.patch.object(
            routes, "InferHubClient"
        ) as client_cls, mock.patch.object(
            routes, "load_check_module", return_value=module
        ), mock.patch(
            "sys.stdout", new_callable=io.StringIO
        ) as out:
            rc = routes.main(["a/x"])
        self.assertEqual(rc, 0)
        self.assertIn("a/x", out.getvalue())
        self.assertIn("tool call streamed", out.getvalue())
        client_cls.assert_called_once_with("sk-test")


if __name__ == "__main__":
    unittest.main()