from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ppms_control.config import load_config
from ppms_control.real_instruments import (
    MultiPyVuPPMS,
    QcodesKeithley2400Gate,
    QcodesLockinAdapter,
    RealDriverFactories,
    build_real_bundle,
)


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


class FakeParameter:
    def __init__(self, value: object) -> None:
        self.value = value
        self.set_values: list[object] = []

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value
        self.set_values.append(value)


class FakeLockinDriver:
    name = "lockin"

    def __init__(self, status: int = 0) -> None:
        self.status = status
        self.frequency = FakeParameter(17.777)
        self.X = FakeParameter(1.2e-6)
        self.Y = FakeParameter(-3.4e-7)
        self.harmonic = FakeParameter(1)
        self.amplitude = FakeParameter(1.0)
        self.reference_source = FakeParameter("external")
        self.closed = False

    def ask(self, command: str) -> str:
        if command != "LIAS?":
            raise AssertionError(command)
        return str(self.status)

    def close(self) -> None:
        self.closed = True


class FakeGateDriver:
    name = "gate"

    def __init__(self) -> None:
        self.compliancei = FakeParameter(1e-8)
        self.volt = FakeParameter(0.0)
        self.output = FakeParameter(False)
        self.curr = FakeParameter(2e-10)
        self.mode = FakeParameter("VOLT")
        self.sense = FakeParameter("CURR")
        self.closed = False

    def ask(self, command: str) -> str:
        if command != ":SOUR:VOLT:LEV?":
            raise AssertionError(command)
        return str(self.volt.value)

    def close(self) -> None:
        self.closed = True


class _EnumValue:
    pass


class _ApproachMode:
    no_overshoot = _EnumValue()
    linear = _EnumValue()


class _DrivenMode:
    driven = _EnumValue()


class _Temperature:
    waitfor = 1
    approach_mode = _ApproachMode()


class _Field:
    waitfor = 2
    approach_mode = _ApproachMode()
    driven_mode = _DrivenMode()


class FakePPMSClient:
    temperature = _Temperature()
    field = _Field()

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.temperature_set: tuple[object, ...] | None = None
        self.field_set: tuple[object, ...] | None = None

    def open(self) -> None:
        self.opened = True

    def close_client(self) -> None:
        self.closed = True

    def get_temperature(self) -> tuple[float, str]:
        return 10.0, "Stable"

    def get_version(self) -> str:
        return "3.6.1"

    def get_field(self) -> tuple[float, str]:
        return 25000.0, "Holding"

    def get_chamber(self) -> str:
        return "Sealed"

    def get_position(self) -> tuple[float, str]:
        return 12.5, "Holding"

    def is_steady(self, bitmask: int) -> bool:
        return bitmask == 3

    def set_temperature(self, *args: object) -> None:
        self.temperature_set = args

    def set_field(self, *args: object) -> None:
        self.field_set = args


