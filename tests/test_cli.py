from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ppms_control.cli import _diagnose_hardware_command, _run_hardware_command, _simulate
from ppms_control.config import DataConfig, load_config
from ppms_control.diagnostics import DiagnosticResult
from ppms_control.hardware_run import HardwareRunOutcome


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "simulation.toml"


def _hardware_config():
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
    with patch.object(Path, "read_text", return_value=text):
        config = load_config(EXAMPLE_CONFIG)
    return replace(config, data=DataConfig(Path(":memory:")))


class HardwareDiagnosticCliTests(unittest.TestCase):
    def test_frequency_simulation_command_completes(self) -> None:
        config = load_config(EXAMPLE_CONFIG)
        config = replace(config, data=DataConfig(Path(":memory:")))
        output = io.StringIO()
        with patch("ppms_control.cli.load_config", return_value=config):
            with redirect_stdout(output):
                exit_code = _simulate(
                    EXAMPLE_CONFIG,
                    None,
                    sweep_kind="frequency",
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload["newly_measured_conditions"],
            config.frequency_sweep.points,
        )

    def test_temperature_field_simulation_command_completes(self) -> None:
        config = load_config(EXAMPLE_CONFIG)
        config = replace(config, data=DataConfig(Path(":memory:")))
        output = io.StringIO()
        with patch("ppms_control.cli.load_config", return_value=config):
            with redirect_stdout(output):
                exit_code = _simulate(
                    EXAMPLE_CONFIG,
                    None,
                    sweep_kind="temperature_field",
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload["newly_measured_conditions"],
            config.temperature_field_sweep.temperature_points
            * config.temperature_field_sweep.field_points,
        )

    def test_successful_diagnostic_is_audited_and_prints_run_id(self) -> None:
        config = _hardware_config()
        result = DiagnosticResult(
            component="ppms",
            endpoint="127.0.0.1:5000",
            ok=True,
            details={"temperature_k": 300.0},
        )
        output = io.StringIO()
        with patch("ppms_control.cli.load_config", return_value=config):
            with redirect_stdout(output):
                exit_code = _diagnose_hardware_command(
                    EXAMPLE_CONFIG,
                    runner=lambda loaded: (result,),
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["run_id"])
        self.assertEqual(payload["database"], ":memory:")

    def test_hardware_command_passes_authorization_inputs_to_runner(self) -> None:
        config = _hardware_config()
        output = io.StringIO()
        outcome = HardwareRunOutcome("hardware-run", 9, "completed")
        with patch("ppms_control.cli.load_config", return_value=config):
            with patch(
                "ppms_control.cli.run_authorized_voltage_sweep",
                return_value=outcome,
            ) as runner:
                with redirect_stdout(output):
                    exit_code = _run_hardware_command(
                        EXAMPLE_CONFIG,
                        diagnostic_run_id="diagnostic-run",
                        confirmation="I CONFIRM REAL HARDWARE CONTROL",
                        resume_run_id=None,
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["run_id"], "hardware-run")
        self.assertEqual(runner.call_args.kwargs["diagnostic_run_id"], "diagnostic-run")

    def test_frequency_hardware_command_selects_frequency_runner(self) -> None:
        config = _hardware_config()
        output = io.StringIO()
        outcome = HardwareRunOutcome("frequency-run", 7, "completed")
        with patch("ppms_control.cli.load_config", return_value=config):
            with patch(
                "ppms_control.cli.run_authorized_frequency_sweep",
                return_value=outcome,
            ) as runner:
                with redirect_stdout(output):
                    exit_code = _run_hardware_command(
                        EXAMPLE_CONFIG,
                        diagnostic_run_id="diagnostic-run",
                        confirmation="I CONFIRM REAL HARDWARE CONTROL",
                        resume_run_id=None,
                        sweep_kind="frequency",
                    )

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["run_id"], "frequency-run")
        runner.assert_called_once()

    def test_failed_component_returns_three_and_is_still_audited(self) -> None:
        config = _hardware_config()
        result = DiagnosticResult(
            component="sr830",
            endpoint="GPIB0::8::INSTR",
            ok=False,
            details={},
            error="timeout",
        )
        output = io.StringIO()
        with patch("ppms_control.cli.load_config", return_value=config):
            with redirect_stdout(output):
                exit_code = _diagnose_hardware_command(
                    EXAMPLE_CONFIG,
                    runner=lambda loaded: (result,),
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertFalse(payload["success"])
        self.assertTrue(payload["run_id"])


if __name__ == "__main__":
    unittest.main()
