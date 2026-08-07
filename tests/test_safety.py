from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ppms_control.config import load_config
from ppms_control.instruments import build_simulated_bundle
from ppms_control.safety import SafeStation, SafetyViolation


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "config" / "simulation.toml")
        self.bundle = build_simulated_bundle(self.config)
        self.station = SafeStation(self.bundle, self.config)

    def tearDown(self) -> None:
        self.station.safe_shutdown()
        self.bundle.close()

    def test_over_limit_voltage_fails_before_driver_call(self) -> None:
        self.assertEqual(
            float(self.bundle.sr830.source_voltage_v.get()),
            self.config.safety.source_safe_idle_voltage_v,
        )
        with self.assertRaisesRegex(SafetyViolation, "source voltage"):
            self.station.set_excitation_voltage(self.config.safety.source_voltage_max_v * 2)
        self.assertEqual(
            float(self.bundle.sr830.source_voltage_v.get()),
            self.config.safety.source_safe_idle_voltage_v,
        )

    def test_environment_limits_fail_before_ppms_driver_call(self) -> None:
        with patch.object(self.bundle.ppms, "set_temperature") as set_temperature:
            with self.assertRaisesRegex(SafetyViolation, "temperature rate"):
                self.station.set_temperature(
                    10.0,
                    self.config.safety.temperature_rate_max_k_per_min * 2,
                )
            set_temperature.assert_not_called()

        with patch.object(self.bundle.ppms, "set_field") as set_field:
            with self.assertRaisesRegex(SafetyViolation, "field exceeds"):
                self.station.set_field(self.config.safety.field_abs_limit_t * 2, 0.001)
            set_field.assert_not_called()

    def test_station_owned_field_is_zeroed_during_shutdown(self) -> None:
        self.station.set_field(0.5, 0.005)
        self.assertEqual(float(self.bundle.ppms.field_t.get()), 0.5)
        errors = self.station.safe_shutdown()
        self.assertEqual(errors, ())
        self.assertEqual(float(self.bundle.ppms.field_t.get()), 0.0)

    def test_nonzero_gate_is_forbidden_at_room_temperature(self) -> None:
        with self.assertRaisesRegex(SafetyViolation, "current temperature"):
            self.station.set_gates(0.1, 0.0)
        self.assertFalse(bool(self.bundle.gate_top.output_enabled.get()))
        self.assertFalse(bool(self.bundle.gate_bottom.output_enabled.get()))

    def test_cleanup_continues_after_one_gate_fails(self) -> None:
        self.station.set_excitation_voltage(0.05)
        self.bundle.gate_top.set_output(True)
        self.bundle.gate_top.set_voltage(0.5)
        self.bundle.gate_bottom.set_output(True)
        self.bundle.gate_bottom.set_voltage(0.5)
        with patch.object(self.bundle.gate_top, "set_voltage", side_effect=RuntimeError("boom")):
            errors = self.station.safe_shutdown()
        self.assertEqual([error.step for error in errors], ["top_gate_zero"])
        self.assertEqual(
            float(self.bundle.sr830.source_voltage_v.get()),
            self.config.safety.source_safe_idle_voltage_v,
        )
        self.assertFalse(bool(self.bundle.gate_top.output_enabled.get()))
        self.assertEqual(float(self.bundle.gate_bottom.voltage_v.get()), 0.0)
        self.assertFalse(bool(self.bundle.gate_bottom.output_enabled.get()))

    def test_gate_driver_failure_triggers_full_shutdown(self) -> None:
        self.station.set_excitation_voltage(0.05)
        self.bundle.gate_top.set_output(True)
        with patch.object(
            self.bundle.gate_bottom,
            "set_voltage",
            side_effect=RuntimeError("driver failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "driver failure"):
                self.station.set_gates(0.0, 0.0)
        self.assertEqual(
            float(self.bundle.sr830.source_voltage_v.get()),
            self.config.safety.source_safe_idle_voltage_v,
        )
        self.assertFalse(bool(self.bundle.gate_top.output_enabled.get()))
        self.assertFalse(bool(self.bundle.gate_bottom.output_enabled.get()))

    def test_sr830_is_both_excitation_source_and_first_harmonic_lockin(self) -> None:
        self.assertIs(self.bundle.excitation, self.bundle.sr830)
        self.station.set_excitation_voltage(0.05)
        self.assertEqual(float(self.bundle.sr830.source_voltage_v.get()), 0.05)
        self.station.safe_shutdown()
        self.assertEqual(
            float(self.bundle.sr830.source_voltage_v.get()),
            self.config.safety.source_safe_idle_voltage_v,
        )


if __name__ == "__main__":
    unittest.main()
