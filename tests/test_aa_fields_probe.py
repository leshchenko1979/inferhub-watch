"""The perf-hint scan must count ENTRIES, not the first one it sees.

`scripts/aa_fields_probe.py` reports what the AA Free response carries on the
wire, and its counts are read as coverage. The list branch of the original
`walk()` descended `obj[0]` only, so section 3 inspected exactly ONE model and
printed the result in the shape of a fleet-wide figure — the reading that
nearly cast doubt on a 646/646 headline.

These tests pin the scope: every entry walked, each counted once.
"""

import unittest

from scripts.aa_fields_probe import perf_hint_counts

# Both contain a HINTS substring ("token" / "second"), so both are discovered.
NAMED = "median_output_tokens_per_second"
TTFT = "median_time_to_first_token_seconds"

def _entry(slug, **extra):
    return {"slug": slug, **extra}

class PerfHintCountsTest(unittest.TestCase):
    def test_every_entry_is_walked_not_just_the_first(self):
        entries = [
            _entry("a", **{NAMED: 12.5}),
            _entry("b", **{NAMED: 9.0}),
            _entry("c"),
        ]
        self.assertEqual(perf_hint_counts(entries), {NAMED: 2})

    def test_a_key_nested_inside_a_list_counts_each_entry_once(self):
        entries = [
            _entry("a", provider={TTFT: 0.4}),
            _entry("b", provider={TTFT: 0.5}),
        ]
        self.assertEqual(perf_hint_counts(entries), {TTFT: 2})

    def test_a_null_value_is_not_coverage(self):
        entries = [_entry("a", **{NAMED: None}), _entry("b", **{NAMED: 3.0})]
        self.assertEqual(perf_hint_counts(entries), {NAMED: 1})

    def test_a_key_repeated_within_one_entry_is_counted_once(self):
        entries = [_entry("a", top={NAMED: 1.0}, nested={NAMED: 2.0})]
        self.assertEqual(perf_hint_counts(entries), {NAMED: 1})

    def test_no_hint_key_anywhere_counts_nothing(self):
        entries = [_entry("a", name="x"), _entry("b", name="y")]
        self.assertEqual(perf_hint_counts(entries), {})

    def test_empty_input_is_empty(self):
        self.assertEqual(perf_hint_counts([]), {})

if __name__ == "__main__":
    unittest.main()
