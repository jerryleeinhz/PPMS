from __future__ import annotations

from dataclasses import dataclass
import math
import random
from typing import Callable, Protocol
from uuid import uuid4

from qcodes.instrument import Instrument
from qcodes.parameters import ManualParameter
from qcodes.station import Station
from qcodes.validators import Bool, Numbers

from ppms_control.config import AppConfig
from ppms_control.models import (
    GateState,
    LockinPairReading,
    LockinReading,
    PPMSState,
    PhysicalState,
)


class LockinInstrument(Protocol):
    name: str

    def reference_status(self) -> tuple[bool, float]: ...

    def set_harmonic(self, harmonic: int) -> None: ...

    def acquire(self, source_voltage_v: float) -> LockinReading: ...


class ExcitationInstrument(Protocol):
    name: str

    def set_source_voltage(self, voltage_v: float) -> None: ...

    def read_source_voltage(self) -> float: ...

    def set_source_frequency(self, frequency_hz: float) -> None: ...

    def read_source_frequency(self) -> float: ...

    def retreat_to_safe_state(self) -> None: ...

    def close(self) -> None: ...


class GateInstrument(Protocol):
    name: str

    def set_compliance(self, compliance_a: float) -> None: ...

    def set_voltage(self, voltage_v: float) -> None: ...

    def set_output(self, enabled: bool) -> None: ...

    def measure_leakage(self) -> float: ...

    def read_state(self) -> GateState: ...

    def close(self) -> None: ...


class PPMSInstrument(Protocol):
    name: str

    def read_temperature(self) -> float: ...

    def read_field(self) -> float: ...

    def is_stable(self) -> bool: ...

    def read_state(self) -> PPMSState: ...

    def set_temperature(self, target_k: float, rate_k_per_min: float) -> None: ...

    def set_field(self, target_t: float, rate_t_per_s: float) -> None: ...

    def close(self) -> None: ...


