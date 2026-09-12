"""Criterion-2 harness: stability across two synthetic basis shifts (#27).

Re-arm criterion 2 (``skills/inferhub/SKILL.md`` §Armed auto-switcher →
RE-ARM CRITERIA) requires a verified dry-run showing **stability across two
consecutive synthetic basis shifts** — force the basis to change twice and
confirm no switch is emitted on either shift.

This harness drives the REAL pipeline entry point
(``scripts.auto_route_switch.run_auto_route_switch``) over fixture inputs in a
temporary root, with ``dry_run=True`` and ``notify=False``. Nothing live is
touched: no config write, no session churn, no Telegram, no state file in the
repo. The live surfaces are additionally patched to RAISE, so an accidental
production write fails loudly instead of mutating the fleet.

Fixture shape — the #27 thrash reproduced in miniature:

    route                      floor ask (catalog)  realized ask (billed)
    cb/deepseek-v4.1-flash     0.00040 (incumbent)  0.00090
    ag/gemini-3.8-flash-high   0.00060 (challenger) 0.00120

On ONE basis the incumbent wins on both:

    floor     0.00040 < 0.00060  -> gain -33.3%  (no switch)
    realized  0.00090 < 0.00120  -> gain -25.0%  (no switch)

The pre-#27 MIXED ranking (incumbent on its realized ask, challenger on its
floor ask) reads 0.00090 against 0.00060 as a **+50.0% gain -> switch**. That
mixed comparison is the thrash. The harness reports that contrast, so the
gate is shown to be load-bearing rather than vacuous.

Three runs, two shifts:

    run 1  challenger has 0 realized samples  -> basis floor
    run 2  challenger gains 5 samples         -> basis realized  (SHIFT 1)
    run 3  challenger loses them again        -> basis floor     (SHIFT 2)
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from probe import pgstore  # noqa: E402
from scripts import auto_route_switch as ars  # noqa: E402

INCIDENT_MODEL = "cb/deepseek-v4.1-flash"
CHALLENGER = "ag/gemini-3.8-flash-high"

FIXTURE_IQ = 38.0
FIXTURE_TPS = 240.0

# Catalog asks -> floor basis (projection gate stays CLOSED here: the fixture
# root carries no data/pricing/ history, so resolve_floor_price falls through
# to the catalog floor ask, which is exactly the traffic-independent basis).
FIXTURE_CATALOG: dict[str, dict[str, Any]] = {
    INCIDENT_MODEL: {
        "official_in": 0.15,
        "official_out": 0.6,
        "supports_cache": False,
        "supports_tools": True,
        "ask_in": 0.0004,
        "ask_out": 0.0004,
    },
    CHALLENGER: {
        "official_in": 0.3,
        "official_out": 1.2,
        "supports_cache": False,
        "supports_tools": True,
        "ask_in": 0.0006,
        "ask_out": 0.0006,
    },
}

# resolve_slug normalises the route tail (dots -> dashes), so the intel slugs
# are the normalised tails of the two routes above.
FIXTURE_INTEL = {
    "models": {
        "deepseek-v4-1-flash": {"iq": FIXTURE_IQ},
        "gemini-3-8-flash-high": {"iq": FIXTURE_IQ},
    }
}

FIXTURE_CONFIG = f'[agent]\ndefault_model = "{INCIDENT_MODEL}"\n'

# (label, realized samples behind the CHALLENGER, expected basis)
RUN_SPECS: list[tuple[str, int, str]] = [
    ("run 1 (pre-shift)", 0, ars.BASIS_FLOOR),
    ("run 2 (SHIFT 1: floor -> realized)", 5, ars.BASIS_REALIZED),
    ("run 3 (SHIFT 2: realized -> floor)", 0, ars.BASIS_FLOOR),
]

FIXED_NOW = datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc)


def fixture_pricing(challenger_samples: int) -> dict[str, Any]:
    """The billing/perf payload for one run (issue #27 realized basis)."""
    return {
        "routes": {
            INCIDENT_MODEL: {"eff_per_mtok": 0.0009},
            CHALLENGER: {"eff_per_mtok": 0.0012},
        },
        "perf": {
            "models": {
                INCIDENT_MODEL: {"tps_mean": FIXTURE_TPS, "tps_samples": 5000},
                CHALLENGER: {"tps_mean": FIXTURE_TPS, "tps_samples": challenger_samples},
            }
        },
    }


def _production_guards() -> tuple[list[Any], dict[str, Any]]:
    """Patch every live surface the pipeline can reach, to RAISE if used.

    Returns the patch list plus the mock objects themselves, so the receipt
    reports MEASURED call counts rather than asserted zeros: a write that is
    attempted raises here instead of mutating the fleet, and its call_count
    would show it even if the raise were ever swallowed.
    """
    config_write = mock.MagicMock(
        side_effect=AssertionError("harness attempted a config write")
    )
    session_write = mock.MagicMock(
        side_effect=AssertionError("harness attempted a session write")
    )
    notify = mock.MagicMock(
        side_effect=AssertionError("harness attempted a notification")
    )
    notify_session = mock.MagicMock(
        side_effect=AssertionError("harness attempted to resolve a notify session")
    )
    guards = [
        mock.patch.object(pgstore, "window_rows", return_value=[]),
        mock.patch.object(ars, "resolve_inferhub_key", return_value=""),
        mock.patch.object(ars, "update_opencrabs_config", config_write),
        mock.patch.object(ars, "update_active_sessions", session_write),
        mock.patch.object(ars, "send_session_notification", notify),
        mock.patch.object(ars, "resolve_notify_session", notify_session),
    ]
    return guards, {
        "config_write_calls": config_write,
        "session_write_calls": session_write,
        "notification_calls": notify,
        "notify_session_calls": notify_session,
    }


def mixed_hazard() -> dict[str, Any]:
    """The pre-#27 MIXED ranking: incumbent REALIZED vs challenger FLOOR."""
    inc_realized = fixture_pricing(5)["routes"][INCIDENT_MODEL]["eff_per_mtok"]
    chal_floor = FIXTURE_CATALOG[CHALLENGER]["ask_in"]
    inc_val = ars.calculate_value(
        FIXTURE_IQ, eff_price=inc_realized, tps=FIXTURE_TPS, tps_ref=FIXTURE_TPS
    )
    chal_val = ars.calculate_value(
        FIXTURE_IQ, eff_price=chal_floor, tps=FIXTURE_TPS, tps_ref=FIXTURE_TPS
    )
    gain = (chal_val - inc_val) / inc_val
    return {
        "incumbent_basis": f"realized {inc_realized}",
        "challenger_basis": f"floor {chal_floor}",
        "incumbent_value": round(inc_val, 1),
        "challenger_value": round(chal_val, 1),
        "gain_pct": round(gain * 100, 1),
        "would_switch": gain > ars.SWITCH_THRESHOLD,
    }


def ranked_values(basis_mode: str, challenger_samples: int) -> dict[str, Any]:
    """Both candidates' values on ONE basis, via the module's own math.

    This is the non-vacuity evidence: the no-switch verdict must come from
    the challenger LOSING the ranking, not from it being excluded.
    """
    realized = fixture_pricing(challenger_samples)["routes"]
    if basis_mode == ars.BASIS_REALIZED:
        inc_eff = realized[INCIDENT_MODEL]["eff_per_mtok"]
        chal_eff = realized[CHALLENGER]["eff_per_mtok"]
    else:
        inc_eff = FIXTURE_CATALOG[INCIDENT_MODEL]["ask_in"]
        chal_eff = FIXTURE_CATALOG[CHALLENGER]["ask_in"]
    inc = ars.calculate_value(
        FIXTURE_IQ, eff_price=inc_eff, tps=FIXTURE_TPS, tps_ref=FIXTURE_TPS
    )
    chal = ars.calculate_value(
        FIXTURE_IQ, eff_price=chal_eff, tps=FIXTURE_TPS, tps_ref=FIXTURE_TPS
    )
    return {
        "incumbent_value": round(inc, 1),
        "challenger_value": round(chal, 1),
        "challenger_gain_pct": round((chal - inc) / inc * 100, 1),
        "challenger_loses": chal < inc,
    }


def run_harness(repo_root: Path | None = None) -> dict[str, Any]:
    """Drive the real pipeline over fixtures; return the full receipt."""
    repo_root = repo_root or Path(__file__).resolve().parents[1]
    live_config = ars.DEFAULT_CONFIG_PATH
    config_before = live_config.read_bytes() if live_config.exists() else None

    runs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="oc-basis-shift-") as tmp:
        root = Path(tmp)
        (root / "data").mkdir(parents=True, exist_ok=True)
        (root / "data" / "catalog.json").write_text(
            json.dumps({"models": FIXTURE_CATALOG}), encoding="utf-8"
        )
        (root / "data" / "intelligence.json").write_text(
            json.dumps(FIXTURE_INTEL), encoding="utf-8"
        )
        config_path = root / "config.toml"
        config_path.write_text(FIXTURE_CONFIG, encoding="utf-8")

        guards, counters = _production_guards()
        with ExitStack() as stack:
            for guard in guards:
                stack.enter_context(guard)
            for label, samples, expected in RUN_SPECS:
                (root / "data" / "pricing.json").write_text(
                    json.dumps(fixture_pricing(samples)), encoding="utf-8"
                )
                res = ars.run_auto_route_switch(
                    root_dir=root,
                    config_path=config_path,
                    dry_run=True,
                    notify=False,
                    now=FIXED_NOW,
                )
                runs.append(
                    {
                        "run": label,
                        "expected_basis": expected,
                        "basis_mode": res.get("basis_mode"),
                        "basis_samples": res.get("basis_samples"),
                        "challenger_realized_samples": samples,
                        "current_model": res.get("current_model"),
                        "best_model": res.get("best_model"),
                        "should_switch": res.get("should_switch"),
                        "reason": res.get("reason"),
                        "dwell": (res.get("dwell") or {}).get("reason"),
                        "catalog_source": res.get("catalog_source"),
                        "challenger_qualified": ars.is_qualified(
                            CHALLENGER, FIXTURE_CATALOG[CHALLENGER], FIXTURE_IQ
                        ),
                        "ranked_on_basis": ranked_values(
                            res.get("basis_mode"), samples
                        ),
                        "config_updated": res.get("config_updated"),
                        "sessions_updated": res.get("sessions_updated"),
                        "notification_sent": res.get("notification_sent"),
                    }
                )
        state_in_tmp = (root / "data" / "switcher_state.json").exists()

    config_after = live_config.read_bytes() if live_config.exists() else None
    repo_state = repo_root / "data" / "switcher_state.json"
    basis_sequence = [r["basis_mode"] for r in runs]
    shifts = sum(
        1 for a, b in zip(basis_sequence, basis_sequence[1:]) if a != b
    )
    stable = all(r["should_switch"] is False for r in runs)
    non_vacuous = all(
        r["challenger_qualified"] and r["ranked_on_basis"]["challenger_loses"]
        for r in runs
    )
    return {
        "harness": "basis-shift stability (re-arm criterion 2)",
        "issue": "https://github.com/leshchenko1979/inferhub-watch/issues/27",
        "basis_sequence": basis_sequence,
        "shift_count": shifts,
        "no_switch_on_any_run": stable,
        "challenger_non_vacuous": non_vacuous,
        "mixed_pre_27_would_switch": mixed_hazard(),
        "production_mutation": {
            "live_config_bytes_unchanged": config_before == config_after,
            "live_config": str(live_config),
            "repo_switcher_state_present": repo_state.exists(),
            "fixture_state_written": state_in_tmp,
            "config_write_calls": counters["config_write_calls"].call_count,
            "session_write_calls": counters["session_write_calls"].call_count,
            "notification_calls": counters["notification_calls"].call_count,
            "notify_session_calls": counters["notify_session_calls"].call_count,
        },
        "runs": runs,
    }


def main() -> int:
    receipt = run_harness()
    print(json.dumps(receipt, indent=2))
    ok = (
        receipt["shift_count"] == 2
        and receipt["no_switch_on_any_run"]
        and receipt["challenger_non_vacuous"]
        and receipt["production_mutation"]["live_config_bytes_unchanged"]
        and not receipt["production_mutation"]["repo_switcher_state_present"]
    )
    print(f"\n[criterion-2] shifts={receipt['shift_count']} "
          f"stable={receipt['no_switch_on_any_run']} "
          f"non_vacuous={receipt['challenger_non_vacuous']} "
          f"mixed_would_switch={receipt['mixed_pre_27_would_switch']['would_switch']} "
          f"-> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
