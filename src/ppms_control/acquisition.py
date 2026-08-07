from __future__ import annotations

import math
from statistics import fmean, stdev
import time

from ppms_control.config import AcquisitionConfig
from ppms_control.models import (
    AttemptResult,
    AveragedPair,
    InstrumentSample,
    LockinPairReading,
    LockinReading,
    MeasurementCondition,
    PhysicalState,
    TransportReading,
)
from ppms_control.safety import SafeStation
from ppms_control.store import RunStore


class AcquisitionError(RuntimeError):
    """Raised after every allowed attempt for a condition is rejected."""


def _standard_deviation(values: list[float]) -> float:
    return stdev(values) if len(values) > 1 else 0.0


def _average(samples: list[LockinPairReading]) -> AveragedPair:
    xx_1w = [sample.xx for sample in samples if sample.requested_harmonic == 1]
    xy_3w = [sample.xy for sample in samples if sample.requested_harmonic == 3]
    return AveragedPair(
        xx_x_v=fmean(reading.x_v for reading in xx_1w),
        xx_y_v=fmean(reading.y_v for reading in xx_1w),
        xx_x_std_v=_standard_deviation([reading.x_v for reading in xx_1w]),
        xx_y_std_v=_standard_deviation([reading.y_v for reading in xx_1w]),
        xx_frequency_hz=fmean(reading.frequency_hz for reading in xx_1w),
        xy_x_v=fmean(reading.x_v for reading in xy_3w),
        xy_y_v=fmean(reading.y_v for reading in xy_3w),
        xy_x_std_v=_standard_deviation([reading.x_v for reading in xy_3w]),
        xy_y_std_v=_standard_deviation([reading.y_v for reading in xy_3w]),
        xy_frequency_hz=fmean(reading.frequency_hz for reading in xy_3w),
    )


def _sr_transport_readings(
    condition: MeasurementCondition,
    lockins: LockinPairReading,
    state: PhysicalState,
    acquisition: AcquisitionConfig,
) -> tuple[TransportReading, TransportReading]:
    """Normalize one raw SR830/SR865A sample for the shared reading table."""

    timestamp_s = time.time()
    state_flags: set[str] = set()
    if (
        abs(state.source_voltage_v - condition.source_voltage_v)
        > acquisition.source_voltage_tolerance_v
    ):
        state_flags.add("source_voltage_mismatch")
    if not state.ppms.stable:
        state_flags.add("ppms_unstable")
    if abs(state.ppms.temperature_k - condition.temperature_k) > acquisition.temperature_tolerance_k:
        state_flags.add("temperature_drift")
    if abs(state.source_frequency_hz - condition.frequency_hz) > acquisition.reference_frequency_tolerance_hz:
        state_flags.add("source_frequency_mismatch")
    if abs(state.ppms.field_t - condition.field_t) > acquisition.field_tolerance_t:
        state_flags.add("field_drift")

    def normalize(
        signal: str,
        instrument_channel: str,
        expected_harmonic: int,
        reading: LockinReading,
    ) -> TransportReading:
        flags = set(state_flags)
        if not reading.reference_locked:
            flags.add("reference_unlocked")
        if reading.overload:
            flags.add("overload")
        if abs(reading.frequency_hz - condition.frequency_hz) > acquisition.reference_frequency_tolerance_hz:
            flags.add("frequency_mismatch")
        if reading.harmonic != expected_harmonic:
            flags.add("harmonic_mismatch")
        return TransportReading(
            backend="sr_lockin",
            signal=signal,
            instrument_channel=instrument_channel,
            harmonic=reading.harmonic,
            timestamp_s=timestamp_s,
            temperature_k=state.ppms.temperature_k,
            field_t=state.ppms.field_t,
            gate_top_voltage_v=condition.gate_top_voltage_v,
            gate_bottom_voltage_v=condition.gate_bottom_voltage_v,
            sample_position_deg=state.ppms.sample_position_deg,
            drive_current_a=condition.estimated_current_a,
            frequency_hz=reading.frequency_hz,
            x_v=reading.x_v,
            y_v=reading.y_v,
            amplitude_v=reading.r_v,
            phase_deg=reading.theta_deg,
            ratio_db=None,
            phase_resolved=True,
            sequence_index=condition.sequence_index,
            quality_flags=tuple(sorted(flags)),
        )

    return (
        normalize("xx", "sr830", lockins.requested_harmonic, lockins.xx),
        normalize("xy", "sr865a", lockins.requested_harmonic, lockins.xy),
    )


