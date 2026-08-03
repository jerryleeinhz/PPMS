from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ppms_control.config import ConfigError, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "simulation.toml"


class ConfigTests(unittest.TestCase):
    def test_example_configuration_is_valid(self) -> None:
        config = load_config(EXAMPLE_CONFIG)
        self.assertTrue(config.runtime.simulation)
        self.assertEqual(config.current_sweep.points, 9)

    def test_unknown_field_is_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "seed = 1729", "seed = 1729\nunknown = 1"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "unknown"):
                load_config(EXAMPLE_CONFIG)

    def test_real_mode_is_rejected_in_v1(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "simulation = true", "simulation = false"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "not implemented"):
                load_config(EXAMPLE_CONFIG)

    def test_duplicate_multi_point_setpoints_are_rejected(self) -> None:
        text = EXAMPLE_CONFIG.read_text(encoding="utf-8").replace(
            "stop_current_a = 0.00008", "stop_current_a = -0.00008"
        )
        with patch.object(Path, "read_text", return_value=text):
            with self.assertRaisesRegex(ConfigError, "distinct setpoints"):
                load_config(EXAMPLE_CONFIG)


if __name__ == "__main__":
    unittest.main()
