from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from ppms_control.authorization import REAL_CONTROL_CONFIRMATION, AuthorizationError
from ppms_control.config import load_config
from ppms_control.hardware_run import (
    run_authorized_field_sweep,
    run_authorized_frequency_sweep,
    run_authorized_gate_sweep,
    run_authorized_temperature_field_sweep,
    run_authorized_voltage_sweep,
)
from ppms_control.instruments import build_simulated_bundle
from ppms_control.store import RunStore


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


def _diagnostic_run(store: RunStore, config) -> str:
    run_id = store.start_run(
        protocol="read_only_hardware_diagnostic",
        sample_name=config.runtime.sample_name,
        config_json=config.canonical_json(),
        station_snapshot_json="{}",
    )
    store.finish_run(run_id, "completed")
    return run_id


class AuthorizedHardwareRunTests(unittest.TestCase):
    def test_matching_diagnostic_runs_full_audited_sweep(self) -> None:
        config = _hardware_config()

        def bundle_factory(loaded):
            return build_simulated_bundle(loaded)

        with RunStore(":memory:") as store:
            diagnostic_run_id = _diagnostic_run(store, config)
            outcome = run_authorized_voltage_sweep(
                config,
                store,
                confirmation=REAL_CONTROL_CONFIRMATION,
                diagnostic_run_id=diagnostic_run_id,
                bundle_factory=bundle_factory,
            )
            self.assertEqual(store.run_status(outcome.run_id), "completed")
            self.assertEqual(
                store.attempt_count(outcome.run_id, accepted=True),
                config.voltage_sweep.points,
            )
            self.assertEqual(
                store.instrument_sample_count(outcome.run_id),
                config.voltage_sweep.points * config.acquisition.averages * 3,
            )
            self.assertEqual(
                store.transport_reading_count(outcome.run_id),
                config.voltage_sweep.points * config.acquisition.averages * 6,
            )

        self.assertEqual(outcome.newly_measured_conditions, config.voltage_sweep.points)

    def test_matching_diagnostic_runs_authorized_frequency_sweep(self) -> None:
        config = _hardware_config()

        with RunStore(":memory:") as store:
            diagnostic_run_id = _diagnostic_run(store, config)
            outcome = run_authorized_frequency_sweep(
                config,
                store,
                confirmation=REAL_CONTROL_CONFIRMATION,
                diagnostic_run_id=diagnostic_run_id,
                bundle_factory=build_simulated_bundle,
            )
            self.assertEqual(store.run_status(outcome.run_id), "completed")
            self.assertEqual(
                store.attempt_count(outcome.run_id, accepted=True),
                config.frequency_sweep.points,
            )
            rows = store._connection.execute(
                """
                SELECT DISTINCT source_frequency_hz
                FROM attempts
                WHERE run_id = ?
                ORDER BY source_frequency_hz
                """,
                (outcome.run_id,),
            ).fetchall()
            self.assertEqual(len(rows), config.frequency_sweep.points)

        self.assertEqual(outcome.newly_measured_conditions, config.frequency_sweep.points)

    def test_matching_diagnostic_runs_authorized_field_sweep(self) -> None:
        config = _hardware_config()

        with RunStore(":memory:") as store:
            diagnostic_run_id = _diagnostic_run(store, config)
            outcome = run_authorized_field_sweep(
                config,
                store,
                confirmation=REAL_CONTROL_CONFIRMATION,
                diagnostic_run_id=diagnostic_run_id,
                bundle_factory=build_simulated_bundle,
            )
            self.assertEqual(store.run_status(outcome.run_id), "completed")
            self.assertEqual(
                store.attempt_count(outcome.run_id, accepted=True),
                config.field_sweep.points,
            )

        self.assertEqual(outcome.newly_measured_conditions, config.field_sweep.points)

    def test_matching_diagnostic_runs_authorized_temperature_field_sweep(self) -> None:
        config = _hardware_config()

        with RunStore(":memory:") as store:
            diagnostic_run_id = _diagnostic_run(store, config)
            outcome = run_authorized_temperature_field_sweep(
                config,
                store,
                confirmation=REAL_CONTROL_CONFIRMATION,
                diagnostic_run_id=diagnostic_run_id,
                bundle_factory=build_simulated_bundle,
            )
            expected_points = (
                config.temperature_field_sweep.temperature_points
                * config.temperature_field_sweep.field_points
            )
            self.assertEqual(store.run_status(outcome.run_id), "completed")
            self.assertEqual(
                store.attempt_count(outcome.run_id, accepted=True),
                expected_points,
            )

        self.assertEqual(outcome.newly_measured_conditions, expected_points)

    def test_matching_diagnostic_runs_authorized_gate_sweep(self) -> None:
        config = _hardware_config()

        with RunStore(":memory:") as store:
            diagnostic_run_id = _diagnostic_run(store, config)
            outcome = run_authorized_gate_sweep(
                config,
                store,
                confirmation=REAL_CONTROL_CONFIRMATION,
                diagnostic_run_id=diagnostic_run_id,
                bundle_factory=build_simulated_bundle,
            )
            expected_points = (
                config.gate_sweep.top_gate_points
                * config.gate_sweep.bottom_gate_points
            )
            self.assertEqual(store.run_status(outcome.run_id), "completed")
            self.assertEqual(
                store.attempt_count(outcome.run_id, accepted=True),
                expected_points,
            )

        self.assertEqual(outcome.newly_measured_conditions, expected_points)

    def test_bad_confirmation_prevents_bundle_creation(self) -> None:
        config = _hardware_config()
        bundle_factory = Mock()
        with RunStore(":memory:") as store:
            diagnostic_run_id = _diagnostic_run(store, config)
            with self.assertRaises(AuthorizationError):
                run_authorized_voltage_sweep(
                    config,
                    store,
                    confirmation="wrong",
                    diagnostic_run_id=diagnostic_run_id,
                    bundle_factory=bundle_factory,
                )
        bundle_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
