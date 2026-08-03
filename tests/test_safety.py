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

    def test_over_limit_current_fails_before_driver_call(self) -> None:
        self.assertEqual(float(self.bundle.sr830.source_current_a.get()), 0.0)
        with self.assertRaisesRegex(SafetyViolation, "normal current limit"):
            self.station.set_excitation_current(self.config.safety.normal_current_limit_a * 2)
        self.assertEqual(float(self.bundle.sr830.source_current_a.get()), 0.0)

    def test_nonzero_gate_is_forbidden_at_room_temperature(self) -> None:
        with self.assertRaisesRegex(SafetyViolation, "current temperature"):
            self.station.set_gates(0.1, 0.0)
        self.assertFalse(bool(self.bundle.gate_top.output_enabled.get()))
        self.assertFalse(bool(self.bundle.gate_bottom.output_enabled.get()))

    def test_cleanup_continues_after_one_gate_fails(self) -> None:
        self.station.set_excitation_current(1e-5)
        self.bundle.gate_bottom.set_output(True)
        self.bundle.gate_bottom.set_voltage(0.5)
        with patch.object(self.bundle.gate_top, "set_voltage", side_effect=RuntimeError("boom")):
            errors = self.station.safe_shutdown()
        self.assertEqual([error.step for error in errors], ["top_gate_zero"])
        self.assertEqual(float(self.bundle.sr830.source_current_a.get()), 0.0)
        self.assertEqual(float(self.bundle.gate_bottom.voltage_v.get()), 0.0)
        self.assertFalse(bool(self.bundle.gate_bottom.output_enabled.get()))


if __name__ == "__main__":
    unittest.main()
