from __future__ import annotations

import math
from statistics import fmean, stdev
import time

from ppms_control.config import AcquisitionConfig, InstrumentConfig
from ppms_control.models import (
    AttemptResult,
    AveragedPair,
    LockinPairReading,
    MeasurementCondition,
)
from ppms_control.safety import SafeStation
from ppms_control.store import RunStore


class AcquisitionError(RuntimeError):
    """Raised after every allowed attempt for a condition is rejected."""


def _standard_deviation(values: list[float]) -> float:
    return stdev(values) if len(values) > 1 else 0.0


def _average(samples: list[LockinPairReading]) -> AveragedPair:
    return AveragedPair(
        xx_x_v=fmean(sample.xx_1w.x_v for sample in samples),
        xx_y_v=fmean(sample.xx_1w.y_v for sample in samples),
        xx_x_std_v=_standard_deviation([sample.xx_1w.x_v for sample in samples]),
        xx_y_std_v=_standard_deviation([sample.xx_1w.y_v for sample in samples]),
        xx_frequency_hz=fmean(sample.xx_1w.frequency_hz for sample in samples),
        xy_x_v=fmean(sample.xy_3w.x_v for sample in samples),
        xy_y_v=fmean(sample.xy_3w.y_v for sample in samples),
        xy_x_std_v=_standard_deviation([sample.xy_3w.x_v for sample in samples]),
        xy_y_std_v=_standard_deviation([sample.xy_3w.y_v for sample in samples]),
        xy_frequency_hz=fmean(sample.xy_3w.frequency_hz for sample in samples),
    )


def _quality_flags(
    samples: list[LockinPairReading],
    averaged: AveragedPair,
    acquisition: AcquisitionConfig,
    instruments: InstrumentConfig,
) -> list[str]:
    flags: set[str] = set()
    for sample in samples:
        for name, reading in (("xx_1w", sample.xx_1w), ("xy_3w", sample.xy_3w)):
            if not reading.reference_locked:
                flags.add(f"{name}:reference_unlocked")
            if reading.overload:
                flags.add(f"{name}:overload")
            if abs(reading.frequency_hz - instruments.reference_frequency_hz) > acquisition.reference_frequency_tolerance_hz:
                flags.add(f"{name}:frequency_mismatch")
            if not all(math.isfinite(value) for value in (reading.x_v, reading.y_v, reading.frequency_hz)):
                flags.add(f"{name}:nonfinite")

    standard_deviations = (
        averaged.xx_x_std_v,
        averaged.xx_y_std_v,
        averaged.xy_x_std_v,
        averaged.xy_y_std_v,
    )
    if any(value is not None and value > acquisition.noise_limit_v for value in standard_deviations):
        flags.add("excess_noise")
    return sorted(flags)


class MeasurementEngine:
    def __init__(
        self,
        station: SafeStation,
        store: RunStore,
        run_id: str,
        acquisition: AcquisitionConfig,
        instruments: InstrumentConfig,
    ) -> None:
        self._station = station
        self._store = store
        self._run_id = run_id
        self._acquisition = acquisition
        self._instruments = instruments

    def acquire(self, condition: MeasurementCondition) -> AttemptResult:
        last_result: AttemptResult | None = None
        for attempt_index in range(1, self._acquisition.max_attempts + 1):
            reading = AveragedPair.empty()
            flags: list[str] = []
            error: str | None = None
            try:
                self._station.preflight_condition(condition)
                self._station.set_excitation_current(condition.current_a)
                if self._acquisition.settle_s:
                    time.sleep(self._acquisition.settle_s)
                samples: list[LockinPairReading] = []
                for sample_index in range(self._acquisition.averages):
                    samples.append(self._station.read_lockins(condition.current_a))
                    if self._acquisition.sample_interval_s and sample_index + 1 < self._acquisition.averages:
                        time.sleep(self._acquisition.sample_interval_s)
                reading = _average(samples)
                flags.extend(_quality_flags(samples, reading, self._acquisition, self._instruments))
            except Exception as exc:
                flags.append("acquisition_exception")
                error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    self._station.set_excitation_current(0.0)
                except Exception as exc:
                    flags.append("excitation_zero_failed")
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
