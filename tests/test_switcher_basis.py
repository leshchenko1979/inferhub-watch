"""Issue #27 tests: valuation basis consistency and the dwell gate.

The thrash: the armed auto-switcher flipped the fleet default twice in ~11
minutes because the incumbent was ranked on its REALIZED (billed) basis
while the challenger was ranked on its projected FLOOR basis. The 58x gap
between those two bases read as a +561.9% gain. These tests pin the two
mechanisms that stop it: ONE basis per evaluation, and a dwell gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.auto_route_switch import (
    BASIS_FLOOR,
    BASIS_MIN_SAMPLES,
    BASIS_REALIZED,
    DWELL_OVERRIDE_GAIN,
    DWELL_SECONDS,
    RouteCandidate,
    apply_basis,
    check_dwell_gate,
    evaluate_candidates,
    find_best_route,
    load_switcher_state,
    resolve_floor_price,
    resolve_realized_price,
    save_switcher_state,
    select_basis,
)

AA_MAP: dict = {}
INTEL = {"m1": {"iq": 38.0}, "m2": {"iq": 38.0}}

CATALOG = {
    "inc/m1": {"ask_in": 0.002, "ask_out": 0.01, "supports_tools": True, "supports_cache": False},
    "chal/m2": {"ask_in": 0.0021, "ask_out": 0.0105, "supports_tools": True, "supports_cache": False},
}


def _payload(inc_samples: int, chal_samples: int, inc_eff: float = 0.02, chal_eff: float = 0.0205) -> dict:
    return {
        "routes": {
            "inc/m1": {"eff_per_mtok": inc_eff},
            "chal/m2": {"eff_per_mtok": chal_eff},
        },
        "perf": {
            "models": {
                "inc/m1": {"tps_mean": 100.0, "tps_samples": inc_samples},
                "chal/m2": {"tps_mean": 100.0, "tps_samples": chal_samples},
            }
        },
    }


class TestSelectBasis:
    def test_all_candidates_supplied_realized_selects_realized(self) -> None:
        cands = [
            RouteCandidate("a", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                           realized_price=0.02, realized_samples=BASIS_MIN_SAMPLES),
            RouteCandidate("b", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                           realized_price=0.03, realized_samples=900),
        ]
        assert select_basis(cands) == BASIS_REALIZED

    def test_one_candidate_without_realized_drops_everyone_to_floor(self) -> None:
        """The #27 regression: never mix realized-for-incumbent with floor-for-challenger."""
        cands = [
            RouteCandidate("inc", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                           realized_price=0.02, realized_samples=5572),
            RouteCandidate("chal", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                           realized_price=None, realized_samples=0),
        ]
        assert select_basis(cands) == BASIS_FLOOR

    def test_thin_sample_candidate_drops_everyone_to_floor(self) -> None:
        cands = [
            RouteCandidate("inc", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                           realized_price=0.02, realized_samples=5572),
            RouteCandidate("thin", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                           realized_price=0.03, realized_samples=BASIS_MIN_SAMPLES - 1),
        ]
        assert select_basis(cands) == BASIS_FLOOR

    def test_empty_pool_is_floor(self) -> None:
        assert select_basis([]) == BASIS_FLOOR


class TestApplyBasis:
    def _cand(self) -> RouteCandidate:
        return RouteCandidate("r", 38.0, 0.0, 0.0, 0.0, 100.0, 0.0, True, False,
                              floor_price=0.002, realized_price=0.02, realized_samples=500)

    def test_floor_mode_ranks_on_floor_price(self) -> None:
        cand = self._cand()
        apply_basis([cand], BASIS_FLOOR, tps_ref=100.0)
        assert cand.eff_price == 0.002
        assert cand.value == pytest.approx(38.0 / 0.002)

    def test_realized_mode_ranks_on_realized_price(self) -> None:
        cand = self._cand()
        apply_basis([cand], BASIS_REALIZED, tps_ref=100.0)
        assert cand.eff_price == 0.02
        assert cand.value == pytest.approx(38.0 / 0.02)

    def test_realized_mode_falls_back_to_floor_without_billing(self) -> None:
        cand = self._cand()
        cand.realized_price = None
        apply_basis([cand], BASIS_REALIZED, tps_ref=100.0)
        assert cand.eff_price == cand.floor_price


