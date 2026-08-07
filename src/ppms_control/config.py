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
class ConnectionConfig:
    visa_backend: str
    visa_timeout_ms: int
    sr830_address: str
    sr865a_address: str
    gate_top_address: str
    gate_bottom_address: str
    ppms_host: str
    ppms_port: int


@dataclass(frozen=True)
class SafetyLimits:
    source_voltage_min_v: float
    source_voltage_max_v: float
    source_safe_idle_voltage_v: float
    source_frequency_min_hz: float
    source_frequency_max_hz: float
    estimated_current_limit_a: float
    field_abs_limit_t: float
    field_rate_max_t_per_s: float
    field_shutdown_rate_t_per_s: float
    temperature_min_k: float
    temperature_max_k: float
    temperature_rate_max_k_per_min: float
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
    source_voltage_tolerance_v: float
    gate_voltage_tolerance_v: float


@dataclass(frozen=True)
class VoltageSweepConfig:
    start_voltage_v: float
    stop_voltage_v: float
    points: int
    target_temperature_k: float
    target_field_t: float
    temperature_rate_k_per_min: float
    field_rate_t_per_s: float
    stabilization_timeout_s: float
    stability_poll_s: float


@dataclass(frozen=True)
class FrequencySweepConfig:
    start_frequency_hz: float
    stop_frequency_hz: float
    points: int
    source_voltage_v: float
    target_temperature_k: float
    target_field_t: float
    temperature_rate_k_per_min: float
    field_rate_t_per_s: float
    stabilization_timeout_s: float
    stability_poll_s: float


@dataclass(frozen=True)
class FieldSweepConfig:
    start_field_t: float
    stop_field_t: float
    points: int
    source_voltage_v: float
    frequency_hz: float
    target_temperature_k: float
    temperature_rate_k_per_min: float
    field_rate_t_per_s: float
    stabilization_timeout_s: float
    stability_poll_s: float


@dataclass(frozen=True)
class TemperatureFieldSweepConfig:
    start_temperature_k: float
    stop_temperature_k: float
    temperature_points: int
    start_field_t: float
    stop_field_t: float
    field_points: int
    source_voltage_v: float
    frequency_hz: float
    temperature_rate_k_per_min: float
    field_rate_t_per_s: float
    stabilization_timeout_s: float
    stability_poll_s: float


@dataclass(frozen=True)
class GateSweepConfig:
    mode: str
    start_top_gate_v: float
    stop_top_gate_v: float
    top_gate_points: int
    start_bottom_gate_v: float
    stop_bottom_gate_v: float
    bottom_gate_points: int
    source_voltage_v: float
    frequency_hz: float
    target_temperature_k: float
    target_field_t: float
    temperature_rate_k_per_min: float
    field_rate_t_per_s: float
    gate_ramp_step_v: float
    gate_ramp_step_delay_s: float
    gate_settle_s: float
    stabilization_timeout_s: float
    stability_poll_s: float


@dataclass(frozen=True)
class DataConfig:
    database_path: Path


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    instruments: InstrumentConfig
    connections: ConnectionConfig
    safety: SafetyLimits
    acquisition: AcquisitionConfig
    voltage_sweep: VoltageSweepConfig
    frequency_sweep: FrequencySweepConfig
    field_sweep: FieldSweepConfig
    temperature_field_sweep: TemperatureFieldSweepConfig
    gate_sweep: GateSweepConfig
    data: DataConfig

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), default=str, separators=(",", ":"), sort_keys=True)


