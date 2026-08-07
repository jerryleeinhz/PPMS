from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ppms_control.config import ConfigError, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "simulation.toml"


def _hardware_text() -> str:
    text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
        "simulation = true", "simulation = false"
    )
    text = text.replace('sample_name = "SIMULATED_SAMPLE"', 'sample_name = "LAB_TEST_SAMPLE"')
    replacements = {
        "SIMULATED::SR830": "GPIB0::8::INSTR",
        "SIMULATED::SR865A": "GPIB0::4::INSTR",
        "SIMULATED::KEITHLEY2400_TOP": "GPIB0::24::INSTR",
        "SIMULATED::KEITHLEY2400_BOTTOM": "GPIB0::25::INSTR",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


class ConfigTests(unittest.TestCase):
    def test_example_configuration_is_valid(self) -> None:
        config = load_config(EXAMPLE_CONFIG)
        self.assertTrue(config.runtime.simulation)
        self.assertEqual(config.voltage_sweep.points, 9)
        self.assertEqual(config.safety.source_safe_idle_voltage_v, 0.004)
        self.assertEqual(config.frequency_sweep.points, 7)
        self.assertEqual(
            config.gate_sweep.top_gate_points * config.gate_sweep.bottom_gate_points,
            6,
        )

    def test_unknown_field_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "seed = 1729", "seed = 1729\nunknown = 1"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "unknown"):
                load_config(EXAMPLE_CONFIG)

    def test_real_mode_rejects_placeholder_addresses(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "simulation = true", "simulation = false"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "placeholder"):
                load_config(EXAMPLE_CONFIG)

    def test_hardware_configuration_can_be_validated_without_connecting(self) -> None:
        with patch.object(Path, "read_text", return_value=_hardware_text()):
            config = load_config(EXAMPLE_CONFIG)
        self.assertFalse(config.runtime.simulation)
        self.assertEqual(config.connections.ppms_host, "127.0.0.1")
        self.assertEqual(config.connections.ppms_port, 5000)

    def test_ppms_port_outside_tcp_range_is_rejected(self) -> None:
        text = _hardware_text().replace("ppms_port = 5000", "ppms_port = 70000")
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "<= 65535"):
                load_config(EXAMPLE_CONFIG)

    def test_hardware_sample_placeholder_is_rejected(self) -> None:
        text = _hardware_text().replace("LAB_TEST_SAMPLE", "CHANGE_ME_SAMPLE")
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "sample_name.*placeholder"):
                load_config(EXAMPLE_CONFIG)

    def test_duplicate_multi_point_setpoints_are_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "stop_voltage_v = 0.05", "stop_voltage_v = 0.004"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "distinct setpoints"):
                load_config(EXAMPLE_CONFIG)

    def test_negative_sr830_amplitude_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "start_voltage_v = 0.004", "start_voltage_v = -0.004"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, ">= 0"):
                load_config(EXAMPLE_CONFIG)

    def test_safe_idle_must_respect_estimated_current_limit(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "estimated_current_limit_a = 0.00005",
            "estimated_current_limit_a = 1e-9",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "safe-idle.*estimated current"):
                load_config(EXAMPLE_CONFIG)

    def test_source_range_cannot_exceed_sr830_hardware_range(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "source_voltage_min_v = 0.004",
            "source_voltage_min_v = 0.001",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "0.004 to 5.0"):
                load_config(EXAMPLE_CONFIG)

    def test_frequency_sweep_outside_source_range_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "stop_frequency_hz = 100.0",
            "stop_frequency_hz = 200000.0",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "frequency_sweep.stop_frequency_hz"):
                load_config(EXAMPLE_CONFIG)

    def test_field_sweep_outside_safety_range_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "stop_field_t = 1.0",
            "stop_field_t = 3.0",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "field_sweep.stop_field_t"):
                load_config(EXAMPLE_CONFIG)

    def test_temperature_field_grid_outside_temperature_range_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "start_temperature_k = 295.0",
            "start_temperature_k = 1.0",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(
                ConfigError,
                "temperature_field_sweep.start_temperature_k",
            ):
                load_config(EXAMPLE_CONFIG)

    def test_gate_sweep_outside_voltage_limit_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "stop_top_gate_v = 1.0",
            "stop_top_gate_v = 11.0",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "stop_top_gate_v"):
                load_config(EXAMPLE_CONFIG)

    def test_nonzero_gate_sweep_above_temperature_limit_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "target_temperature_k = 10.0",
            "target_temperature_k = 21.0",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "gate-temperature limit"):
                load_config(EXAMPLE_CONFIG)

    def test_duplicate_top_gate_setpoints_are_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "stop_top_gate_v = 1.0",
            "stop_top_gate_v = -1.0",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "top-gate sweep.*distinct"):
                load_config(EXAMPLE_CONFIG)

    def test_unknown_gate_sweep_mode_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            'mode = "grid"',
            'mode = "diagonal"',
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "mode must be"):
                load_config(EXAMPLE_CONFIG)

    def test_paired_gate_sweep_requires_equal_point_counts(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            'mode = "grid"',
            'mode = "paired"',
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "equal top/bottom"):
                load_config(EXAMPLE_CONFIG)

    def test_gate_leakage_abort_must_precede_hardware_compliance(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "gate_leakage_limit_a = 1e-9",
            "gate_leakage_limit_a = 1e-8",
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "below gate_compliance"):
                load_config(EXAMPLE_CONFIG)


if __name__ == "__main__":
    unittest.main()
