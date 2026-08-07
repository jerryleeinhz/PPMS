from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Callable

from ppms_control.acquisition import MeasurementEngine
from ppms_control.authorization import authorize_real_control
from ppms_control.config import AppConfig
from ppms_control.instruments import InstrumentBundle
from ppms_control.protocols import (
    prepare_field_sweep,
    prepare_frequency_sweep,
    prepare_temperature_field_sweep,
    prepare_voltage_sweep,
    run_field_sweep,
    run_frequency_sweep,
    run_temperature_field_sweep,
    run_voltage_sweep,
)
from ppms_control.real_instruments import build_real_bundle
from ppms_control.safety import CleanupError, SafeStation
from ppms_control.store import RunStore


class HardwareRunError(RuntimeError):
    """Raised when cleanup prevents a hardware run from completing safely."""


@dataclass(frozen=True)
class HardwareRunOutcome:
    run_id: str
    newly_measured_conditions: int
    status: str


BundleFactory = Callable[[AppConfig], InstrumentBundle]


def _default_bundle_factory(
    config: AppConfig,
) -> InstrumentBundle:
    return build_real_bundle(config)


def run_authorized_voltage_sweep(
    config: AppConfig,
    store: RunStore,
    *,
    confirmation: str,
    diagnostic_run_id: str,
    resume_run_id: str | None = None,
    bundle_factory: BundleFactory = _default_bundle_factory,
) -> HardwareRunOutcome:
    return _run_authorized_sweep(
        config,
        store,
        confirmation=confirmation,
        diagnostic_run_id=diagnostic_run_id,
        resume_run_id=resume_run_id,
        bundle_factory=bundle_factory,
        sweep_kind="voltage",
    )


def run_authorized_frequency_sweep(
    config: AppConfig,
    store: RunStore,
    *,
    confirmation: str,
    diagnostic_run_id: str,
    resume_run_id: str | None = None,
    bundle_factory: BundleFactory = _default_bundle_factory,
) -> HardwareRunOutcome:
    return _run_authorized_sweep(
        config,
        store,
        confirmation=confirmation,
        diagnostic_run_id=diagnostic_run_id,
        resume_run_id=resume_run_id,
        bundle_factory=bundle_factory,
        sweep_kind="frequency",
    )


def run_authorized_field_sweep(
    config: AppConfig,
    store: RunStore,
    *,
    confirmation: str,
    diagnostic_run_id: str,
    resume_run_id: str | None = None,
    bundle_factory: BundleFactory = _default_bundle_factory,
) -> HardwareRunOutcome:
    return _run_authorized_sweep(
        config,
        store,
        confirmation=confirmation,
        diagnostic_run_id=diagnostic_run_id,
        resume_run_id=resume_run_id,
        bundle_factory=bundle_factory,
        sweep_kind="field",
    )


def run_authorized_temperature_field_sweep(
    config: AppConfig,
    store: RunStore,
    *,
    confirmation: str,
    diagnostic_run_id: str,
    resume_run_id: str | None = None,
    bundle_factory: BundleFactory = _default_bundle_factory,
) -> HardwareRunOutcome:
    return _run_authorized_sweep(
        config,
        store,
        confirmation=confirmation,
        diagnostic_run_id=diagnostic_run_id,
        resume_run_id=resume_run_id,
        bundle_factory=bundle_factory,
        sweep_kind="temperature_field",
    )


