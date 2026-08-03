from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math


@dataclass(frozen=True)
class MeasurementCondition:
    sequence_index: int
    current_a: float
    temperature_k: float
    field_t: float

    @property
    def condition_id(self) -> str:
        def canonical(value: float) -> float:
            return 0.0 if value == 0 else value

        payload = json.dumps(
            {
                "current_a": canonical(self.current_a),
                "field_t": canonical(self.field_t),
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
    xx_1w: LockinReading
    xy_3w: LockinReading


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