class TestBasisConsistencyEndToEnd:
    def test_thin_challenger_forces_floor_for_the_incumbent_too(self) -> None:
        """The incumbent must NOT be ranked on its cheap-looking realized bill
        when the challenger cannot supply one — that mix is the thrash engine."""
        payload = _payload(inc_samples=5572, chal_samples=2)
        out: dict = {}
        find_best_route(
            CATALOG, INTEL, AA_MAP,
            current_model="inc/m1", pricing_payload=payload,
            dated_snapshots=[], use_proj=False, out=out,
        )
        assert out["basis_mode"] == BASIS_FLOOR
        assert out["basis_samples"] == 2

        cands = evaluate_candidates(
            CATALOG, INTEL, AA_MAP, pricing_payload=payload,
            dated_snapshots=[], use_proj=False,
        )
        inc = cands["inc/m1"]
        # Both bases are carried...
        assert inc.realized_price == 0.02
        assert inc.floor_price == pytest.approx(0.99 * 0.002 + 0.01 * 0.01)
        # ...and the ranked price is the FLOOR one, not the realized one.
        apply_basis(list(cands.values()), BASIS_FLOOR, tps_ref=100.0)
        assert inc.eff_price == inc.floor_price
        assert inc.eff_price != inc.realized_price

    def test_realized_expensive_incumbent_no_longer_produces_a_spurious_switch(self) -> None:
        """Old mixed basis: incumbent realized 0.02 vs challenger floor 0.0022
        read as a ~800% gain and flipped the fleet. On one consistent basis the
        two floors are nearly equal, so the honest verdict is NO SWITCH."""
        payload = _payload(inc_samples=5572, chal_samples=2, inc_eff=0.02, chal_eff=0.0205)
        should_switch, best, current, reason = find_best_route(
            CATALOG, INTEL, AA_MAP,
            current_model="inc/m1", pricing_payload=payload,
            dated_snapshots=[], use_proj=False, threshold=0.15,
        )
        assert "basis=floor" in reason
        # The incumbent's floor (0.00208) is CHEAPER than the challenger's
        # (0.002184), so on one basis there is nothing to gain.
        assert should_switch is False
        assert best.route == "inc/m1"

    def test_all_candidates_supplied_realized_uses_realized(self) -> None:
        payload = _payload(inc_samples=100, chal_samples=100, inc_eff=0.02, chal_eff=0.005)
        out: dict = {}
        should_switch, best, _current, reason = find_best_route(
            CATALOG, INTEL, AA_MAP,
            current_model="inc/m1", pricing_payload=payload,
            dated_snapshots=[], use_proj=False, threshold=0.15, out=out,
        )
        assert out["basis_mode"] == BASIS_REALIZED
        assert "basis=realized" in reason
        assert best.route == "chal/m2"
        assert should_switch is True

    def test_resolvers_agree_with_the_selected_basis(self) -> None:
        payload = _payload(inc_samples=100, chal_samples=100)
        floor_price = resolve_floor_price("inc/m1", CATALOG["inc/m1"], payload, [], use_proj=False)
        realized_price, samples = resolve_realized_price("inc/m1", payload)
        assert floor_price == pytest.approx(0.00208)
        assert realized_price == 0.02
        assert samples == 100


class TestDwellGate:
    def test_no_prior_switch_is_allowed(self, tmp_path) -> None:
        verdict = check_dwell_gate(tmp_path, gain=5.0)
        assert verdict["allowed"] is True
        assert verdict["reason"] == "no_prior_switch"

    def test_switch_inside_the_dwell_window_is_refused(self, tmp_path) -> None:
        now = datetime(2026, 9, 12, 7, 30, tzinfo=timezone.utc)
        save_switcher_state(tmp_path, "a/m", "b/m", BASIS_FLOOR, when=now - timedelta(minutes=11))
        verdict = check_dwell_gate(tmp_path, gain=1.5, now=now)
        assert verdict["allowed"] is False
        assert verdict["reason"] == "dwell_active"
        assert "dwell requires 6.0h" in verdict["detail"]

    def test_large_gain_overrides_the_dwell_window(self, tmp_path) -> None:
        now = datetime(2026, 9, 12, 7, 30, tzinfo=timezone.utc)
        save_switcher_state(tmp_path, "a/m", "b/m", BASIS_FLOOR, when=now - timedelta(minutes=11))
        verdict = check_dwell_gate(tmp_path, gain=DWELL_OVERRIDE_GAIN + 0.5, now=now)
        assert verdict["allowed"] is True
        assert verdict["reason"] == "override_gain"

    def test_gain_exactly_at_the_override_is_not_enough(self, tmp_path) -> None:
        now = datetime(2026, 9, 12, 7, 30, tzinfo=timezone.utc)
        save_switcher_state(tmp_path, "a/m", "b/m", BASIS_FLOOR, when=now - timedelta(hours=1))
        verdict = check_dwell_gate(tmp_path, gain=DWELL_OVERRIDE_GAIN, now=now)
        assert verdict["allowed"] is False

    def test_elapsed_dwell_reopens_the_gate(self, tmp_path) -> None:
        now = datetime(2026, 9, 12, 7, 30, tzinfo=timezone.utc)
        save_switcher_state(tmp_path, "a/m", "b/m", BASIS_FLOOR,
                            when=now - timedelta(seconds=DWELL_SECONDS + 60))
        verdict = check_dwell_gate(tmp_path, gain=0.2, now=now)
        assert verdict["allowed"] is True
        assert verdict["reason"] == "dwell_elapsed"

    def test_corrupt_state_never_blocks_a_switch(self, tmp_path) -> None:
        path = tmp_path / "data" / "switcher_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_switcher_state(tmp_path) == {}
        assert check_dwell_gate(tmp_path, gain=0.0)["allowed"] is True

    def test_unparseable_timestamp_never_blocks_a_switch(self, tmp_path) -> None:
        path = tmp_path / "data" / "switcher_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"last_switch_at": "not-a-date"}', encoding="utf-8")
        verdict = check_dwell_gate(tmp_path, gain=0.0)
        assert verdict["allowed"] is True
        assert verdict["reason"] == "unparseable_state"

    def test_state_roundtrip(self, tmp_path) -> None:
        when = datetime(2026, 9, 12, 7, 24, tzinfo=timezone.utc)
        save_switcher_state(tmp_path, "ag/gemini-3.8-flash-high", "cb/deepseek-v4.1-flash",
                            BASIS_FLOOR, when=when)
        state = load_switcher_state(tmp_path)
        assert state["from_model"] == "ag/gemini-3.8-flash-high"
        assert state["to_model"] == "cb/deepseek-v4.1-flash"
        assert state["basis_mode"] == BASIS_FLOOR
        assert state["last_switch_at"] == "2026-09-12T07:24:00+00:00"
