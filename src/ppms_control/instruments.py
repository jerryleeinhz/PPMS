from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Protocol
from uuid import uuid4

from qcodes.instrument import Instrument
from qcodes.parameters import ManualParameter
from qcodes.station import Station
from qcodes.validators import Bool, Numbers

from ppms_control.config import AppConfig
from ppms_control.models import LockinPairReading, LockinReading


class LockinInstrument(Protocol):
    name: str

    def reference_status(self) -> tuple[bool, float]: ...

    def acquire(self, current_a: float) -> LockinReading: ...

    def set_source_current(self, current_a: float) -> None: ...

    def close(self) -> None: ...


class GateInstrument(Protocol):
    name: str

    def set_compliance(self, compliance_a: float) -> None: ...

    def set_voltage(self, voltage_v: float) -> None: ...

    def set_output(self, enabled: bool) -> None: ...

    def measure_leakage(self) -> float: ...

    def close(self) -> None: ...


class PPMSInstrument(Protocol):
    name: str

    def read_temperature(self) -> float: ...

    def read_field(self) -> float: ...

    def is_stable(self) -> bool: ...

    def close(self) -> None: ...


class SimulatedLockin(Instrument):
    def __init__(
        self,
        name: str,
        *,
        role: str,
        harmonic: int,
        reference_frequency_hz: float,
        seed: int,
        can_source: bool,
    ) -> None:
        super().__init__(name)
        if role not in {"xx", "xy"}:
            raise ValueError(f"Unsupported simulated lock-in role: {role}")
        self.role = role
        self.harmonic = harmonic
        self._rng = random.Random(seed)
        self._noise_v = 2e-9
        self._can_source = can_source

        self.reference_frequency_hz: ManualParameter = self.add_parameter(
            "reference_frequency_hz",
            parameter_class=ManualParameter,
            initial_value=reference_frequency_hz,
            unit="Hz",
            vals=Numbers(0, 1e6),
        )
        self.reference_locked: ManualParameter = self.add_parameter(
            "reference_locked",
            parameter_class=ManualParameter,
            initial_value=True,
            vals=Bool(),
        )
        self.force_overload: ManualParameter = self.add_parameter(
            "force_overload",
            parameter_class=ManualParameter,
            initial_value=False,
            vals=Bool(),
        )
        self.source_current_a: ManualParameter = self.add_parameter(
            "source_current_a",
            parameter_class=ManualParameter,
            initial_value=0.0,
            unit="A",
            vals=Numbers(-0.01, 0.01),
        )

    def get_idn(self) -> dict[str, str | None]:
        model = "SR830-SIM" if self.role == "xx" else "SR865A-SIM"
        return {"vendor": "QCoDeS simulation", "model": model, "serial": self.name, "firmware": "0.1"}

    def reference_status(self) -> tuple[bool, float]:
        return bool(self.reference_locked.get()), float(self.reference_frequency_hz.get())

    def set_source_current(self, current_a: float) -> None:
        if not self._can_source and current_a != 0:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        self.source_current_a.set(float(current_a))

    def acquire(self, current_a: float) -> LockinReading:
        if self.role == "xx":
            in_phase = 100.0 * current_a
            phase_deg = 3.0
        else:
            in_phase = 1.0e8 * current_a**3
            phase_deg = -7.0
        phase_rad = math.radians(phase_deg)
        x_v = in_phase * math.cos(phase_rad) + self._rng.gauss(0.0, self._noise_v)
        y_v = in_phase * math.sin(phase_rad) + self._rng.gauss(0.0, self._noise_v)
        locked, frequency_hz = self.reference_status()
        return LockinReading(
            x_v=x_v,
            y_v=y_v,
            frequency_hz=frequency_hz,
            reference_locked=locked,
            overload=bool(self.force_overload.get()),
        )


