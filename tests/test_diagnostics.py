from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from ppms_control.config import load_config
from ppms_control.diagnostics import DiagnosticError, diagnose_hardware


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
        return load_config(EXAMPLE_CONFIG)


class FakeVisaResource:
    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.timeout = 0
        self.queries: list[str] = []
        self.closed = False

    def query(self, command: str) -> str:
        self.queries.append(command)
        return self.replies[command]

    def close(self) -> None:
        self.closed = True


class FakeVisaManager:
    def __init__(self, resources: dict[str, FakeVisaResource]) -> None:
        self.resources = resources
        self.closed = False

    def open_resource(self, address: str) -> FakeVisaResource:
        return self.resources[address]

    def close(self) -> None:
        self.closed = True


class _WaitFor:
    def __init__(self, waitfor: int) -> None:
        self.waitfor = waitfor


class FakePPMSClient:
    temperature = _WaitFor(1)
    field = _WaitFor(2)

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.steady_bitmask: int | None = None

    def is_server_running(self) -> bool:
        return True

    def open(self) -> object:
        self.opened = True
        return self

    def get_version(self) -> str:
        return "3.6.1"

    def get_temperature(self) -> tuple[float, str]:
        return 300.0, "Stable"

    def get_field(self) -> tuple[float, str]:
        return 10000.0, "Holding"

    def get_chamber(self) -> str:
        return "Sealed"

    def get_position(self) -> tuple[float, str]:
        return 22.5, "Holding"

    def is_steady(self, bitmask: int = 0) -> bool:
        self.steady_bitmask = bitmask
        return True

    def close_client(self) -> None:
        self.closed = True


def _visa_resources() -> dict[str, FakeVisaResource]:
    lockin_common = {
        "FREQ?": "17.777\n",
        "HARM?": "1\n",
        "SLVL?": "0.100\n",
    }
    gate_common = {
        ":OUTP:STAT?": "0\n",
        ":SOUR:FUNC?": "VOLT\n",
        ":SOUR:VOLT:LEV?": "0.0\n",
        ":SENS:CURR:PROT?": "1e-8\n",
    }
    return {
        "GPIB0::8::INSTR": FakeVisaResource(
            {"*IDN?": "Stanford Research Systems,SR830,1,1.0\n", "FMOD?": "0\n", **lockin_common}
        ),
        "GPIB0::4::INSTR": FakeVisaResource(
            {"*IDN?": "Stanford Research Systems,SR865A,2,1.0\n", "RSRC?": "1\n", **lockin_common}
        ),
        "GPIB0::24::INSTR": FakeVisaResource(
            {"*IDN?": "Keithley Instruments Inc.,MODEL 2400,3,1.0\n", **gate_common}
        ),
        "GPIB0::25::INSTR": FakeVisaResource(
            {"*IDN?": "Keithley Instruments Inc.,MODEL 2400,4,1.0\n", **gate_common}
        ),
    }


class HardwareDiagnosticTests(unittest.TestCase):
    def test_read_only_diagnostic_queries_all_components_and_closes(self) -> None:
        config = _hardware_config()
        resources = _visa_resources()
        manager = FakeVisaManager(resources)
        ppms = FakePPMSClient()

        results = diagnose_hardware(
            config,
            visa_factory=lambda backend: manager,
            ppms_factory=lambda host, port: ppms,
        )

        self.assertEqual(len(results), 5)
        self.assertTrue(all(result.ok for result in results))
        self.assertTrue(manager.closed)
        self.assertTrue(all(resource.closed for resource in resources.values()))
        self.assertTrue(ppms.opened)
        self.assertTrue(ppms.closed)
        self.assertEqual(ppms.steady_bitmask, 3)
        self.assertEqual(results[-1].details["field_t"], 1.0)
        self.assertEqual(results[-1].details["sample_position_deg"], 22.5)
        self.assertTrue(results[-1].details["rotator_available"])
        self.assertTrue(all("*IDN?" in resource.queries for resource in resources.values()))

    def test_identity_mismatch_fails_only_that_component(self) -> None:
        config = _hardware_config()
        resources = _visa_resources()
        resources["GPIB0::8::INSTR"].replies["*IDN?"] = "WRONG MODEL\n"
        manager = FakeVisaManager(resources)

        results = diagnose_hardware(
            config,
            visa_factory=lambda backend: manager,
            ppms_factory=lambda host, port: FakePPMSClient(),
        )

        self.assertFalse(results[0].ok)
        self.assertIn("Identity mismatch", results[0].error or "")
        self.assertTrue(all(result.ok for result in results[1:]))

    def test_simulation_configuration_cannot_run_hardware_diagnostics(self) -> None:
        config = load_config(EXAMPLE_CONFIG)
        with self.assertRaisesRegex(DiagnosticError, "simulation = false"):
            diagnose_hardware(config)


if __name__ == "__main__":
    unittest.main()
