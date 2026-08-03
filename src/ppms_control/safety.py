from __future__ import annotations

from dataclasses import dataclass
import math

from ppms_control.config import AppConfig
from ppms_control.instruments import GateInstrument, InstrumentBundle
from ppms_control.models import LockinPairReading, MeasurementCondition


class SafetyViolation(RuntimeError):
    """Raised before an unsafe command reaches an instrument driver."""


@dataclass(frozen=True)
class CleanupError:
    step: str
    message: str


class SafeStation:
    """The protocol-facing facade for all state-changing operations."""

    def __init__(self, bundle: InstrumentBundle, config: AppConfig) -> None:
        self._bundle = bundle
        self._config = config

    @property
    def qcodes_snapshot(self) -> dict[str, object]:
        return self._bundle.qcodes_station.snapshot(update=True)

    def preflight_condition(self, condition: MeasurementCondition) -> None:
        self._validate_finite(condition.current_a, "current")
        if abs(condition.current_a) > self._config.safety.normal_current_limit_a:
            raise SafetyViolation("Requested current exceeds the normal current limit.")
        if not (
            self._config.safety.temperature_min_k
            <= condition.temperature_k
            <= self._config.safety.temperature_max_k
        ):
            raise SafetyViolation("Requested temperature is outside the safety range.")
        if abs(condition.field_t) > self._config.safety.field_abs_limit_t:
            raise SafetyViolation("Requested field exceeds the safety limit.")
        if not self._bundle.ppms.is_stable():
            raise SafetyViolation("PPMS is not stable.")
        measured_temperature = self._bundle.ppms.read_temperature()
        measured_field = self._bundle.ppms.read_field()
        if abs(measured_temperature - condition.temperature_k) > self._config.acquisition.temperature_tolerance_k:
            raise SafetyViolation("Measured temperature is outside the acquisition tolerance.")
        if abs(measured_field - condition.field_t) > self._config.acquisition.field_tolerance_t:
            raise SafetyViolation("Measured field is outside the acquisition tolerance.")

        for lockin in (self._bundle.sr830, self._bundle.sr865a):
            locked, frequency_hz = lockin.reference_status()
            if not locked:
                raise SafetyViolation(f"{lockin.name} external reference is not locked.")
            if (
                abs(frequency_hz - self._config.instruments.reference_frequency_hz)
                > self._config.acquisition.reference_frequency_tolerance_hz
            ):
                raise SafetyViolation(f"{lockin.name} reference frequency is outside tolerance.")

    def set_excitation_current(self, current_a: float) -> None:
        self._validate_finite(current_a, "current")
        if abs(current_a) > self._config.safety.normal_current_limit_a:
            raise SafetyViolation("Requested current exceeds the normal current limit.")
        self._bundle.sr830.set_source_current(current_a)

    def set_gates(self, top_v: float, bottom_v: float) -> tuple[float, float]:
        self._validate_finite(top_v, "top-gate voltage")
        self._validate_finite(bottom_v, "bottom-gate voltage")
        limit = self._config.safety.gate_voltage_limit_v
        if abs(top_v) > limit or abs(bottom_v) > limit:
            raise SafetyViolation("Requested gate voltage exceeds the hardware limit.")
        temperature_k = self._bundle.ppms.read_temperature()
        if (top_v != 0 or bottom_v != 0) and temperature_k > self._config.safety.gate_temperature_limit_k:
            raise SafetyViolation("Non-zero gate voltage is forbidden at the current temperature.")

        compliance = self._config.safety.gate_compliance_limit_a
        for gate, target in ((self._bundle.gate_top, top_v), (self._bundle.gate_bottom, bottom_v)):
            gate.set_compliance(compliance)
            if target == 0:
                gate.set_voltage(0.0)
                gate.set_output(False)
            else:
                gate.set_output(True)
                gate.set_voltage(target)

        leakages = (
            self._bundle.gate_top.measure_leakage(),
            self._bundle.gate_bottom.measure_leakage(),
        )
        if any(abs(leakage) > self._config.safety.gate_leakage_limit_a for leakage in leakages):
            self.safe_shutdown()
            raise SafetyViolation("Gate leakage exceeds the configured limit.")
        return leakages

    def read_lockins(self, current_a: float) -> LockinPairReading:
        return self._bundle.read_lockins(current_a)

    def safe_shutdown(self) -> tuple[CleanupError, ...]:
        errors: list[CleanupError] = []
        actions = (
            ("excitation_zero", lambda: self._bundle.sr830.set_source_current(0.0)),
            ("top_gate_zero", lambda: self._zero_gate(self._bundle.gate_top)),
            ("bottom_gate_zero", lambda: self._zero_gate(self._bundle.gate_bottom)),
        )
        for step, action in actions:
            try:
                action()
            except Exception as exc:  # cleanup must continue after an individual failure
                errors.append(CleanupError(step, str(exc)))
        return tuple(errors)

    @staticmethod
    def _zero_gate(gate: GateInstrument) -> None:
        gate.set_voltage(0.0)
        gate.set_output(False)

    @staticmethod
    def _validate_finite(value: float, label: str) -> None:
        if not math.isfinite(value):
            raise SafetyViolation(f"{label} must be finite.")