class SimulatedKeithley2400(Instrument):
    def __init__(self, name: str, *, leakage_slope_a_per_v: float) -> None:
        super().__init__(name)
        self._leakage_slope_a_per_v = leakage_slope_a_per_v
        self.voltage_v: ManualParameter = self.add_parameter(
            "voltage_v",
            parameter_class=ManualParameter,
            initial_value=0.0,
            unit="V",
            vals=Numbers(-210, 210),
        )
        self.output_enabled: ManualParameter = self.add_parameter(
            "output_enabled",
            parameter_class=ManualParameter,
            initial_value=False,
            vals=Bool(),
        )
        self.compliance_a: ManualParameter = self.add_parameter(
            "compliance_a",
            parameter_class=ManualParameter,
            initial_value=1e-8,
            unit="A",
            vals=Numbers(1e-12, 1.0),
        )

    def get_idn(self) -> dict[str, str | None]:
        return {
            "vendor": "QCoDeS simulation",
            "model": "Keithley2400-SIM",
            "serial": self.name,
            "firmware": "0.1",
        }

    def set_compliance(self, compliance_a: float) -> None:
        self.compliance_a.set(float(compliance_a))

    def set_voltage(self, voltage_v: float) -> None:
        self.voltage_v.set(float(voltage_v))

    def set_output(self, enabled: bool) -> None:
        self.output_enabled.set(bool(enabled))

    def measure_leakage(self) -> float:
        if not bool(self.output_enabled.get()):
            return 0.0
        return 2e-12 + abs(float(self.voltage_v.get())) * self._leakage_slope_a_per_v


class SimulatedPPMS(Instrument):
    def __init__(self, name: str, *, temperature_k: float, field_t: float) -> None:
        super().__init__(name)
        self.temperature_k: ManualParameter = self.add_parameter(
            "temperature_k",
            parameter_class=ManualParameter,
            initial_value=temperature_k,
            unit="K",
            vals=Numbers(1.0, 500.0),
        )
        self.field_t: ManualParameter = self.add_parameter(
            "field_t",
            parameter_class=ManualParameter,
            initial_value=field_t,
            unit="T",
            vals=Numbers(-14.0, 14.0),
        )
        self.stable: ManualParameter = self.add_parameter(
            "stable",
            parameter_class=ManualParameter,
            initial_value=True,
            vals=Bool(),
        )

    def get_idn(self) -> dict[str, str | None]:
        return {
            "vendor": "QCoDeS simulation",
            "model": "DynaCool-SIM",
            "serial": self.name,
            "firmware": "0.1",
        }

    def read_temperature(self) -> float:
        return float(self.temperature_k.get())

    def read_field(self) -> float:
        return float(self.field_t.get())

    def is_stable(self) -> bool:
        return bool(self.stable.get())


@dataclass
class InstrumentBundle:
    qcodes_station: Station
    sr830: SimulatedLockin
    sr865a: SimulatedLockin
    gate_top: SimulatedKeithley2400
    gate_bottom: SimulatedKeithley2400
    ppms: SimulatedPPMS

    def read_lockins(self, current_a: float) -> LockinPairReading:
        return LockinPairReading(
            xx_1w=self.sr830.acquire(current_a),
            xy_3w=self.sr865a.acquire(current_a),
        )

    def close(self) -> None:
        for instrument in (self.sr830, self.sr865a, self.gate_top, self.gate_bottom, self.ppms):
            instrument.close()


def build_simulated_bundle(config: AppConfig) -> InstrumentBundle:
    suffix = uuid4().hex[:8]
    sr830 = SimulatedLockin(
        f"sr830_{suffix}",
        role="xx",
        harmonic=1,
        reference_frequency_hz=config.instruments.reference_frequency_hz,
        seed=config.runtime.seed,
        can_source=True,
    )
    sr865a = SimulatedLockin(
        f"sr865a_{suffix}",
        role="xy",
        harmonic=3,
        reference_frequency_hz=config.instruments.reference_frequency_hz,
        seed=config.runtime.seed + 1,
        can_source=False,
    )
    gate_top = SimulatedKeithley2400(f"gate_top_{suffix}", leakage_slope_a_per_v=2e-11)
    gate_bottom = SimulatedKeithley2400(f"gate_bottom_{suffix}", leakage_slope_a_per_v=2.5e-11)
    ppms = SimulatedPPMS(
        f"ppms_{suffix}",
        temperature_k=config.instruments.initial_temperature_k,
        field_t=config.instruments.initial_field_t,
    )
    station = Station(sr830, sr865a, gate_top, gate_bottom, ppms, default=False)
    return InstrumentBundle(station, sr830, sr865a, gate_top, gate_bottom, ppms)
