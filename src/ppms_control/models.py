from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math


@dataclass(frozen=True)
class MeasurementCondition:
    sequence_index: int
    source_voltage_v: float
    estimated_current_a: float
    frequency_hz: float
    temperature_k: float
    field_t: float
    gate_top_voltage_v: float = 0.0
    gate_bottom_voltage_v: float = 0.0

    @property
    def condition_id(self) -> str:
        def canonical(value: float) -> float:
            return 0.0 if value == 0 else value

        payload = json.dumps(
            {
                "source_voltage_v": canonical(self.source_voltage_v),
                "frequency_hz": canonical(self.frequency_hz),
                "field_t": canonical(self.field_t),
                "gate_bottom_voltage_v": canonical(self.gate_bottom_voltage_v),
                "gate_top_voltage_v": canonical(self.gate_top_voltage_v),
                "temperature_k": canonical(self.temperature_k),
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LockinReading:
    x_v: float
    y_v: float
    frequency_hz: float
    harmonic: int
    reference_locked: bool
    overload: bool

    @property
    def r_v(self) -> float:
        return math.hypot(self.x_v, self.y_v)

    @property
    def theta_deg(self) -> float:
        return math.degrees(math.atan2(self.y_v, self.x_v))


@dataclass(frozen=True)
class LockinPairReading:
    requested_harmonic: int
    xx: LockinReading
    xy: LockinReading

    def __post_init__(self) -> None:
        if self.requested_harmonic not in {1, 2, 3}:
            raise ValueError("Requested harmonic must be 1, 2, or 3.")


@dataclass(frozen=True)
class GateState:
    source_voltage_v: float
    output_enabled: bool
    compliance_a: float
    measured_current_a: float | None


@dataclass(frozen=True)
class PPMSState:
    temperature_k: float
    temperature_status: str
    field_t: float
    field_status: str
    chamber_status: str
    sample_position_deg: float | None
    position_status: str | None
    stable: bool


@dataclass(frozen=True)
class PhysicalState:
    source_voltage_v: float
    source_frequency_hz: float
    gate_top: GateState
    gate_bottom: GateState
    ppms: PPMSState


@dataclass(frozen=True)
class InstrumentSample:
    condition: MeasurementCondition
    attempt_index: int
    sample_index: int
    lockins: LockinPairReading
    state: PhysicalState


@dataclass(frozen=True)
class AveragedPair:
    xx_x_v: float | None
    xx_y_v: float | None
    xx_x_std_v: float | None
    xx_y_std_v: float | None
    xx_frequency_hz: float | None
    xy_x_v: float | None
    xy_y_v: float | None
    xy_x_std_v: float | None
    xy_y_std_v: float | None
    xy_frequency_hz: float | None

    @classmethod
    def empty(cls) -> "AveragedPair":
        return cls(None, None, None, None, None, None, None, None, None, None)


@dataclass(frozen=True)
class AttemptResult:
    condition: MeasurementCondition
    attempt_index: int
    reading: AveragedPair
    accepted: bool
    flags: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class TransportReading:
    """One backend-independent transport observation.

    A reading represents one spatial signal (xx or xy) at one harmonic.  Fields
    that a backend cannot provide remain ``None``; in particular, the installed
    ETO 1.2 software provides dB-referenced amplitudes but no phase for 2w/3w.
    """

    backend: str
    signal: str
    instrument_channel: str
    harmonic: int
    timestamp_s: float
    temperature_k: float
    field_t: float
    sample_position_deg: float | None
    drive_current_a: float | None
    frequency_hz: float | None
    x_v: float | None
    y_v: float | None
    amplitude_v: float | None
    phase_deg: float | None
    ratio_db: float | None
    phase_resolved: bool
    gate_top_voltage_v: float | None = None
    gate_bottom_voltage_v: float | None = None
    sequence_index: int | None = None
    source_row: int | None = None
    comment: str = ""
    status_code: int | None = None
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("Transport backend must not be empty.")
        if self.signal not in {"xx", "xy"}:
            raise ValueError("Transport signal must be 'xx' or 'xy'.")
        if self.harmonic not in {1, 2, 3}:
            raise ValueError("Transport harmonic must be 1, 2, or 3.")
        numeric_values = (
            self.timestamp_s,
            self.temperature_k,
            self.field_t,
            self.gate_top_voltage_v,
            self.gate_bottom_voltage_v,
            self.sample_position_deg,
            self.drive_current_a,
            self.frequency_hz,
            self.x_v,
            self.y_v,
            self.amplitude_v,
            self.phase_deg,
            self.ratio_db,
        )
        if not all(value is None or math.isfinite(value) for value in numeric_values):
            raise ValueError("Transport reading contains a non-finite value.")