def _quality_flags(
    samples: list[LockinPairReading],
    states: list[PhysicalState],
    condition: MeasurementCondition,
    acquisition: AcquisitionConfig,
) -> list[str]:
    flags: set[str] = set()
    for sample in samples:
        for signal, reading in (("xx", sample.xx), ("xy", sample.xy)):
            name = f"{signal}_{sample.requested_harmonic}w"
            if not reading.reference_locked:
                flags.add(f"{name}:reference_unlocked")
            if reading.overload:
                flags.add(f"{name}:overload")
            if abs(reading.frequency_hz - condition.frequency_hz) > acquisition.reference_frequency_tolerance_hz:
                flags.add(f"{name}:frequency_mismatch")
            if reading.harmonic != sample.requested_harmonic:
                flags.add(f"{name}:harmonic_mismatch")
            if not all(math.isfinite(value) for value in (reading.x_v, reading.y_v, reading.frequency_hz)):
                flags.add(f"{name}:nonfinite")

    for harmonic in (1, 2, 3):
        harmonic_samples = [
            sample for sample in samples if sample.requested_harmonic == harmonic
        ]
        for signal in ("xx", "xy"):
            readings = [getattr(sample, signal) for sample in harmonic_samples]
            if any(
                _standard_deviation(values) > acquisition.noise_limit_v
                for values in (
                    [reading.x_v for reading in readings],
                    [reading.y_v for reading in readings],
                )
            ):
                flags.add(f"{signal}_{harmonic}w:excess_noise")

    for state in states:
        if (
            abs(state.source_voltage_v - condition.source_voltage_v)
            > acquisition.source_voltage_tolerance_v
        ):
            flags.add("source_voltage_mismatch")
        if not state.ppms.stable:
            flags.add("ppms_unstable")
        if abs(state.ppms.temperature_k - condition.temperature_k) > acquisition.temperature_tolerance_k:
            flags.add("temperature_drift")
        if (
            abs(state.source_frequency_hz - condition.frequency_hz)
            > acquisition.reference_frequency_tolerance_hz
        ):
            flags.add("source_frequency_mismatch")
        if abs(state.ppms.field_t - condition.field_t) > acquisition.field_tolerance_t:
            flags.add("field_drift")
        state_values = [
            state.source_voltage_v,
            state.source_frequency_hz,
            state.gate_top.source_voltage_v,
            state.gate_top.compliance_a,
            state.gate_bottom.source_voltage_v,
            state.gate_bottom.compliance_a,
            state.ppms.temperature_k,
            state.ppms.field_t,
        ]
        if state.ppms.sample_position_deg is not None:
            state_values.append(state.ppms.sample_position_deg)
        state_values.extend(
            value
            for value in (
                state.gate_top.measured_current_a,
                state.gate_bottom.measured_current_a,
            )
            if value is not None
        )
        if not all(math.isfinite(value) for value in state_values):
            flags.add("physical_state_nonfinite")
    return sorted(flags)


class MeasurementEngine:
    def __init__(
        self,
        station: SafeStation,
        store: RunStore,
        run_id: str,
        acquisition: AcquisitionConfig,
    ) -> None:
        self._station = station
        self._store = store
        self._run_id = run_id
        self._acquisition = acquisition

    def acquire(self, condition: MeasurementCondition) -> AttemptResult:
        last_result: AttemptResult | None = None
        for attempt_index in range(1, self._acquisition.max_attempts + 1):
            reading = AveragedPair.empty()
            flags: list[str] = []
            error: str | None = None
            try:
                self._station.preflight_condition(condition)
                self._station.set_excitation_frequency(condition.frequency_hz)
                self._station.set_excitation_voltage(condition.source_voltage_v)
                samples: list[LockinPairReading] = []
                states: list[PhysicalState] = []
                sample_index = 0
                for harmonic in (1, 2, 3):
                    self._station.set_lockin_harmonic(harmonic)
                    if self._acquisition.settle_s:
                        time.sleep(self._acquisition.settle_s)
                    for average_index in range(self._acquisition.averages):
                        lockins = self._station.read_lockins(
                            condition.source_voltage_v,
                            harmonic,
                        )
                        state = self._station.read_physical_state()
                        self._store.record_instrument_sample(
                            self._run_id,
                            InstrumentSample(
                                condition=condition,
                                attempt_index=attempt_index,
                                sample_index=sample_index,
                                lockins=lockins,
                                state=state,
                            ),
                        )
                        self._station.verify_gate_state(
                            condition.gate_top_voltage_v,
                            condition.gate_bottom_voltage_v,
                            state,
                        )
                        for transport_reading in _sr_transport_readings(
                            condition,
                            lockins,
                            state,
                            self._acquisition,
                        ):
                            self._store.record_transport_reading(
                                self._run_id,
                                transport_reading,
                            )
                        samples.append(lockins)
                        states.append(state)
                        sample_index += 1
                        if (
                            self._acquisition.sample_interval_s
                            and average_index + 1 < self._acquisition.averages
                        ):
                            time.sleep(self._acquisition.sample_interval_s)
                reading = _average(samples)
                flags.extend(
                    _quality_flags(
                        samples,
                        states,
                        condition,
                        self._acquisition,
                    )
                )
            except Exception as exc:
                flags.append("acquisition_exception")
                error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    self._station.retreat_excitation()
                except Exception as exc:
                    flags.append("excitation_safe_idle_failed")
                    cleanup_message = f"{type(exc).__name__}: {exc}"
                    error = cleanup_message if error is None else f"{error}; {cleanup_message}"

            flags = sorted(set(flags))
            result = AttemptResult(
                condition=condition,
                attempt_index=attempt_index,
                reading=reading,
                accepted=not flags,
                flags=tuple(flags),
                error=error,
            )
            self._store.record_attempt(self._run_id, result)
            last_result = result
            if result.accepted:
                return result

        assert last_result is not None
        raise AcquisitionError(
            f"Condition {condition.condition_id} was rejected after "
            f"{self._acquisition.max_attempts} attempts: {last_result.flags}"
        )
