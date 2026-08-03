from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import tomllib
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when configuration is incomplete, ambiguous, or unsafe."""


@dataclass(frozen=True)
class RuntimeConfig:
    simulation: bool
    seed: int
    sample_name: str


@dataclass(frozen=True)
class InstrumentConfig:
    reference_frequency_hz: float
    series_resistance_ohm: float
    initial_temperature_k: float
    initial_field_t: float


@dataclass(frozen=True)
class SafetyLimits:
    normal_current_limit_a: float
    diagnostic_current_limit_a: float
    field_abs_limit_t: float
    temperature_min_k: float
    temperature_max_k: float
    gate_temperature_limit_k: float
    gate_voltage_limit_v: float
    gate_compliance_limit_a: float
    gate_leakage_limit_a: float


@dataclass(frozen=True)
class AcquisitionConfig:
    averages: int
    sample_interval_s: float
    settle_s: float
    max_attempts: int
    reference_frequency_tolerance_hz: float
    noise_limit_v: float
    temperature_tolerance_k: float
    field_tolerance_t: float


@dataclass(frozen=True)
class CurrentSweepConfig:
    start_current_a: float
    stop_current_a: float
    points: int
    target_temperature_k: float
    target_field_t: float


@dataclass(frozen=True)
class DataConfig:
    database_path: Path


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    instruments: InstrumentConfig
    safety: SafetyLimits
    acquisition: AcquisitionConfig
    current_sweep: CurrentSweepConfig
    data: DataConfig

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), default=str, separators=(",", ":"), sort_keys=True)


_ROOT_KEYS = {
    "runtime",
    "instruments",
    "safety",
    "acquisition",
    "current_sweep",
    "data",
}


def _section(root: Mapping[str, Any], name: str, keys: set[str]) -> Mapping[str, Any]:
    value = root.get(name)
    if not isinstance(value, Mapping):
        raise ConfigError(f"Missing or invalid [{name}] section.")
    actual = set(value)
    missing = keys - actual
    unknown = actual - keys
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise ConfigError(f"Invalid [{name}] fields: {', '.join(details)}.")
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{path} must be a boolean.")
    return value


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ConfigError(f"{path} must be an integer.")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path} must be >= {minimum}.")
    return value


def _number(value: Any, path: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be a number.")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{path} must be finite.")
    if positive and result <= 0:
        raise ConfigError(f"{path} must be > 0.")
    if nonnegative and result < 0:
        raise ConfigError(f"{path} must be >= 0.")
    return result


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} must be a non-empty string.")
    return value.strip()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc

    unknown_root = set(raw) - _ROOT_KEYS
    missing_root = _ROOT_KEYS - set(raw)
    if missing_root or unknown_root:
        raise ConfigError(
            f"Invalid root sections: missing {sorted(missing_root)}, unknown {sorted(unknown_root)}."
        )

    runtime_raw = _section(raw, "runtime", {"simulation", "seed", "sample_name"})
    instruments_raw = _section(
        raw,
        "instruments",
        {
            "reference_frequency_hz",
            "series_resistance_ohm",
            "initial_temperature_k",
            "initial_field_t",
        },
    )
    safety_raw = _section(
        raw,
        "safety",
        {
            "normal_current_limit_a",
            "diagnostic_current_limit_a",
            "field_abs_limit_t",
            "temperature_min_k",
            "temperature_max_k",
            "gate_temperature_limit_k",
            "gate_voltage_limit_v",
            "gate_compliance_limit_a",
            "gate_leakage_limit_a",
        },
    )
    acquisition_raw = _section(
        raw,
        "acquisition",
        {
            "averages",
            "sample_interval_s",
            "settle_s",
            "max_attempts",
            "reference_frequency_tolerance_hz",
            "noise_limit_v",
            "temperature_tolerance_k",
            "field_tolerance_t",
        },
    )
    sweep_raw = _section(
        raw,
        "current_sweep",
        {
            "start_current_a",
            "stop_current_a",
            "points",
            "target_temperature_k",
            "target_field_t",
        },
    )
    data_raw = _section(raw, "data", {"database_path"})

    runtime = RuntimeConfig(
        simulation=_bool(runtime_raw["simulation"], "runtime.simulation"),
        seed=_integer(runtime_raw["seed"], "runtime.seed", minimum=0),
        sample_name=_text(runtime_raw["sample_name"], "runtime.sample_name"),
    )
    if not runtime.simulation:
        raise ConfigError("Real-hardware mode is not implemented in milestone V1.")

    instruments = InstrumentConfig(
        reference_frequency_hz=_number(
            instruments_raw["reference_frequency_hz"],
            "instruments.reference_frequency_hz",
            positive=True,
        ),
        series_resistance_ohm=_number(
            instruments_raw["series_resistance_ohm"],
            "instruments.series_resistance_ohm",
            positive=True,
        ),
        initial_temperature_k=_number(
            instruments_raw["initial_temperature_k"], "instruments.initial_temperature_k"
        ),
        initial_field_t=_number(instruments_raw["initial_field_t"], "instruments.initial_field_t"),
    )
    safety = SafetyLimits(
        normal_current_limit_a=_number(
            safety_raw["normal_current_limit_a"], "safety.normal_current_limit_a", positive=True
        ),
        diagnostic_current_limit_a=_number(
            safety_raw["diagnostic_current_limit_a"],
            "safety.diagnostic_current_limit_a",
            positive=True,
        ),
        field_abs_limit_t=_number(
            safety_raw["field_abs_limit_t"], "safety.field_abs_limit_t", positive=True
        ),
        temperature_min_k=_number(safety_raw["temperature_min_k"], "safety.temperature_min_k"),
        temperature_max_k=_number(safety_raw["temperature_max_k"], "safety.temperature_max_k"),
        gate_temperature_limit_k=_number(
            safety_raw["gate_temperature_limit_k"], "safety.gate_temperature_limit_k"
        ),
        gate_voltage_limit_v=_number(
            safety_raw["gate_voltage_limit_v"], "safety.gate_voltage_limit_v", positive=True
        ),
        gate_compliance_limit_a=_number(
            safety_raw["gate_compliance_limit_a"],
            "safety.gate_compliance_limit_a",
            positive=True,
        ),
        gate_leakage_limit_a=_number(
            safety_raw["gate_leakage_limit_a"], "safety.gate_leakage_limit_a", positive=True
        ),
    )
    acquisition = AcquisitionConfig(
        averages=_integer(acquisition_raw["averages"], "acquisition.averages", minimum=1),
        sample_interval_s=_number(
            acquisition_raw["sample_interval_s"], "acquisition.sample_interval_s", nonnegative=True
        ),
        settle_s=_number(acquisition_raw["settle_s"], "acquisition.settle_s", nonnegative=True),
        max_attempts=_integer(
            acquisition_raw["max_attempts"], "acquisition.max_attempts", minimum=1
        ),
        reference_frequency_tolerance_hz=_number(
            acquisition_raw["reference_frequency_tolerance_hz"],
            "acquisition.reference_frequency_tolerance_hz",
            nonnegative=True,
        ),
        noise_limit_v=_number(
            acquisition_raw["noise_limit_v"], "acquisition.noise_limit_v", positive=True
        ),
        temperature_tolerance_k=_number(
            acquisition_raw["temperature_tolerance_k"],
            "acquisition.temperature_tolerance_k",
            nonnegative=True,
        ),
        field_tolerance_t=_number(
            acquisition_raw["field_tolerance_t"],
            "acquisition.field_tolerance_t",
            nonnegative=True,
        ),
    )
    sweep = CurrentSweepConfig(
        start_current_a=_number(sweep_raw["start_current_a"], "current_sweep.start_current_a"),
        stop_current_a=_number(sweep_raw["stop_current_a"], "current_sweep.stop_current_a"),
        points=_integer(sweep_raw["points"], "current_sweep.points", minimum=1),
        target_temperature_k=_number(
            sweep_raw["target_temperature_k"], "current_sweep.target_temperature_k"
        ),
        target_field_t=_number(sweep_raw["target_field_t"], "current_sweep.target_field_t"),
    )
    database_value = _text(data_raw["database_path"], "data.database_path")
    database_path = Path(database_value)
    if not database_path.is_absolute():
        database_path = (config_path.parent / database_path).resolve()
    data = DataConfig(database_path=database_path)

    if safety.temperature_min_k >= safety.temperature_max_k:
        raise ConfigError("safety.temperature_min_k must be below temperature_max_k.")
    if safety.normal_current_limit_a > safety.diagnostic_current_limit_a:
        raise ConfigError("Normal current limit must not exceed diagnostic current limit.")
    for label, current in (
        ("start_current_a", sweep.start_current_a),
        ("stop_current_a", sweep.stop_current_a),
    ):
        if abs(current) > safety.normal_current_limit_a:
            raise ConfigError(f"current_sweep.{label} exceeds the normal current limit.")
    if sweep.points > 1 and sweep.start_current_a == sweep.stop_current_a:
        raise ConfigError("A multi-point current sweep must contain distinct setpoints.")
    if not safety.temperature_min_k <= sweep.target_temperature_k <= safety.temperature_max_k:
        raise ConfigError("Current-sweep target temperature is outside the safety range.")
    if abs(sweep.target_field_t) > safety.field_abs_limit_t:
        raise ConfigError("Current-sweep target field exceeds the safety limit.")
    if not safety.temperature_min_k <= instruments.initial_temperature_k <= safety.temperature_max_k:
        raise ConfigError("Initial simulated temperature is outside the safety range.")
    if abs(instruments.initial_field_t) > safety.field_abs_limit_t:
        raise ConfigError("Initial simulated field exceeds the safety limit.")

    return AppConfig(runtime, instruments, safety, acquisition, sweep, data)