class SimulatedLockin(Instrument):
    def __init__(
        self,
        name: str,
        *,
        role: str,
        harmonic: int,
        reference_frequency_hz: float,
        series_resistance_ohm: float,
        safe_idle_voltage_v: float,
        seed: int,
        can_source: bool,
        external_frequency_provider: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(name)
        if role not in {"xx", "xy"}:
            raise ValueError(f"Unsupported simulated lock-in role: {role}")
        self.role = role
        self.harmonic = harmonic
        self._series_resistance_ohm = series_resistance_ohm
        self._safe_idle_voltage_v = safe_idle_voltage_v
        self._rng = random.Random(seed)
        self._noise_v = 2e-9
        self._can_source = can_source
        self._external_frequency_provider = external_frequency_provider

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
        self.source_voltage_v: ManualParameter = self.add_parameter(
            "source_voltage_v",
            parameter_class=ManualParameter,
            initial_value=safe_idle_voltage_v,
            unit="V",
            vals=Numbers(0.0, 5.0),
        )

    def get_idn(self) -> dict[str, str | None]:
        model = "SR830-SIM" if self.role == "xx" else "SR865A-SIM"
        return {"vendor": "QCoDeS simulation", "model": model, "serial": self.name, "firmware": "0.1"}

    def reference_status(self) -> tuple[bool, float]:
        frequency_hz = (
            self._external_frequency_provider()
            if self._external_frequency_provider is not None
            else float(self.reference_frequency_hz.get())
        )
        return bool(self.reference_locked.get()), frequency_hz

    def set_harmonic(self, harmonic: int) -> None:
        if harmonic not in {1, 2, 3}:
            raise ValueError("Harmonic must be 1, 2, or 3.")
        self.harmonic = harmonic

    def set_source_voltage(self, voltage_v: float) -> None:
        if not self._can_source and voltage_v != self._safe_idle_voltage_v:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        self.source_voltage_v.set(float(voltage_v))

    def read_source_voltage(self) -> float:
        return float(self.source_voltage_v.get())

    def set_source_frequency(self, frequency_hz: float) -> None:
        if not self._can_source:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        self.reference_frequency_hz.set(float(frequency_hz))

    def read_source_frequency(self) -> float:
        return float(self.reference_frequency_hz.get())

    def retreat_to_safe_state(self) -> None:
        self.set_source_voltage(self._safe_idle_voltage_v)

    def acquire(self, source_voltage_v: float) -> LockinReading:
        estimated_current_a = source_voltage_v / self._series_resistance_ohm
        if self.role == "xx":
            in_phase = 100.0 * estimated_current_a
            phase_deg = 3.0
        else:
            in_phase = 1.0e8 * estimated_current_a**3
            phase_deg = -7.0
        phase_rad = math.radians(phase_deg)
        x_v = in_phase * math.cos(phase_rad) + self._rng.gauss(0.0, self._noise_v)
        y_v = in_phase * math.sin(phase_rad) + self._rng.gauss(0.0, self._noise_v)
        locked, frequency_hz = self.reference_status()
        return LockinReading(
            x_v=x_v,
            y_v=y_v,
            frequency_hz=frequency_hz,
            harmonic=self.harmonic,
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

    def read_state(self) -> GateState:
        output_enabled = bool(self.output_enabled.get())
        return GateState(
            source_voltage_v=float(self.voltage_v.get()),
            output_enabled=output_enabled,
            compliance_a=float(self.compliance_a.get()),
            measured_current_a=self.measure_leakage() if output_enabled else None,
        )


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
        self.sample_position_deg: ManualParameter = self.add_parameter(
            "sample_position_deg",
            parameter_class=ManualParameter,
            initial_value=0.0,
            unit="deg",
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

    def read_state(self) -> PPMSState:
        return PPMSState(
            temperature_k=self.read_temperature(),
            temperature_status="Stable" if self.is_stable() else "Changing",
            field_t=self.read_field(),
            field_status="Holding" if self.is_stable() else "Changing",
            chamber_status="Sealed",
            sample_position_deg=float(self.sample_position_deg.get()),
            position_status="Holding" if self.is_stable() else "Moving",
            stable=self.is_stable(),
        )

    def set_temperature(self, target_k: float, rate_k_per_min: float) -> None:
        self.temperature_k.set(float(target_k))
        self.stable.set(True)

    def set_field(self, target_t: float, rate_t_per_s: float) -> None:
        self.field_t.set(float(target_t))
        self.stable.set(True)


@dataclass
class InstrumentBundle:
    qcodes_station: Station
    sr830: LockinInstrument
    sr865a: LockinInstrument
    gate_top: GateInstrument
    gate_bottom: GateInstrument
    ppms: PPMSInstrument
    excitation: ExcitationInstrument

    def set_lockin_harmonic(self, harmonic: int) -> None:
        self.sr830.set_harmonic(harmonic)
        self.sr865a.set_harmonic(harmonic)

    def read_lockins(
        self,
        source_voltage_v: float,
        requested_harmonic: int,
    ) -> LockinPairReading:
        return LockinPairReading(
            requested_harmonic=requested_harmonic,
            xx=self.sr830.acquire(source_voltage_v),
            xy=self.sr865a.acquire(source_voltage_v),
        )

    def read_physical_state(self) -> PhysicalState:
        return PhysicalState(
            source_voltage_v=self.excitation.read_source_voltage(),
            source_frequency_hz=self.excitation.read_source_frequency(),
            gate_top=self.gate_top.read_state(),
            gate_bottom=self.gate_bottom.read_state(),
            ppms=self.ppms.read_state(),
        )

    def close(self) -> None:
        instruments = (
            self.excitation,
            self.sr830,
            self.sr865a,
            self.gate_top,
            self.gate_bottom,
            self.ppms,
        )
        closed: set[int] = set()
        for instrument in instruments:
            if id(instrument) not in closed:
                instrument.close()
                closed.add(id(instrument))


def build_simulated_bundle(config: AppConfig) -> InstrumentBundle:
    suffix = uuid4().hex[:8]
    sr830 = SimulatedLockin(
        f"sr830_{suffix}",
        role="xx",
        harmonic=1,
        reference_frequency_hz=config.instruments.reference_frequency_hz,
        series_resistance_ohm=config.instruments.series_resistance_ohm,
        safe_idle_voltage_v=config.safety.source_safe_idle_voltage_v,
        seed=config.runtime.seed,
        can_source=True,
    )
    sr865a = SimulatedLockin(
        f"sr865a_{suffix}",
        role="xy",
        harmonic=3,
        reference_frequency_hz=config.instruments.reference_frequency_hz,
        series_resistance_ohm=config.instruments.series_resistance_ohm,
        safe_idle_voltage_v=config.safety.source_safe_idle_voltage_v,
        seed=config.runtime.seed + 1,
        can_source=False,
        external_frequency_provider=sr830.read_source_frequency,
    )
    gate_top = SimulatedKeithley2400(f"gate_top_{suffix}", leakage_slope_a_per_v=2e-11)
    gate_bottom = SimulatedKeithley2400(f"gate_bottom_{suffix}", leakage_slope_a_per_v=2.5e-11)
    ppms = SimulatedPPMS(
        f"ppms_{suffix}",
        temperature_k=config.instruments.initial_temperature_k,
        field_t=config.instruments.initial_field_t,
    )
    station = Station(sr830, sr865a, gate_top, gate_bottom, ppms, default=False)
    return InstrumentBundle(
        qcodes_station=station,
        sr830=sr830,
        sr865a=sr865a,
        gate_top=gate_top,
        gate_bottom=gate_bottom,
        ppms=ppms,
        excitation=sr830,
    )
