from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from ppms_control.authorization import (
    REAL_CONTROL_CONFIRMATION,
    AuthorizationError,
    authorize_real_control,
)
from ppms_control.config import load_config
from ppms_control.store import RunStore, StoreError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "simulation.toml"


def _hardware_config():
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8")
    replacements = {
        "simulation = true": "simulation = false",
        'sample_name = "SIMULATED_SAMPLE"': 'sample_name = "LAB_TEST_SAMPLE"',
        "SIMULATED::SR830": "GPIB0::8::INSTR",
        "SIMULATED::SR865A": "GPIB0::4::INSTR",
        "SIMULATED::KEITHLEY2400_TOP": "GPIB0::24::INSTR",
        "SIMULATED::KEITHLEY2400_BOTTOM": "GPIB0::25::INSTR",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    with patch.object(Path, "read_text", return_value=text):
        return load_config(EXAMPLE_CONFIG)


def _diagnostic_run(store: RunStore, config, status: str = "completed") -> str:
    run_id = store.start_run(
        protocol="read_only_hardware_diagnostic",
        sample_name=config.runtime.sample_name,
        config_json=config.canonical_json(),
        station_snapshot_json="{}",
    )
    store.finish_run(run_id, status)
    return run_id


class HardwareAuthorizationTests(unittest.TestCase):
    def test_exact_confirmation_and_matching_diagnostic_authorize(self) -> None:
        config = _hardware_config()
        with RunStore(":memory:") as store:
            run_id = _diagnostic_run(store, config)
            authorization = authorize_real_control(
                config,
                store,
                confirmation=REAL_CONTROL_CONFIRMATION,
                diagnostic_run_id=run_id,
            )
        self.assertEqual(authorization.diagnostic_run_id, run_id)

    def test_wrong_confirmation_fails_before_diagnostic_lookup(self) -> None:
        config = _hardware_config()
        with RunStore(":memory:") as store:
            with self.assertRaisesRegex(AuthorizationError, "exact"):
                authorize_real_control(
                    config,
                    store,
                    confirmation="yes",
                    diagnostic_run_id="missing",
                )

    def test_failed_diagnostic_cannot_authorize_control(self) -> None:
        config = _hardware_config()
        with RunStore(":memory:") as store:
            run_id = _diagnostic_run(store, config, status="failed")
            with self.assertRaisesRegex(StoreError, "did not complete"):
                authorize_real_control(
                    config,
                    store,
                    confirmation=REAL_CONTROL_CONFIRMATION,
                    diagnostic_run_id=run_id,
                )

    def test_changed_configuration_invalidates_diagnostic(self) -> None:
        config = _hardware_config()
        changed = replace(
            config,
            runtime=replace(config.runtime, sample_name="DIFFERENT_SAMPLE"),
        )
        with RunStore(":memory:") as store:
            run_id = _diagnostic_run(store, config)
            with self.assertRaisesRegex(StoreError, "does not match"):
                authorize_real_control(
                    changed,
                    store,
                    confirmation=REAL_CONTROL_CONFIRMATION,
                    diagnostic_run_id=run_id,
                )


if __name__ == "__main__":
    unittest.main()
