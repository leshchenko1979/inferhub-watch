"""Ontology enforcement: site copy uses ontology terms, no banned synonyms.

The naming law lives in ONTOLOGY.md at the repo root; this test keeps code
and copy honest against it.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# P2b carve: the site copy lives in the section modules now; generate.py is
# the composition root. The copy law scans all of them.
SITE_MODULES = [ROOT / "site" / n for n in (
    "generate.py", "chrome.py", "spend.py", "ticket.py", "board.py", "results.py",
)]
ONTOLOGY = ROOT / "ONTOLOGY.md"


class OntologyFileTest(unittest.TestCase):
    def test_ontology_md_exists_with_required_sections(self) -> None:
        text = ONTOLOGY.read_text()
        for section in ("## Entities", "## Prices", "## Failures",
                        "## Invariants", "## Naming law", "Banned synonyms"):
            self.assertIn(section, text)

    def test_every_term_row_maps_to_a_realizing_identifier(self) -> None:
        text = ONTOLOGY.read_text()
        # each entity/price/failure row must name the code or label that
        # realizes it (the "Realized as" column is non-empty)
        for line in text.splitlines():
            if line.startswith("| **") and " — " in line:
                cols = [c.strip() for c in line.strip("|").split("|")]
                self.assertGreaterEqual(len(cols), 3, line)
                self.assertTrue(cols[-1], f"no realization given: {line}")


class SiteCopyTermsTest(unittest.TestCase):
    def load(self) -> str:
        return "\n".join(p.read_text() for p in SITE_MODULES)

    def test_no_banned_error_noun_in_copy(self) -> None:
        # "failure" is the ontology noun; "error(s)" is banned in site copy.
        # Allow: identifiers, imports, and the docstring word "error" only
        # inside code (HTML attributes, class names) — the user-visible
        # strings must say failure.
        text = self.load()
        for m in re.finditer(r'"([^"]*\berror\w*\b[^"]*)"', text, re.IGNORECASE):
            s = m.group(1)
            # permitted: non-copy technical strings
            if s.startswith("test") or "cache miss" in s:
                continue
            self.fail(f"banned 'error' noun in site copy: {s!r}")

    def test_retired_failures_table_stays_gone(self) -> None:
        # owner 2026-09-07: the failures table was removed (plumbing row
        # carries per-route failures) — the retirement must stick.
        text = self.load()
        self.assertNotIn("def failures_table(", text)
        self.assertNotIn("def official_table(", text)
        self.assertNotIn("def perf_table(", text)
        self.assertNotIn("def evidence_block(", text)

    def test_floor_ask_legend_present(self) -> None:
        text = self.load()
        self.assertIn("floor ask &#8212; catalog minimum, no billed traffic yet", text)
        self.assertNotIn("catalog list price", text)

    def test_projection_pair_uses_now_vs_30d(self) -> None:
        # owner 2026-09-07: the official comparison table is gone; the
        # two-era pairing copy lives in the rate cell's tip.
        text = self.load()
        self.assertIn("current billed asks", text)
        self.assertIn("realized window", text)

    def test_pricing_key_is_failures(self) -> None:
        pricing = (ROOT / "probe" / "pricing.py").read_text()
        self.assertIn('"failures": failure_stats(rows)', pricing)
        self.assertNotIn("error_stats", pricing)
        self.assertNotIn('"errors"', pricing)


if __name__ == "__main__":
    unittest.main()