def _run_authorized_sweep(
    config: AppConfig,
    store: RunStore,
    *,
    confirmation: str,
    diagnostic_run_id: str,
    resume_run_id: str | None,
    bundle_factory: BundleFactory,
    sweep_kind: str,
) -> HardwareRunOutcome:
    """Authorize, connect, prepare the PPMS, measure, audit, and clean up."""
    if sweep_kind == "voltage":
        protocol = "authorized_hardware_voltage_sweep"
    elif sweep_kind == "frequency":
        protocol = "authorized_hardware_frequency_sweep"
    elif sweep_kind == "field":
        protocol = "authorized_hardware_field_sweep"
    elif sweep_kind == "temperature_field":
        protocol = "authorized_hardware_temperature_field_sweep"
    else:
        raise ValueError(f"Unsupported hardware sweep: {sweep_kind}")
    authorization = authorize_real_control(
        config,
        store,
        confirmation=confirmation,
        diagnostic_run_id=diagnostic_run_id,
    )
    run_id = store.start_run(
        protocol=protocol,
        sample_name=config.runtime.sample_name,
        config_json=config.canonical_json(),
        station_snapshot_json=json.dumps({"state": "connecting"}, sort_keys=True),
        resume_run_id=resume_run_id,
    )
    store.record_event(
        run_id,
        "INFO",
        "hardware_control_authorized",
        {"diagnostic_run_id": authorization.diagnostic_run_id},
    )

    bundle: InstrumentBundle | None = None
    station: SafeStation | None = None
    measured = 0
    terminal_status = "failed"
    failure: BaseException | None = None
    cleanup_errors: tuple[CleanupError, ...] = ()
    try:
        bundle = bundle_factory(config)
        station = SafeStation(bundle, config)
        store.update_station_snapshot(
            run_id,
            json.dumps(station.qcodes_snapshot, default=str, sort_keys=True),
        )
        engine = MeasurementEngine(
            station,
            store,
            run_id,
            config.acquisition,
        )
        if sweep_kind == "voltage":
            prepare_voltage_sweep(station, config.voltage_sweep)
            measured = run_voltage_sweep(
                engine,
                store,
                run_id,
                config.voltage_sweep,
                frequency_hz=config.instruments.reference_frequency_hz,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        elif sweep_kind == "frequency":
            prepare_frequency_sweep(station, config.frequency_sweep)
            measured = run_frequency_sweep(
                engine,
                store,
                run_id,
                config.frequency_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        elif sweep_kind == "field":
            prepare_field_sweep(station, config.field_sweep)
            measured = run_field_sweep(
                engine,
                station,
                store,
                run_id,
                config.field_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        else:
            prepare_temperature_field_sweep(
                station,
                config.temperature_field_sweep,
            )
            measured = run_temperature_field_sweep(
                engine,
                station,
                store,
                run_id,
                config.temperature_field_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        terminal_status = "completed"
    except BaseException as exc:
        failure = exc
        terminal_status = "aborted" if isinstance(exc, KeyboardInterrupt) else "failed"
        store.record_event(
            run_id,
            "ERROR",
            "hardware_run_exception",
            {"error": f"{type(exc).__name__}: {exc}"},
        )
    finally:
        if station is not None:
            cleanup_errors = station.safe_shutdown()
            for item in cleanup_errors:
                store.record_event(
                    run_id,
                    "ERROR",
                    "cleanup_error",
                    {"step": item.step, "message": item.message},
                )
            try:
                store.record_event(
                    run_id,
                    "INFO",
                    "post_shutdown_physical_state",
                    asdict(station.read_physical_state()),
                )
            except Exception as exc:
                store.record_event(
                    run_id,
                    "WARNING",
                    "post_shutdown_state_unavailable",
                    {"error": str(exc)},
                )

        if bundle is not None:
            try:
                bundle.close()
            except Exception as exc:
                cleanup_errors += (CleanupError("bundle_close", str(exc)),)
                store.record_event(
                    run_id,
                    "ERROR",
                    "cleanup_error",
                    {"step": "bundle_close", "message": str(exc)},
                )
        if cleanup_errors:
            terminal_status = "failed"
        store.finish_run(run_id, terminal_status)

    if failure is not None:
        raise failure.with_traceback(failure.__traceback__)
    if cleanup_errors:
        raise HardwareRunError(
            "Hardware run cleanup failed: "
            + "; ".join(f"{item.step}: {item.message}" for item in cleanup_errors)
        )
    return HardwareRunOutcome(run_id, measured, terminal_status)