class RealInstrumentAdapterTests(unittest.TestCase):
    def test_lockin_status_bits_are_preserved(self) -> None:
        clean_driver = FakeLockinDriver(status=0)
        clean = QcodesLockinAdapter(clean_driver)
        clean.set_harmonic(2)
        reading = clean.acquire(0.05)
        self.assertEqual(clean_driver.harmonic.set_values, [2])
        self.assertEqual(reading.harmonic, 2)
        self.assertTrue(reading.reference_locked)
        self.assertFalse(reading.overload)

        bad = QcodesLockinAdapter(FakeLockinDriver(status=0b1001))
        reading = bad.acquire(0.05)
        self.assertFalse(reading.reference_locked)
        self.assertTrue(reading.overload)

        source_driver = FakeLockinDriver()
        source = QcodesLockinAdapter(source_driver, safe_idle_voltage_v=0.004)
        source.set_source_frequency(31.25)
        self.assertEqual(source_driver.frequency.value, 31.25)
        self.assertEqual(source.read_source_frequency(), 31.25)

    def test_gate_adapter_maps_voltage_output_and_leakage(self) -> None:
        driver = FakeGateDriver()
        gate = QcodesKeithley2400Gate(driver)
        gate.set_compliance(5e-9)
        gate.set_output(True)
        gate.set_voltage(0.25)
        self.assertEqual(driver.compliancei.value, 5e-9)
        self.assertIs(driver.output.value, True)
        self.assertEqual(driver.volt.value, 0.25)
        self.assertEqual(gate.measure_leakage(), 2e-10)
        state = gate.read_state()
        self.assertEqual(state.source_voltage_v, 0.25)
        self.assertTrue(state.output_enabled)
        self.assertEqual(state.measured_current_a, 2e-10)
        gate.set_output(False)
        self.assertIsNone(gate.read_state().measured_current_a)

    def test_multipyvu_adapter_converts_field_units_and_setpoints(self) -> None:
        client = FakePPMSClient()
        ppms = MultiPyVuPPMS.connect(
            "127.0.0.1",
            5000,
            client_factory=lambda host, port: client,
        )
        self.assertTrue(client.opened)
        self.assertEqual(ppms.read_temperature(), 10.0)
        self.assertEqual(ppms.read_field(), 2.5)
        self.assertTrue(ppms.is_stable())
        state = ppms.read_state()
        self.assertEqual(state.sample_position_deg, 12.5)
        self.assertEqual(state.position_status, "Holding")

        ppms.set_temperature(20.0, 1.5)
        ppms.set_field(1.2, 0.01)
        self.assertEqual(client.temperature_set[:2], (20.0, 1.5))
        self.assertEqual(client.field_set[:2], (12000.0, 100.0))
        ppms.close()
        ppms.close()
        self.assertTrue(client.closed)

    def test_real_bundle_uses_sr830_as_voltage_source_at_safe_idle(self) -> None:
        config = _hardware_config()
        sr830 = FakeLockinDriver()
        sr865a = FakeLockinDriver()
        gates = [FakeGateDriver(), FakeGateDriver()]
        ppms_client = FakePPMSClient()
        gate_index = 0

        def make_gate(*args: object, **kwargs: object) -> FakeGateDriver:
            nonlocal gate_index
            gate = gates[gate_index]
            gate_index += 1
            return gate

        factories = RealDriverFactories(
            sr830=lambda *args, **kwargs: sr830,
            sr865a=lambda *args, **kwargs: sr865a,
            keithley2400=make_gate,
            station=lambda *components: object(),
        )
        bundle = build_real_bundle(
            config,
            factories=factories,
            ppms_client_factory=lambda host, port: ppms_client,
        )

        self.assertIs(bundle.excitation, bundle.sr830)
        self.assertEqual(sr830.amplitude.value, config.safety.source_safe_idle_voltage_v)
        self.assertEqual(sr830.reference_source.value, "internal")
        self.assertEqual(sr830.frequency.value, config.instruments.reference_frequency_hz)
        self.assertEqual(sr865a.reference_source.value, "EXT")
        self.assertEqual(sr830.harmonic.value, 1)
        self.assertEqual(sr865a.harmonic.value, 1)
        for gate in gates:
            self.assertFalse(gate.output.value)
            self.assertEqual(gate.volt.value, 0.0)
            self.assertEqual(gate.mode.value, "VOLT")
            self.assertEqual(gate.sense.value, "CURR")
        bundle.close()
        self.assertTrue(sr830.closed)
        self.assertTrue(sr865a.closed)
        self.assertTrue(all(gate.closed for gate in gates))
        self.assertTrue(ppms_client.closed)

    def test_real_bundle_failure_retreats_created_outputs(self) -> None:
        config = _hardware_config()
        sr830 = FakeLockinDriver()
        sr865a = FakeLockinDriver()
        first_gate = FakeGateDriver()
        first_gate.output.value = True
        calls = 0

        def make_gate(*args: object, **kwargs: object) -> FakeGateDriver:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second gate unavailable")
            return first_gate

        factories = RealDriverFactories(
            sr830=lambda *args, **kwargs: sr830,
            sr865a=lambda *args, **kwargs: sr865a,
            keithley2400=make_gate,
            station=lambda *components: object(),
        )

        with self.assertRaisesRegex(RuntimeError, "second gate unavailable"):
            build_real_bundle(config, factories=factories)

        self.assertEqual(
            sr830.amplitude.set_values,
            [config.safety.source_safe_idle_voltage_v] * 2,
        )
        self.assertFalse(first_gate.output.value)
        self.assertEqual(first_gate.volt.value, 0.0)
        self.assertTrue(first_gate.closed)
        self.assertTrue(sr830.closed)
        self.assertTrue(sr865a.closed)


if __name__ == "__main__":
    unittest.main()