_ROOT_KEYS = {
    "runtime",
    "instruments",
    "connections",
    "safety",
    "acquisition",
    "voltage_sweep",
    "frequency_sweep",
    "field_sweep",
    "temperature_field_sweep",
    "gate_sweep",
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


def _integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise ConfigError(f"{path} must be an integer.")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{path} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{path} must be <= {maximum}.")
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
    connections_raw = _section(
        raw,
        "connections",
        {
            "visa_backend",
            "visa_timeout_ms",
            "sr830_address",
            "sr865a_address",
            "gate_top_address",
            "gate_bottom_address",
            "ppms_host",
            "ppms_port",
        },
    )
    safety_raw = _section(
        raw,
        "safety",
        {
            "source_voltage_min_v",
            "source_voltage_max_v",
            "source_safe_idle_voltage_v",
            "source_frequency_min_hz",
            "source_frequency_max_hz",
            "estimated_current_limit_a",
            "field_abs_limit_t",
            "field_rate_max_t_per_s",
            "field_shutdown_rate_t_per_s",
            "temperature_min_k",
            "temperature_max_k",
            "temperature_rate_max_k_per_min",
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
            "source_voltage_tolerance_v",
            "gate_voltage_tolerance_v",
        },
    )
    sweep_raw = _section(
        raw,
        "voltage_sweep",
        {
            "start_voltage_v",
            "stop_voltage_v",
            "points",
            "target_temperature_k",
            "target_field_t",
            "temperature_rate_k_per_min",
            "field_rate_t_per_s",
            "stabilization_timeout_s",
            "stability_poll_s",
        },
    )
    frequency_sweep_raw = _section(
        raw,
        "frequency_sweep",
        {
            "start_frequency_hz",
            "stop_frequency_hz",
            "points",
            "source_voltage_v",
            "target_temperature_k",
            "target_field_t",
            "temperature_rate_k_per_min",
            "field_rate_t_per_s",
            "stabilization_timeout_s",
            "stability_poll_s",
        },
    )
    field_sweep_raw = _section(
        raw,
        "field_sweep",
        {
            "start_field_t",
            "stop_field_t",
            "points",
            "source_voltage_v",
            "frequency_hz",
            "target_temperature_k",
            "temperature_rate_k_per_min",
            "field_rate_t_per_s",
            "stabilization_timeout_s",
            "stability_poll_s",
        },
    )
    temperature_field_sweep_raw = _section(
        raw,
        "temperature_field_sweep",
        {
            "start_temperature_k",
            "stop_temperature_k",
            "temperature_points",
            "start_field_t",
            "stop_field_t",
            "field_points",
            "source_voltage_v",
            "frequency_hz",
            "temperature_rate_k_per_min",
            "field_rate_t_per_s",
            "stabilization_timeout_s",
            "stability_poll_s",
        },
    )
    gate_sweep_raw = _section(
        raw,
        "gate_sweep",
        {
            "mode",
            "start_top_gate_v",
            "stop_top_gate_v",
            "top_gate_points",
            "start_bottom_gate_v",
            "stop_bottom_gate_v",
            "bottom_gate_points",
            "source_voltage_v",
            "frequency_hz",
            "target_temperature_k",
            "target_field_t",
            "temperature_rate_k_per_min",
            "field_rate_t_per_s",
            "gate_ramp_step_v",
            "gate_ramp_step_delay_s",
            "gate_settle_s",
            "stabilization_timeout_s",
            "stability_poll_s",
        },
    )
    data_raw = _section(raw, "data", {"database_path"})

    runtime = RuntimeConfig(
        simulation=_bool(runtime_raw["simulation"], "runtime.simulation"),
        seed=_integer(runtime_raw["seed"], "runtime.seed", minimum=0),
        sample_name=_text(runtime_raw["sample_name"], "runtime.sample_name"),
    )
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
    connections = ConnectionConfig(
        visa_backend=_text(connections_raw["visa_backend"], "connections.visa_backend"),
        visa_timeout_ms=_integer(
            connections_raw["visa_timeout_ms"],
            "connections.visa_timeout_ms",
            minimum=1,
        ),
        sr830_address=_text(connections_raw["sr830_address"], "connections.sr830_address"),
        sr865a_address=_text(
            connections_raw["sr865a_address"], "connections.sr865a_address"
        ),
        gate_top_address=_text(
            connections_raw["gate_top_address"], "connections.gate_top_address"
        ),
        gate_bottom_address=_text(
            connections_raw["gate_bottom_address"], "connections.gate_bottom_address"
        ),
        ppms_host=_text(connections_raw["ppms_host"], "connections.ppms_host"),
        ppms_port=_integer(
            connections_raw["ppms_port"],
            "connections.ppms_port",
            minimum=1,
            maximum=65535,
        ),
    )
    safety = SafetyLimits(
        source_voltage_min_v=_number(
            safety_raw["source_voltage_min_v"],
            "safety.source_voltage_min_v",
            positive=True,
        ),
        source_voltage_max_v=_number(
            safety_raw["source_voltage_max_v"],
            "safety.source_voltage_max_v",
            positive=True,
        ),
        source_safe_idle_voltage_v=_number(
            safety_raw["source_safe_idle_voltage_v"],
            "safety.source_safe_idle_voltage_v",
            positive=True,
        ),
        source_frequency_min_hz=_number(
            safety_raw["source_frequency_min_hz"],
            "safety.source_frequency_min_hz",
            positive=True,
        ),
        source_frequency_max_hz=_number(
            safety_raw["source_frequency_max_hz"],
            "safety.source_frequency_max_hz",
            positive=True,
        ),
        estimated_current_limit_a=_number(
            safety_raw["estimated_current_limit_a"],
            "safety.estimated_current_limit_a",
            positive=True,
        ),
        field_abs_limit_t=_number(
            safety_raw["field_abs_limit_t"], "safety.field_abs_limit_t", positive=True
        ),
        field_rate_max_t_per_s=_number(
            safety_raw["field_rate_max_t_per_s"],
            "safety.field_rate_max_t_per_s",
            positive=True,
        ),
        field_shutdown_rate_t_per_s=_number(
            safety_raw["field_shutdown_rate_t_per_s"],
            "safety.field_shutdown_rate_t_per_s",
            positive=True,
        ),
        temperature_min_k=_number(safety_raw["temperature_min_k"], "safety.temperature_min_k"),
        temperature_max_k=_number(safety_raw["temperature_max_k"], "safety.temperature_max_k"),
        temperature_rate_max_k_per_min=_number(
            safety_raw["temperature_rate_max_k_per_min"],
            "safety.temperature_rate_max_k_per_min",
            positive=True,
        ),
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
        source_voltage_tolerance_v=_number(
            acquisition_raw["source_voltage_tolerance_v"],
            "acquisition.source_voltage_tolerance_v",
            nonnegative=True,
        ),
        gate_voltage_tolerance_v=_number(
            acquisition_raw["gate_voltage_tolerance_v"],
            "acquisition.gate_voltage_tolerance_v",
            nonnegative=True,
        ),
    )
    sweep = VoltageSweepConfig(
        start_voltage_v=_number(
            sweep_raw["start_voltage_v"],
            "voltage_sweep.start_voltage_v",
            nonnegative=True,
        ),
        stop_voltage_v=_number(
            sweep_raw["stop_voltage_v"],
            "voltage_sweep.stop_voltage_v",
            nonnegative=True,
        ),
        points=_integer(sweep_raw["points"], "voltage_sweep.points", minimum=1),
        target_temperature_k=_number(
            sweep_raw["target_temperature_k"], "voltage_sweep.target_temperature_k"
        ),
        target_field_t=_number(sweep_raw["target_field_t"], "voltage_sweep.target_field_t"),
        temperature_rate_k_per_min=_number(
            sweep_raw["temperature_rate_k_per_min"],
            "voltage_sweep.temperature_rate_k_per_min",
            positive=True,
        ),
        field_rate_t_per_s=_number(
            sweep_raw["field_rate_t_per_s"],
            "voltage_sweep.field_rate_t_per_s",
            positive=True,
        ),
        stabilization_timeout_s=_number(
            sweep_raw["stabilization_timeout_s"],
            "voltage_sweep.stabilization_timeout_s",
            positive=True,
        ),
        stability_poll_s=_number(
            sweep_raw["stability_poll_s"],
            "voltage_sweep.stability_poll_s",
            nonnegative=True,
        ),
    )
    frequency_sweep = FrequencySweepConfig(
        start_frequency_hz=_number(
            frequency_sweep_raw["start_frequency_hz"],
            "frequency_sweep.start_frequency_hz",
            positive=True,
        ),
        stop_frequency_hz=_number(
            frequency_sweep_raw["stop_frequency_hz"],
            "frequency_sweep.stop_frequency_hz",
            positive=True,
        ),
        points=_integer(
            frequency_sweep_raw["points"],
            "frequency_sweep.points",
            minimum=1,
        ),
        source_voltage_v=_number(
            frequency_sweep_raw["source_voltage_v"],
            "frequency_sweep.source_voltage_v",
            nonnegative=True,
        ),
        target_temperature_k=_number(
            frequency_sweep_raw["target_temperature_k"],
            "frequency_sweep.target_temperature_k",
        ),
        target_field_t=_number(
            frequency_sweep_raw["target_field_t"],
            "frequency_sweep.target_field_t",
        ),
        temperature_rate_k_per_min=_number(
            frequency_sweep_raw["temperature_rate_k_per_min"],
            "frequency_sweep.temperature_rate_k_per_min",
            positive=True,
        ),
        field_rate_t_per_s=_number(
            frequency_sweep_raw["field_rate_t_per_s"],
            "frequency_sweep.field_rate_t_per_s",
            positive=True,
        ),
        stabilization_timeout_s=_number(
            frequency_sweep_raw["stabilization_timeout_s"],
            "frequency_sweep.stabilization_timeout_s",
            positive=True,
        ),
        stability_poll_s=_number(
            frequency_sweep_raw["stability_poll_s"],
            "frequency_sweep.stability_poll_s",
            nonnegative=True,
        ),
    )
    field_sweep = FieldSweepConfig(
        start_field_t=_number(
            field_sweep_raw["start_field_t"],
            "field_sweep.start_field_t",
        ),
        stop_field_t=_number(
            field_sweep_raw["stop_field_t"],
            "field_sweep.stop_field_t",
        ),
        points=_integer(field_sweep_raw["points"], "field_sweep.points", minimum=1),
        source_voltage_v=_number(
            field_sweep_raw["source_voltage_v"],
            "field_sweep.source_voltage_v",
            nonnegative=True,
        ),
        frequency_hz=_number(
            field_sweep_raw["frequency_hz"],
            "field_sweep.frequency_hz",
            positive=True,
        ),
        target_temperature_k=_number(
            field_sweep_raw["target_temperature_k"],
            "field_sweep.target_temperature_k",
        ),
        temperature_rate_k_per_min=_number(
            field_sweep_raw["temperature_rate_k_per_min"],
            "field_sweep.temperature_rate_k_per_min",
            positive=True,
        ),
        field_rate_t_per_s=_number(
            field_sweep_raw["field_rate_t_per_s"],
            "field_sweep.field_rate_t_per_s",
            positive=True,
        ),
        stabilization_timeout_s=_number(
            field_sweep_raw["stabilization_timeout_s"],
            "field_sweep.stabilization_timeout_s",
            positive=True,
        ),
        stability_poll_s=_number(
            field_sweep_raw["stability_poll_s"],
            "field_sweep.stability_poll_s",
            nonnegative=True,
        ),
    )
    temperature_field_sweep = TemperatureFieldSweepConfig(
        start_temperature_k=_number(
            temperature_field_sweep_raw["start_temperature_k"],
            "temperature_field_sweep.start_temperature_k",
        ),
        stop_temperature_k=_number(
            temperature_field_sweep_raw["stop_temperature_k"],
            "temperature_field_sweep.stop_temperature_k",
        ),
        temperature_points=_integer(
            temperature_field_sweep_raw["temperature_points"],
            "temperature_field_sweep.temperature_points",
            minimum=1,
        ),
        start_field_t=_number(
            temperature_field_sweep_raw["start_field_t"],
            "temperature_field_sweep.start_field_t",
        ),
        stop_field_t=_number(
            temperature_field_sweep_raw["stop_field_t"],
            "temperature_field_sweep.stop_field_t",
        ),
        field_points=_integer(
            temperature_field_sweep_raw["field_points"],
            "temperature_field_sweep.field_points",
            minimum=1,
        ),
        source_voltage_v=_number(
            temperature_field_sweep_raw["source_voltage_v"],
            "temperature_field_sweep.source_voltage_v",
            nonnegative=True,
        ),
        frequency_hz=_number(
            temperature_field_sweep_raw["frequency_hz"],
            "temperature_field_sweep.frequency_hz",
            positive=True,
        ),
        temperature_rate_k_per_min=_number(
            temperature_field_sweep_raw["temperature_rate_k_per_min"],
            "temperature_field_sweep.temperature_rate_k_per_min",
            positive=True,
        ),
        field_rate_t_per_s=_number(
            temperature_field_sweep_raw["field_rate_t_per_s"],
            "temperature_field_sweep.field_rate_t_per_s",
            positive=True,
        ),
        stabilization_timeout_s=_number(
            temperature_field_sweep_raw["stabilization_timeout_s"],
            "temperature_field_sweep.stabilization_timeout_s",
            positive=True,
        ),
        stability_poll_s=_number(
            temperature_field_sweep_raw["stability_poll_s"],
            "temperature_field_sweep.stability_poll_s",
            nonnegative=True,
        ),
    )
    gate_sweep = GateSweepConfig(
        mode=_text(gate_sweep_raw["mode"], "gate_sweep.mode"),
        start_top_gate_v=_number(
            gate_sweep_raw["start_top_gate_v"],
            "gate_sweep.start_top_gate_v",
        ),
        stop_top_gate_v=_number(
            gate_sweep_raw["stop_top_gate_v"],
            "gate_sweep.stop_top_gate_v",
        ),
        top_gate_points=_integer(
            gate_sweep_raw["top_gate_points"],
            "gate_sweep.top_gate_points",
            minimum=1,
        ),
        start_bottom_gate_v=_number(
            gate_sweep_raw["start_bottom_gate_v"],
            "gate_sweep.start_bottom_gate_v",
        ),
        stop_bottom_gate_v=_number(
            gate_sweep_raw["stop_bottom_gate_v"],
            "gate_sweep.stop_bottom_gate_v",
        ),
        bottom_gate_points=_integer(
            gate_sweep_raw["bottom_gate_points"],
            "gate_sweep.bottom_gate_points",
            minimum=1,
        ),
        source_voltage_v=_number(
            gate_sweep_raw["source_voltage_v"],
            "gate_sweep.source_voltage_v",
            nonnegative=True,
        ),
        frequency_hz=_number(
            gate_sweep_raw["frequency_hz"],
            "gate_sweep.frequency_hz",
            positive=True,
        ),
        target_temperature_k=_number(
            gate_sweep_raw["target_temperature_k"],
            "gate_sweep.target_temperature_k",
        ),
        target_field_t=_number(
            gate_sweep_raw["target_field_t"],
            "gate_sweep.target_field_t",
        ),
        temperature_rate_k_per_min=_number(
            gate_sweep_raw["temperature_rate_k_per_min"],
            "gate_sweep.temperature_rate_k_per_min",
            positive=True,
        ),
        field_rate_t_per_s=_number(
            gate_sweep_raw["field_rate_t_per_s"],
            "gate_sweep.field_rate_t_per_s",
            positive=True,
        ),
        gate_ramp_step_v=_number(
            gate_sweep_raw["gate_ramp_step_v"],
            "gate_sweep.gate_ramp_step_v",
            positive=True,
        ),
        gate_ramp_step_delay_s=_number(
            gate_sweep_raw["gate_ramp_step_delay_s"],
            "gate_sweep.gate_ramp_step_delay_s",
            nonnegative=True,
        ),
        gate_settle_s=_number(
            gate_sweep_raw["gate_settle_s"],
            "gate_sweep.gate_settle_s",
            nonnegative=True,
        ),
        stabilization_timeout_s=_number(
            gate_sweep_raw["stabilization_timeout_s"],
            "gate_sweep.stabilization_timeout_s",
            positive=True,
        ),
        stability_poll_s=_number(
            gate_sweep_raw["stability_poll_s"],
            "gate_sweep.stability_poll_s",
            nonnegative=True,
        ),
    )
    database_value = _text(data_raw["database_path"], "data.database_path")
    database_path = Path(database_value)
    if not database_path.is_absolute():
        database_path = (config_path.parent / database_path).resolve()
    data = DataConfig(database_path=database_path)

    if safety.temperature_min_k >= safety.temperature_max_k:
        raise ConfigError("safety.temperature_min_k must be below temperature_max_k.")
    if safety.source_voltage_min_v >= safety.source_voltage_max_v:
        raise ConfigError("Source voltage minimum must be below the maximum.")
    if safety.source_voltage_min_v < 0.004 or safety.source_voltage_max_v > 5.0:
        raise ConfigError("SR830 source voltage range must stay within 0.004 to 5.0 Vrms.")
    if safety.source_frequency_min_hz >= safety.source_frequency_max_hz:
        raise ConfigError("Source frequency minimum must be below the maximum.")
    if safety.source_frequency_min_hz < 0.001 or safety.source_frequency_max_hz > 102000.0:
        raise ConfigError("SR830 source frequency range must stay within 0.001 to 102000 Hz.")
    if not (
        safety.source_frequency_min_hz
        <= instruments.reference_frequency_hz
        <= safety.source_frequency_max_hz
    ):
        raise ConfigError("Reference frequency is outside the source frequency range.")
    if not (
        safety.source_voltage_min_v
        <= safety.source_safe_idle_voltage_v
        <= safety.source_voltage_max_v
    ):
        raise ConfigError("Source safe-idle voltage must be inside the source voltage range.")
    if (
        safety.source_safe_idle_voltage_v / instruments.series_resistance_ohm
        > safety.estimated_current_limit_a
    ):
        raise ConfigError("Source safe-idle voltage exceeds the estimated current limit.")
    if safety.field_shutdown_rate_t_per_s > safety.field_rate_max_t_per_s:
        raise ConfigError("Field shutdown rate must not exceed the maximum field rate.")
    for label, voltage in (
        ("start_voltage_v", sweep.start_voltage_v),
        ("stop_voltage_v", sweep.stop_voltage_v),
    ):
        if not safety.source_voltage_min_v <= voltage <= safety.source_voltage_max_v:
            raise ConfigError(f"voltage_sweep.{label} is outside the source voltage range.")
        if voltage / instruments.series_resistance_ohm > safety.estimated_current_limit_a:
            raise ConfigError(f"voltage_sweep.{label} exceeds the estimated current limit.")
    if sweep.points > 1 and sweep.start_voltage_v == sweep.stop_voltage_v:
        raise ConfigError("A multi-point voltage sweep must contain distinct setpoints.")
    if not safety.temperature_min_k <= sweep.target_temperature_k <= safety.temperature_max_k:
        raise ConfigError("Voltage-sweep target temperature is outside the safety range.")
    if abs(sweep.target_field_t) > safety.field_abs_limit_t:
        raise ConfigError("Voltage-sweep target field exceeds the safety limit.")
    if sweep.temperature_rate_k_per_min > safety.temperature_rate_max_k_per_min:
        raise ConfigError("Voltage-sweep temperature rate exceeds the safety limit.")
    if sweep.field_rate_t_per_s > safety.field_rate_max_t_per_s:
        raise ConfigError("Voltage-sweep field rate exceeds the safety limit.")
    for label, frequency_hz in (
        ("start_frequency_hz", frequency_sweep.start_frequency_hz),
        ("stop_frequency_hz", frequency_sweep.stop_frequency_hz),
    ):
        if not safety.source_frequency_min_hz <= frequency_hz <= safety.source_frequency_max_hz:
            raise ConfigError(f"frequency_sweep.{label} is outside the source frequency range.")
    if (
        frequency_sweep.points > 1
        and frequency_sweep.start_frequency_hz == frequency_sweep.stop_frequency_hz
    ):
        raise ConfigError("A multi-point frequency sweep must contain distinct setpoints.")
    if not (
        safety.source_voltage_min_v
        <= frequency_sweep.source_voltage_v
        <= safety.source_voltage_max_v
    ):
        raise ConfigError("Frequency-sweep source voltage is outside the source voltage range.")
    if (
        frequency_sweep.source_voltage_v / instruments.series_resistance_ohm
        > safety.estimated_current_limit_a
    ):
        raise ConfigError("Frequency-sweep source voltage exceeds the estimated current limit.")
    if not (
        safety.temperature_min_k
        <= frequency_sweep.target_temperature_k
        <= safety.temperature_max_k
    ):
        raise ConfigError("Frequency-sweep target temperature is outside the safety range.")
    if abs(frequency_sweep.target_field_t) > safety.field_abs_limit_t:
        raise ConfigError("Frequency-sweep target field exceeds the safety limit.")
    if frequency_sweep.temperature_rate_k_per_min > safety.temperature_rate_max_k_per_min:
        raise ConfigError("Frequency-sweep temperature rate exceeds the safety limit.")
    if frequency_sweep.field_rate_t_per_s > safety.field_rate_max_t_per_s:
        raise ConfigError("Frequency-sweep field rate exceeds the safety limit.")
    for label, field_t in (
        ("start_field_t", field_sweep.start_field_t),
        ("stop_field_t", field_sweep.stop_field_t),
    ):
        if abs(field_t) > safety.field_abs_limit_t:
            raise ConfigError(f"field_sweep.{label} exceeds the safety limit.")
    if field_sweep.points > 1 and field_sweep.start_field_t == field_sweep.stop_field_t:
        raise ConfigError("A multi-point field sweep must contain distinct setpoints.")
    if not safety.source_voltage_min_v <= field_sweep.source_voltage_v <= safety.source_voltage_max_v:
        raise ConfigError("Field-sweep source voltage is outside the source voltage range.")
    if (
        field_sweep.source_voltage_v / instruments.series_resistance_ohm
        > safety.estimated_current_limit_a
    ):
        raise ConfigError("Field-sweep source voltage exceeds the estimated current limit.")
    if not safety.source_frequency_min_hz <= field_sweep.frequency_hz <= safety.source_frequency_max_hz:
        raise ConfigError("Field-sweep frequency is outside the source frequency range.")
    if not safety.temperature_min_k <= field_sweep.target_temperature_k <= safety.temperature_max_k:
        raise ConfigError("Field-sweep target temperature is outside the safety range.")
    if field_sweep.temperature_rate_k_per_min > safety.temperature_rate_max_k_per_min:
        raise ConfigError("Field-sweep temperature rate exceeds the safety limit.")
    if field_sweep.field_rate_t_per_s > safety.field_rate_max_t_per_s:
        raise ConfigError("Field-sweep field rate exceeds the safety limit.")
    for label, temperature_k in (
        ("start_temperature_k", temperature_field_sweep.start_temperature_k),
        ("stop_temperature_k", temperature_field_sweep.stop_temperature_k),
    ):
        if not safety.temperature_min_k <= temperature_k <= safety.temperature_max_k:
            raise ConfigError(
                f"temperature_field_sweep.{label} is outside the safety range."
            )
    if (
        temperature_field_sweep.temperature_points > 1
        and temperature_field_sweep.start_temperature_k
        == temperature_field_sweep.stop_temperature_k
    ):
        raise ConfigError("A multi-point temperature grid must contain distinct setpoints.")
    for label, field_t in (
        ("start_field_t", temperature_field_sweep.start_field_t),
        ("stop_field_t", temperature_field_sweep.stop_field_t),
    ):
        if abs(field_t) > safety.field_abs_limit_t:
            raise ConfigError(f"temperature_field_sweep.{label} exceeds the safety limit.")
    if (
        temperature_field_sweep.field_points > 1
        and temperature_field_sweep.start_field_t == temperature_field_sweep.stop_field_t
    ):
        raise ConfigError("A multi-point field grid must contain distinct setpoints.")
    if not (
        safety.source_voltage_min_v
        <= temperature_field_sweep.source_voltage_v
        <= safety.source_voltage_max_v
    ):
        raise ConfigError(
            "Temperature-field source voltage is outside the source voltage range."
        )
    if (
        temperature_field_sweep.source_voltage_v / instruments.series_resistance_ohm
        > safety.estimated_current_limit_a
    ):
        raise ConfigError(
            "Temperature-field source voltage exceeds the estimated current limit."
        )
    if not (
        safety.source_frequency_min_hz
        <= temperature_field_sweep.frequency_hz
        <= safety.source_frequency_max_hz
    ):
        raise ConfigError("Temperature-field frequency is outside the source frequency range.")
    if (
        temperature_field_sweep.temperature_rate_k_per_min
        > safety.temperature_rate_max_k_per_min
    ):
        raise ConfigError("Temperature-field temperature rate exceeds the safety limit.")
    if temperature_field_sweep.field_rate_t_per_s > safety.field_rate_max_t_per_s:
        raise ConfigError("Temperature-field field rate exceeds the safety limit.")
    if safety.gate_leakage_limit_a >= safety.gate_compliance_limit_a:
        raise ConfigError(
            "safety.gate_leakage_limit_a must be below gate_compliance_limit_a."
        )
    if gate_sweep.mode not in {"grid", "paired"}:
        raise ConfigError("gate_sweep.mode must be 'grid' or 'paired'.")
    for label, gate_v in (
        ("start_top_gate_v", gate_sweep.start_top_gate_v),
        ("stop_top_gate_v", gate_sweep.stop_top_gate_v),
        ("start_bottom_gate_v", gate_sweep.start_bottom_gate_v),
        ("stop_bottom_gate_v", gate_sweep.stop_bottom_gate_v),
    ):
        if abs(gate_v) > safety.gate_voltage_limit_v:
            raise ConfigError(f"gate_sweep.{label} exceeds the gate-voltage limit.")
    if gate_sweep.mode == "grid":
        if (
            gate_sweep.top_gate_points > 1
            and gate_sweep.start_top_gate_v == gate_sweep.stop_top_gate_v
        ):
            raise ConfigError("A multi-point top-gate sweep must contain distinct setpoints.")
        if (
            gate_sweep.bottom_gate_points > 1
            and gate_sweep.start_bottom_gate_v == gate_sweep.stop_bottom_gate_v
        ):
            raise ConfigError("A multi-point bottom-gate sweep must contain distinct setpoints.")
    else:
        if gate_sweep.top_gate_points != gate_sweep.bottom_gate_points:
            raise ConfigError("A paired gate sweep requires equal top/bottom point counts.")
        if (
            gate_sweep.top_gate_points > 1
            and gate_sweep.start_top_gate_v == gate_sweep.stop_top_gate_v
            and gate_sweep.start_bottom_gate_v == gate_sweep.stop_bottom_gate_v
        ):
            raise ConfigError("A multi-point paired gate sweep must move at least one gate.")
    gate_sweep_has_nonzero_voltage = any(
        gate_v != 0.0
        for gate_v in (
            gate_sweep.start_top_gate_v,
            gate_sweep.stop_top_gate_v,
            gate_sweep.start_bottom_gate_v,
            gate_sweep.stop_bottom_gate_v,
        )
    )
    if not (
        safety.temperature_min_k
        <= gate_sweep.target_temperature_k
        <= safety.temperature_max_k
    ):
        raise ConfigError("Gate-sweep target temperature is outside the safety range.")
    if (
        gate_sweep_has_nonzero_voltage
        and gate_sweep.target_temperature_k > safety.gate_temperature_limit_k
    ):
        raise ConfigError("Non-zero gate sweeps must stay below the gate-temperature limit.")
    if abs(gate_sweep.target_field_t) > safety.field_abs_limit_t:
        raise ConfigError("Gate-sweep target field exceeds the safety limit.")
    if not (
        safety.source_voltage_min_v
        <= gate_sweep.source_voltage_v
        <= safety.source_voltage_max_v
    ):
        raise ConfigError("Gate-sweep source voltage is outside the source voltage range.")
    if (
        gate_sweep.source_voltage_v / instruments.series_resistance_ohm
        > safety.estimated_current_limit_a
    ):
        raise ConfigError("Gate-sweep source voltage exceeds the estimated current limit.")
    if not (
        safety.source_frequency_min_hz
        <= gate_sweep.frequency_hz
        <= safety.source_frequency_max_hz
    ):
        raise ConfigError("Gate-sweep frequency is outside the source frequency range.")
    if gate_sweep.temperature_rate_k_per_min > safety.temperature_rate_max_k_per_min:
        raise ConfigError("Gate-sweep temperature rate exceeds the safety limit.")
    if gate_sweep.field_rate_t_per_s > safety.field_rate_max_t_per_s:
        raise ConfigError("Gate-sweep field rate exceeds the safety limit.")
    if not safety.temperature_min_k <= instruments.initial_temperature_k <= safety.temperature_max_k:
        raise ConfigError("Initial simulated temperature is outside the safety range.")
    if abs(instruments.initial_field_t) > safety.field_abs_limit_t:
        raise ConfigError("Initial simulated field exceeds the safety limit.")

    visa_addresses = (
        connections.sr830_address,
        connections.sr865a_address,
        connections.gate_top_address,
        connections.gate_bottom_address,
    )
    if len(set(visa_addresses)) != len(visa_addresses):
        raise ConfigError("Every VISA instrument must have a distinct address.")
    if not runtime.simulation:
        if "CHANGE_ME" in runtime.sample_name.upper():
            raise ConfigError("runtime.sample_name is still a placeholder.")
        for label, endpoint in (
            ("sr830_address", connections.sr830_address),
            ("sr865a_address", connections.sr865a_address),
            ("gate_top_address", connections.gate_top_address),
            ("gate_bottom_address", connections.gate_bottom_address),
        ):
            endpoint_upper = endpoint.upper()
            if "CHANGE_ME" in endpoint_upper or endpoint_upper.startswith("SIMULATED::"):
                raise ConfigError(f"connections.{label} is still a placeholder.")

    return AppConfig(
        runtime,
        instruments,
        connections,
        safety,
        acquisition,
        sweep,
        frequency_sweep,
        field_sweep,
        temperature_field_sweep,
        gate_sweep,
        data,
    )
