from __future__ import annotations

from dataclasses import dataclass
import math
import time

from ppms_control.config import AppConfig
from ppms_control.instruments import GateInstrument, InstrumentBundle
from ppms_control.models import LockinPairReading, MeasurementCondition, PhysicalState


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
        self._owns_field = False

    @property
    def qcodes_snapshot(self) -> dict[str, object]:
        return self._bundle.qcodes_station.snapshot(update=False)

    def preflight_condition(self, condition: MeasurementCondition) -> None:
        self._validate_excitation_voltage(condition.source_voltage_v)
        self._validate_gate_targets(
            condition.gate_top_voltage_v,
            condition.gate_bottom_voltage_v,
        )
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
        self.verify_gate_state(
            condition.gate_top_voltage_v,
            condition.gate_bottom_voltage_v,
        )

        for lockin in (self._bundle.sr830, self._bundle.sr865a):
            locked, _ = lockin.reference_status()
            if not locked:
                raise SafetyViolation(f"{lockin.name} external reference is not locked.")

    def set_excitation_voltage(self, voltage_v: float) -> None:
        self._validate_excitation_voltage(voltage_v)
        self._bundle.excitation.set_source_voltage(voltage_v)

    def set_excitation_frequency(self, frequency_hz: float) -> None:
        self._validate_finite(frequency_hz, "source frequency")
        limits = self._config.safety
        if not limits.source_frequency_min_hz <= frequency_hz <= limits.source_frequency_max_hz:
            raise SafetyViolation("Requested source frequency is outside the configured range.")
        self._bundle.excitation.set_source_frequency(frequency_hz)

    def retreat_excitation(self) -> None:
        self._bundle.excitation.retreat_to_safe_state()

    def set_temperature(self, target_k: float, rate_k_per_min: float) -> None:
        self._validate_finite(target_k, "temperature")
        self._validate_finite(rate_k_per_min, "temperature rate")
        if not (
            self._config.safety.temperature_min_k
            <= target_k
            <= self._config.safety.temperature_max_k
        ):
            raise SafetyViolation("Requested temperature is outside the safety range.")
        if not 0 < rate_k_per_min <= self._config.safety.temperature_rate_max_k_per_min:
            raise SafetyViolation("Requested temperature rate is outside the safety range.")
        self._bundle.ppms.set_temperature(target_k, rate_k_per_min)

    def set_field(self, target_t: float, rate_t_per_s: float) -> None:
        self._validate_finite(target_t, "field")
        self._validate_finite(rate_t_per_s, "field rate")
        if abs(target_t) > self._config.safety.field_abs_limit_t:
            raise SafetyViolation("Requested field exceeds the safety limit.")
        if not 0 < rate_t_per_s <= self._config.safety.field_rate_max_t_per_s:
            raise SafetyViolation("Requested field rate is outside the safety range.")
        self._owns_field = True
        self._bundle.ppms.set_field(target_t, rate_t_per_s)
        if target_t == 0:
            self._owns_field = False

    def wait_for_environment(
        self,
        target_temperature_k: float,
        target_field_t: float,
        *,
        timeout_s: float,
        poll_s: float,
    ) -> None:
        self._validate_finite(timeout_s, "stabilization timeout")
        self._validate_finite(poll_s, "stability poll interval")
        if timeout_s <= 0 or poll_s < 0:
            raise SafetyViolation("Stabilization timing values are invalid.")
        deadline = time.monotonic() + timeout_s
        while True:
            measured_temperature = self._bundle.ppms.read_temperature()
            measured_field = self._bundle.ppms.read_field()
            if (
                self._bundle.ppms.is_stable()
                and abs(measured_temperature - target_temperature_k)
                <= self._config.acquisition.temperature_tolerance_k
                and abs(measured_field - target_field_t)
                <= self._config.acquisition.field_tolerance_t
            ):
                return
            if time.monotonic() >= deadline:
                raise SafetyViolation(
                    "PPMS did not reach the requested temperature and field before timeout."
                )
            time.sleep(poll_s)

    def set_gates(self, top_v: float, bottom_v: float) -> tuple[float, float]:
        self._validate_gate_targets(top_v, bottom_v)
        temperature_k = self._bundle.ppms.read_temperature()
        if (top_v != 0 or bottom_v != 0) and temperature_k > self._config.safety.gate_temperature_limit_k:
            raise SafetyViolation("Non-zero gate voltage is forbidden at the current temperature.")

        compliance = self._config.safety.gate_compliance_limit_a
        try:
            for gate, target in (
                (self._bundle.gate_top, top_v),
                (self._bundle.gate_bottom, bottom_v),
            ):
                gate.set_compliance(compliance)
                if target == 0:
                    gate.set_voltage(0.0)
                    gate.set_output(False)
                else:
                    gate.set_voltage(target)
                    gate.set_output(True)
            leakages = (
                self._bundle.gate_top.measure_leakage(),
                self._bundle.gate_bottom.measure_leakage(),
            )
        except Exception:
            self.safe_shutdown()
            raise

        if any(
            not math.isfinite(leakage)
            or abs(leakage) > self._config.safety.gate_leakage_limit_a
            for leakage in leakages
        ):
            self.safe_shutdown()
            raise SafetyViolation(
                "Gate leakage exceeds the configured limit "
                f"(top={leakages[0]:.6g} A, bottom={leakages[1]:.6g} A)."
            )
        return leakages

    def ramp_gates(
        self,
        top_v: float,
        bottom_v: float,
        *,
        max_step_v: float,
        step_delay_s: float,
    ) -> tuple[float, float]:
        """Ramp both gate setpoints together while checking leakage at every step."""

        self._validate_gate_targets(top_v, bottom_v)
        self._validate_finite(max_step_v, "gate ramp step")
        self._validate_finite(step_delay_s, "gate ramp step delay")
        if max_step_v <= 0 or step_delay_s < 0:
            raise SafetyViolation("Gate ramp timing values are invalid.")

        top_state = self._bundle.gate_top.read_state()
        bottom_state = self._bundle.gate_bottom.read_state()
        try:
            if not top_state.output_enabled and top_state.source_voltage_v != 0.0:
                self._bundle.gate_top.set_voltage(0.0)
            if not bottom_state.output_enabled and bottom_state.source_voltage_v != 0.0:
                self._bundle.gate_bottom.set_voltage(0.0)
        except Exception:
            self.safe_shutdown()
            raise
        top_start = top_state.source_voltage_v if top_state.output_enabled else 0.0
        bottom_start = bottom_state.source_voltage_v if bottom_state.output_enabled else 0.0
        limit = self._config.safety.gate_voltage_limit_v
        if (
            not math.isfinite(top_start)
            or not math.isfinite(bottom_start)
            or abs(top_start) > limit
            or abs(bottom_start) > limit
        ):
            self.safe_shutdown()
            raise SafetyViolation("Existing gate state is outside the configured limit.")
        self.verify_gate_state(top_start, bottom_start)
        steps = max(
            1,
            math.ceil(
                max(abs(top_v - top_start), abs(bottom_v - bottom_start))
                / max_step_v
            ),
        )
        leakages = (0.0, 0.0)
        for index in range(1, steps + 1):
            fraction = index / steps
            leakages = self.set_gates(
                top_start + (top_v - top_start) * fraction,
                bottom_start + (bottom_v - bottom_start) * fraction,
            )
            if step_delay_s and index < steps:
                time.sleep(step_delay_s)
        return leakages

    def verify_gate_state(
        self,
        expected_top_v: float,
        expected_bottom_v: float,
        state: PhysicalState | None = None,
    ) -> None:
        """Fail closed if a gate drifts, loses output, or exceeds leakage."""

        self._validate_gate_targets(expected_top_v, expected_bottom_v)
        physical_state = self.read_physical_state() if state is None else state
        tolerance_v = self._config.acquisition.gate_voltage_tolerance_v
        problems: list[str] = []
        gates_are_nonzero = expected_top_v != 0.0 or expected_bottom_v != 0.0
        if gates_are_nonzero and (
            not math.isfinite(physical_state.ppms.temperature_k)
            or physical_state.ppms.temperature_k
            > self._config.safety.gate_temperature_limit_k
        ):
            problems.append("temperature exceeds the gate limit")
        for label, expected_v, gate_state in (
            ("top", expected_top_v, physical_state.gate_top),
            ("bottom", expected_bottom_v, physical_state.gate_bottom),
        ):
            if not math.isfinite(gate_state.source_voltage_v):
                problems.append(f"{label}-gate voltage is non-finite")
            elif abs(gate_state.source_voltage_v - expected_v) > tolerance_v:
                problems.append(f"{label}-gate voltage mismatch")
            if (
                not math.isfinite(gate_state.compliance_a)
                or gate_state.compliance_a
                > self._config.safety.gate_compliance_limit_a * (1.0 + 1e-6)
            ):
                problems.append(f"{label}-gate compliance exceeds limit")
            expected_output = expected_v != 0.0
            if gate_state.output_enabled != expected_output:
                problems.append(f"{label}-gate output state mismatch")
            if expected_output and gate_state.measured_current_a is None:
                problems.append(f"{label}-gate leakage unavailable")
            elif (
                gate_state.measured_current_a is not None
                and (
                    not math.isfinite(gate_state.measured_current_a)
                    or abs(gate_state.measured_current_a)
                    > self._config.safety.gate_leakage_limit_a
                )
            ):
                problems.append(f"{label}-gate leakage exceeds limit")

        if problems:
            cleanup_errors = self.safe_shutdown()
            cleanup_suffix = ""
            if cleanup_errors:
                cleanup_suffix = "; cleanup errors: " + ", ".join(
                    f"{item.step}: {item.message}" for item in cleanup_errors
                )
            raise SafetyViolation("Unsafe gate state: " + ", ".join(problems) + cleanup_suffix)

    def set_lockin_harmonic(self, harmonic: int) -> None:
        if harmonic not in {1, 2, 3}:
            raise SafetyViolation("Requested harmonic must be 1, 2, or 3.")
        self._bundle.set_lockin_harmonic(harmonic)

    def read_lockins(
        self,
        source_voltage_v: float,
        requested_harmonic: int,
    ) -> LockinPairReading:
        return self._bundle.read_lockins(source_voltage_v, requested_harmonic)

    def read_physical_state(self) -> PhysicalState:
        return self._bundle.read_physical_state()

    def safe_shutdown(self) -> tuple[CleanupError, ...]:
        errors: list[CleanupError] = []
        actions = (
            ("excitation_safe_idle", self._bundle.excitation.retreat_to_safe_state),
            ("top_gate_zero", lambda: self._zero_gate(self._bundle.gate_top)),
            ("bottom_gate_zero", lambda: self._zero_gate(self._bundle.gate_bottom)),
        )
        cleanup_actions = list(actions)
        if self._owns_field:
            cleanup_actions.append(("field_zero", self._zero_owned_field))
        for step, action in cleanup_actions:
            try:
                action()
            except Exception as exc:  # cleanup must continue after an individual failure
                errors.append(CleanupError(step, str(exc)))
        return tuple(errors)

    def _zero_owned_field(self) -> None:
        self._bundle.ppms.set_field(
            0.0,
            self._config.safety.field_shutdown_rate_t_per_s,
        )
        self._owns_field = False

    @staticmethod
    def _zero_gate(gate: GateInstrument) -> None:
        failures: list[str] = []
        try:
            gate.set_voltage(0.0)
        except Exception as exc:
            failures.append(f"voltage zero failed: {exc}")
        try:
            gate.set_output(False)
        except Exception as exc:
            failures.append(f"output disable failed: {exc}")
        if failures:
            raise RuntimeError("; ".join(failures))

    @staticmethod
    def _validate_finite(value: float, label: str) -> None:
        if not math.isfinite(value):
            raise SafetyViolation(f"{label} must be finite.")

    def _validate_excitation_voltage(self, voltage_v: float) -> None:
        self._validate_finite(voltage_v, "source voltage")
        limits = self._config.safety
        if not limits.source_voltage_min_v <= voltage_v <= limits.source_voltage_max_v:
            raise SafetyViolation("Requested source voltage is outside the configured range.")
        estimated_current_a = voltage_v / self._config.instruments.series_resistance_ohm
        if estimated_current_a > limits.estimated_current_limit_a:
            raise SafetyViolation("Requested source voltage exceeds the estimated current limit.")

    def _validate_gate_targets(self, top_v: float, bottom_v: float) -> None:
        self._validate_finite(top_v, "top-gate voltage")
        self._validate_finite(bottom_v, "bottom-gate voltage")
        limit = self._config.safety.gate_voltage_limit_v
        if abs(top_v) > limit or abs(bottom_v) > limit:
            raise SafetyViolation("Requested gate voltage exceeds the hardware limit.")
