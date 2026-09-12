"""Re-arm criterion 2: stability across two synthetic basis shifts (#27).

The harness itself lives in ``scripts/basis_shift_harness.py``; these tests pin
the properties the re-arm gate rests on, so a future change that quietly
weakens the basis gate fails here instead of in production.

What must hold:
  1. the basis actually changes twice (floor -> realized -> floor) — otherwise
     the stability claim is about a basis that never moved;
  2. NO switch is emitted on any run;
  3. the challenger was qualified and genuinely LOST the ranking — a no-switch
     that comes from the challenger being excluded proves nothing;
  4. the pre-#27 MIXED ranking on the same fixture WOULD have switched, so the
     gate is load-bearing rather than vacuous;
  5. nothing live is touched.
"""

from __future__ import annotations

import pytest

from scripts import auto_route_switch as ars
from scripts.basis_shift_harness import (
    CHALLENGER,
    INCIDENT_MODEL,
    run_harness,
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return run_harness()


class TestBasisShiftStability:
    def test_the_basis_changes_twice(self, receipt) -> None:
        assert receipt["basis_sequence"] == [
            ars.BASIS_FLOOR,
            ars.BASIS_REALIZED,
            ars.BASIS_FLOOR,
        ]
        assert receipt["shift_count"] == 2

    def test_every_run_lands_on_the_basis_the_fixture_forces(self, receipt) -> None:
        for run in receipt["runs"]:
            assert run["basis_mode"] == run["expected_basis"], run

    def test_no_switch_is_emitted_on_any_run(self, receipt) -> None:
        assert receipt["no_switch_on_any_run"] is True
        for run in receipt["runs"]:
            assert run["should_switch"] is False, run

    def test_the_incumbent_is_the_top_candidate_every_run(self, receipt) -> None:
        for run in receipt["runs"]:
            assert run["current_model"] == INCIDENT_MODEL
            assert run["best_model"] == INCIDENT_MODEL, run

    def test_no_state_file_is_written_when_nothing_switches(self, receipt) -> None:
        assert receipt["production_mutation"]["fixture_state_written"] is False


class TestTheGateIsLoadBearing:
    def test_the_challenger_is_qualified_and_genuinely_loses(self, receipt) -> None:
        """A no-switch from an excluded challenger would be vacuous."""
        assert receipt["challenger_non_vacuous"] is True
        for run in receipt["runs"]:
            assert run["challenger_qualified"] is True, run
            ranked = run["ranked_on_basis"]
            assert ranked["challenger_loses"] is True, run
            assert ranked["challenger_gain_pct"] < ars.SWITCH_THRESHOLD * 100, run

    def test_the_pre_27_mixed_ranking_would_have_switched(self, receipt) -> None:
        """The #27 regression, reproduced: incumbent on realized vs challenger
        on floor reads as a >threshold gain, and that is what the gate stops."""
        mixed = receipt["mixed_pre_27_would_switch"]
        assert mixed["would_switch"] is True
        assert mixed["gain_pct"] > ars.SWITCH_THRESHOLD * 100
        assert mixed["challenger_value"] > mixed["incumbent_value"]


class TestZeroProductionMutation:
    def test_the_live_config_is_untouched(self, receipt) -> None:
        mut = receipt["production_mutation"]
        assert mut["live_config"] == str(ars.DEFAULT_CONFIG_PATH)
        assert mut["live_config_bytes_unchanged"] is True
        # On the ops box this compares real bytes. In CI /root is unreadable,
        # so there is nothing to fingerprint and this comparison is vacuous
        # there — the no-write proof is
        # test_no_config_session_or_notification_write_was_attempted.
        assert isinstance(mut["live_config_readable"], bool)

    def test_no_repo_state_file_appears(self, receipt) -> None:
        assert receipt["production_mutation"]["repo_switcher_state_present"] is False

    def test_no_config_session_or_notification_write_was_attempted(
        self, receipt
    ) -> None:
        mut = receipt["production_mutation"]
        assert mut["config_write_calls"] == 0
        assert mut["session_write_calls"] == 0
        assert mut["notification_calls"] == 0
        assert mut["notify_session_calls"] == 0
        for run in receipt["runs"]:
            assert run["config_updated"] is False, run
            assert run["sessions_updated"] == 0, run
            assert run["notification_sent"] is False, run

    def test_every_run_reads_the_fixture_catalog_not_the_live_api(
        self, receipt
    ) -> None:
        """Proves the harness is offline: no live API, no Postgres catalog."""
        for run in receipt["runs"]:
            assert run["catalog_source"] == "data/catalog.json", run

    def test_the_dwell_gate_is_open_by_design_on_a_fresh_root(
        self, receipt
    ) -> None:
        """No prior switch exists in a temp root, so the gate cannot be what
        held the switch — the ranking did (see TestTheGateIsLoadBearing)."""
        for run in receipt["runs"]:
            assert run["dwell"] == "no_prior_switch", run


class TestFixtureSanity:
    def test_the_two_routes_are_the_incident_pair(self) -> None:
        assert INCIDENT_MODEL == "cb/deepseek-v4.1-flash"
        assert CHALLENGER == "ag/gemini-3.8-flash-high"
