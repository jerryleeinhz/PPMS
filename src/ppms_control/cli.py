from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Callable, Sequence

from ppms_control.acquisition import MeasurementEngine
from ppms_control.authorization import AuthorizationError
from ppms_control.config import ConfigError, load_config
from ppms_control.diagnostics import DiagnosticError, DiagnosticResult, diagnose_hardware
from ppms_control.eto_data import EtoDataError, load_eto_data
from ppms_control.eto_follow import ingest_eto_increment
from ppms_control.hardware_run import (
    HardwareRunError,
    run_authorized_field_sweep,
    run_authorized_frequency_sweep,
    run_authorized_gate_sweep,
    run_authorized_temperature_field_sweep,
    run_authorized_voltage_sweep,
)
from ppms_control.instruments import build_simulated_bundle
from ppms_control.ole_inspection import OleInspectionError, inspect_active_multivu_ole
from ppms_control.plotting import (
    PlotDataError,
    generate_publication_plots,
    load_eto_path,
    load_gate_calibration,
    load_sqlite_run,
)
from ppms_control.protocols import (
    prepare_field_sweep,
    prepare_frequency_sweep,
    prepare_gate_sweep,
    prepare_temperature_field_sweep,
    prepare_voltage_sweep,
    run_field_sweep,
    run_frequency_sweep,
    run_gate_sweep,
    run_temperature_field_sweep,
    run_voltage_sweep,
)
from ppms_control.safety import SafeStation
from ppms_control.store import RunStore, StoreError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppms-control")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="Validate a TOML configuration")
    validate.add_argument("config", type=Path)

    simulate = subparsers.add_parser("simulate", help="Run the SR830 voltage-sweep simulation")
    simulate.add_argument("config", type=Path)
    simulate.add_argument("--resume-run", default=None)

    simulate_frequency = subparsers.add_parser(
        "simulate-frequency",
        help="Run the SR830 frequency-sweep simulation",
    )
    simulate_frequency.add_argument("config", type=Path)
    simulate_frequency.add_argument("--resume-run", default=None)

    simulate_field = subparsers.add_parser(
        "simulate-field",
        help="Run the fixed-excitation magnetic-field sweep simulation",
    )
    simulate_field.add_argument("config", type=Path)
    simulate_field.add_argument("--resume-run", default=None)

    simulate_temperature_field = subparsers.add_parser(
        "simulate-temperature-field",
        help="Run the fixed-excitation temperature-field grid simulation",
    )
    simulate_temperature_field.add_argument("config", type=Path)
    simulate_temperature_field.add_argument("--resume-run", default=None)

    simulate_gate = subparsers.add_parser(
        "simulate-gate",
        help="Run the fixed-environment dual-gate grid simulation",
    )
    simulate_gate.add_argument("config", type=Path)
    simulate_gate.add_argument("--resume-run", default=None)

    diagnose = subparsers.add_parser(
        "diagnose-hardware",
        help="Run identification and state queries without set commands",
    )
    diagnose.add_argument("config", type=Path)

    hardware = subparsers.add_parser(
        "run-hardware",
        help="Run the authorized SR830 voltage sweep on real hardware",
    )
    hardware.add_argument("config", type=Path)
    hardware.add_argument("--diagnostic-run-id", required=True)
    hardware.add_argument("--confirm", required=True)
    hardware.add_argument("--resume-run", default=None)

    hardware_frequency = subparsers.add_parser(
        "run-hardware-frequency",
        help="Run the authorized SR830 frequency sweep on real hardware",
    )
    hardware_frequency.add_argument("config", type=Path)
    hardware_frequency.add_argument("--diagnostic-run-id", required=True)
    hardware_frequency.add_argument("--confirm", required=True)
    hardware_frequency.add_argument("--resume-run", default=None)

    hardware_field = subparsers.add_parser(
        "run-hardware-field",
        help="Run the authorized magnetic-field sweep on real hardware",
    )
    hardware_field.add_argument("config", type=Path)
    hardware_field.add_argument("--diagnostic-run-id", required=True)
    hardware_field.add_argument("--confirm", required=True)
    hardware_field.add_argument("--resume-run", default=None)

    hardware_temperature_field = subparsers.add_parser(
        "run-hardware-temperature-field",
        help="Run the authorized temperature-field grid on real hardware",
    )
    hardware_temperature_field.add_argument("config", type=Path)
    hardware_temperature_field.add_argument("--diagnostic-run-id", required=True)
    hardware_temperature_field.add_argument("--confirm", required=True)
    hardware_temperature_field.add_argument("--resume-run", default=None)

    hardware_gate = subparsers.add_parser(
        "run-hardware-gate",
        help="Run the authorized dual-gate grid on real hardware",
    )
    hardware_gate.add_argument("config", type=Path)
    hardware_gate.add_argument("--diagnostic-run-id", required=True)
    hardware_gate.add_argument("--confirm", required=True)
    hardware_gate.add_argument("--resume-run", default=None)

    export = subparsers.add_parser("export", help="Export accepted attempts to CSV")
    export.add_argument("database", type=Path)
    export.add_argument("run_id")
    export.add_argument("destination", type=Path)

    export_samples = subparsers.add_parser(
        "export-samples",
        help="Export timestamped raw instrument states and readings to CSV",
    )
    export_samples.add_argument("database", type=Path)
    export_samples.add_argument("run_id")
    export_samples.add_argument("destination", type=Path)

    export_transport = subparsers.add_parser(
        "export-transport",
        help="Export the backend-independent transport long table to CSV",
    )
    export_transport.add_argument("database", type=Path)
    export_transport.add_argument("run_id")
    export_transport.add_argument("destination", type=Path)

    export_transport_summary = subparsers.add_parser(
        "export-transport-summary",
        help="Export averaged plot-ready transport series to CSV",
    )
    export_transport_summary.add_argument("database", type=Path)
    export_transport_summary.add_argument("run_id")
    export_transport_summary.add_argument("destination", type=Path)

    plot_data = subparsers.add_parser(
        "plot-data",
        help="Generate publication-analysis figures from a SQLite run or ETO .dat path",
    )
    plot_data.add_argument("source", type=Path)
    plot_data.add_argument("output_dir", type=Path)
    plot_data.add_argument(
        "--run-id",
        help="Required when source is a SQLite database",
    )
    plot_data.add_argument("--channel-1-role", choices=("xx", "xy"))
    plot_data.add_argument("--channel-2-role", choices=("xx", "xy"))
    plot_data.add_argument(
        "--gate-calibration",
        type=Path,
        help="Optional strict TOML calibration used only for the n-D map",
    )
    plot_data.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=("png", "pdf"),
        help="Repeat to select outputs; defaults to both PNG and PDF",
    )

    inspect_eto = subparsers.add_parser(
        "inspect-eto-data",
        help="Validate and summarize a MultiVu Electrical Transport Option .dat file",
    )
    inspect_eto.add_argument("data_file", type=Path)

    follow_eto = subparsers.add_parser(
        "follow-eto-data",
        help="Incrementally ingest a growing ETO .dat file into SQLite",
    )
    follow_eto.add_argument("data_file", type=Path)
    follow_eto.add_argument("database", type=Path)
    follow_eto.add_argument("--sample-name", required=True)
    follow_eto.add_argument("--channel-1-role", required=True, choices=("xx", "xy"))
    follow_eto.add_argument("--channel-2-role", required=True, choices=("xx", "xy"))
    follow_eto.add_argument("--resume-run", default=None)
    follow_eto.add_argument("--poll-s", type=float, default=1.0)
    follow_eto.add_argument("--stop-after-idle-s", type=float, default=None)
    follow_eto.add_argument(
        "--once",
        action="store_true",
        help="Ingest currently available complete rows and exit",
    )
    follow_eto.add_argument(
        "--final",
        action="store_true",
        help="With --once, also consume a final row without a newline",
    )

    inspect_ole = subparsers.add_parser(
        "inspect-multivu-ole",
        help="Read type information from an already-running MultiVu OLE object",
    )
    inspect_ole.add_argument(
        "--progid",
        default="QD.MULTIVU.DYNACOOL.1",
        help="Active MultiVu COM ProgID; the default is DynaCool",
    )
    return parser


def _simulate(
    config_path: Path,
    resume_run_id: str | None,
    *,
    sweep_kind: str = "voltage",
) -> int:
    config = load_config(config_path)
    if not config.runtime.simulation:
        raise ConfigError("The simulate command requires runtime.simulation = true.")
    bundle = build_simulated_bundle(config)
    safe_station = SafeStation(bundle, config)
    store = RunStore(config.data.database_path)
    run_id: str | None = None
    terminal_status = "failed"
    exit_code = 1
    output: dict[str, object] | None = None
    try:
        snapshot_json = json.dumps(safe_station.qcodes_snapshot, default=str, sort_keys=True)
        if sweep_kind == "voltage":
            protocol = "fixed_environment_voltage_sweep"
        elif sweep_kind == "frequency":
            protocol = "fixed_environment_frequency_sweep"
        elif sweep_kind == "field":
            protocol = "fixed_excitation_field_sweep"
        elif sweep_kind == "temperature_field":
            protocol = "fixed_excitation_temperature_field_sweep"
        elif sweep_kind == "gate":
            protocol = "fixed_environment_gate_sweep"
        else:
            raise ValueError(f"Unsupported simulation sweep: {sweep_kind}")
        run_id = store.start_run(
            protocol=protocol,
            sample_name=config.runtime.sample_name,
            config_json=config.canonical_json(),
            station_snapshot_json=snapshot_json,
            resume_run_id=resume_run_id,
        )
        engine = MeasurementEngine(
            safe_station,
            store,
            run_id,
            config.acquisition,
        )
        if sweep_kind == "voltage":
            prepare_voltage_sweep(safe_station, config.voltage_sweep)
            measured = run_voltage_sweep(
                engine,
                store,
                run_id,
                config.voltage_sweep,
                frequency_hz=config.instruments.reference_frequency_hz,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        elif sweep_kind == "frequency":
            prepare_frequency_sweep(safe_station, config.frequency_sweep)
            measured = run_frequency_sweep(
                engine,
                store,
                run_id,
                config.frequency_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        elif sweep_kind == "field":
            prepare_field_sweep(safe_station, config.field_sweep)
            measured = run_field_sweep(
                engine,
                safe_station,
                store,
                run_id,
                config.field_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        elif sweep_kind == "temperature_field":
            prepare_temperature_field_sweep(
                safe_station,
                config.temperature_field_sweep,
            )
            measured = run_temperature_field_sweep(
                engine,
                safe_station,
                store,
                run_id,
                config.temperature_field_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        else:
            prepare_gate_sweep(safe_station, config.gate_sweep)
            measured = run_gate_sweep(
                engine,
                safe_station,
                store,
                run_id,
                config.gate_sweep,
                series_resistance_ohm=config.instruments.series_resistance_ohm,
            )
        terminal_status = "completed"
        exit_code = 0
        output = {
            "database": str(config.data.database_path),
            "newly_measured_conditions": measured,
            "run_id": run_id,
            "status": terminal_status,
        }
    except KeyboardInterrupt:
        terminal_status = "aborted"
        print("Simulation aborted by user.", file=sys.stderr)
        exit_code = 130
    except Exception as exc:
        if run_id is not None:
            store.record_event(run_id, "ERROR", "run_exception", {"error": str(exc)})
        print(f"Simulation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        cleanup_errors = safe_station.safe_shutdown()
        if cleanup_errors:
            terminal_status = "failed"
            exit_code = 1
            if run_id is not None:
                for item in cleanup_errors:
                    store.record_event(
                        run_id,
                        "ERROR",
                        "cleanup_error",
                        {"step": item.step, "message": item.message},
                    )
            print(
                "Cleanup errors: " + "; ".join(f"{item.step}: {item.message}" for item in cleanup_errors),
                file=sys.stderr,
            )
        if run_id is not None:
            store.finish_run(run_id, terminal_status)
        store.close()
        bundle.close()
    if output is not None:
        output["status"] = terminal_status
        print(json.dumps(output, sort_keys=True))
    return exit_code


def _diagnose_hardware_command(
    config_path: Path,
    *,
    runner: Callable[..., tuple[DiagnosticResult, ...]] = diagnose_hardware,
) -> int:
    config = load_config(config_path)
    if config.runtime.simulation:
        raise DiagnosticError("Hardware diagnostics require runtime.simulation = false.")

    with RunStore(config.data.database_path) as store:
        run_id = store.start_run(
            protocol="read_only_hardware_diagnostic",
            sample_name=config.runtime.sample_name,
            config_json=config.canonical_json(),
            station_snapshot_json=json.dumps(
                {"mode": "read-only", "qcodes_station_created": False},
                sort_keys=True,
            ),
        )
        try:
            results = runner(config)
            success = all(result.ok for result in results)
            for result in results:
                store.record_event(
                    run_id,
                    "INFO" if result.ok else "ERROR",
                    "hardware_diagnostic_result",
                    result.as_dict(),
                )
            store.finish_run(run_id, "completed" if success else "failed")
        except BaseException as exc:
            status = "aborted" if isinstance(exc, KeyboardInterrupt) else "failed"
            store.record_event(
                run_id,
                "ERROR",
                "hardware_diagnostic_exception",
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            store.finish_run(run_id, status)
            raise

    print(
        json.dumps(
            {
                "database": str(config.data.database_path),
                "mode": "read-only",
                "run_id": run_id,
                "success": success,
                "results": [result.as_dict() for result in results],
            },
            sort_keys=True,
        )
    )
    return 0 if success else 3


def _run_hardware_command(
    config_path: Path,
    *,
    diagnostic_run_id: str,
    confirmation: str,
    resume_run_id: str | None,
    sweep_kind: str = "voltage",
) -> int:
    config = load_config(config_path)
    if config.runtime.simulation:
        raise ConfigError("The run-hardware command requires runtime.simulation = false.")
    runners = {
        "voltage": run_authorized_voltage_sweep,
        "frequency": run_authorized_frequency_sweep,
        "field": run_authorized_field_sweep,
        "temperature_field": run_authorized_temperature_field_sweep,
        "gate": run_authorized_gate_sweep,
    }
    try:
        runner = runners[sweep_kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported hardware sweep: {sweep_kind}") from exc
    with RunStore(config.data.database_path) as store:
        outcome = runner(
            config,
            store,
            confirmation=confirmation,
            diagnostic_run_id=diagnostic_run_id,
            resume_run_id=resume_run_id,
        )
    print(
        json.dumps(
            {
                "database": str(config.data.database_path),
                "newly_measured_conditions": outcome.newly_measured_conditions,
                "run_id": outcome.run_id,
                "status": outcome.status,
            },
            sort_keys=True,
        )
    )
    return 0


def _plot_data_command(
    source: Path,
    output_dir: Path,
    *,
    run_id: str | None,
    channel_1_role: str | None,
    channel_2_role: str | None,
    gate_calibration: Path | None,
    formats: Sequence[str] | None,
) -> int:
    resolved = source.resolve()
    is_eto = resolved.is_dir() or resolved.suffix.lower() == ".dat"
    if is_eto:
        if run_id is not None:
            raise PlotDataError("--run-id is only valid for a SQLite source.")
        if channel_1_role is None or channel_2_role is None:
            raise PlotDataError(
                "ETO plotting requires --channel-1-role and --channel-2-role."
            )
        dataset = load_eto_path(
            resolved,
            {1: channel_1_role, 2: channel_2_role},
        )
    else:
        if run_id is None:
            raise PlotDataError("SQLite plotting requires --run-id.")
        if channel_1_role is not None or channel_2_role is not None:
            raise PlotDataError("ETO channel roles are not valid for a SQLite source.")
        dataset = load_sqlite_run(resolved, run_id)

    calibration = (
        load_gate_calibration(gate_calibration)
        if gate_calibration is not None
        else None
    )
    manifest = generate_publication_plots(
        dataset,
        output_dir,
        calibration=calibration,
        formats=tuple(formats) if formats else ("png", "pdf"),
    )
    generated = sum(
        entry["status"] == "generated" for entry in manifest["figures"]
    )
    skipped = sum(entry["status"] == "skipped" for entry in manifest["figures"])
    print(
        json.dumps(
            {
                "generated_figures": generated,
                "manifest": manifest["manifest"],
                "output_dir": str(output_dir.resolve()),
                "record_count": manifest["record_count"],
                "skipped_figures": skipped,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _follow_eto_command(
    data_file: Path,
    database: Path,
    *,
    sample_name: str,
    channel_1_role: str,
    channel_2_role: str,
    resume_run_id: str | None,
    poll_s: float,
    stop_after_idle_s: float | None,
    once: bool,
    final: bool,
) -> int:
    if poll_s <= 0:
        raise EtoDataError("--poll-s must be greater than zero.")
    if stop_after_idle_s is not None and stop_after_idle_s <= 0:
        raise EtoDataError("--stop-after-idle-s must be greater than zero.")
    if final and not once:
        raise EtoDataError("--final may be used only with --once.")

    source = data_file.resolve()
    roles = {1: channel_1_role, 2: channel_2_role}
    follow_config = json.dumps(
        {
            "channel_roles": roles,
            "sample_name": sample_name,
            "source_path": str(source),
        },
        sort_keys=True,
    )
    with RunStore(database) as store:
        run_id = store.start_run(
            protocol="eto_file_follow",
            sample_name=sample_name,
            config_json=follow_config,
            station_snapshot_json=json.dumps(
                {"mode": "incremental-file-follow", "source_path": str(source)},
                sort_keys=True,
            ),
            resume_run_id=resume_run_id,
        )
        total_new_records = 0
        total_new_readings = 0
        last_activity = time.monotonic()
        try:
            while True:
                batch = ingest_eto_increment(
                    store,
                    run_id,
                    source,
                    roles,
                    final=final,
                )
                total_new_records += batch.new_records
                total_new_readings += batch.new_transport_readings
                if batch.new_records:
                    last_activity = time.monotonic()
                    store.record_event(
                        run_id,
                        "INFO",
                        "eto_follow_batch",
                        {
                            "new_records": batch.new_records,
                            "new_transport_readings": batch.new_transport_readings,
                            "offset_bytes": batch.checkpoint.offset_bytes,
                            "total_records": batch.total_records,
                        },
                    )
                if once:
                    break
                if (
                    stop_after_idle_s is not None
                    and time.monotonic() - last_activity >= stop_after_idle_s
                ):
                    break
                time.sleep(poll_s)
        except KeyboardInterrupt:
            store.finish_run(run_id, "aborted")
            raise
        except Exception:
            store.finish_run(run_id, "failed")
            raise
        else:
            store.finish_run(run_id, "completed")

    print(
        json.dumps(
            {
                "database": ":memory:" if str(database) == ":memory:" else str(database.resolve()),
                "new_records": total_new_records,
                "new_transport_readings": total_new_readings,
                "run_id": run_id,
                "source_path": str(source),
                "status": "completed",
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            config = load_config(args.config)
            mode = "simulation" if config.runtime.simulation else "hardware"
            print(f"Valid {mode} configuration: {args.config.resolve()}")
            print(f"Database: {config.data.database_path}")
            return 0
        if args.command == "simulate":
            return _simulate(args.config, args.resume_run)
        if args.command == "simulate-frequency":
            return _simulate(args.config, args.resume_run, sweep_kind="frequency")
        if args.command == "simulate-field":
            return _simulate(args.config, args.resume_run, sweep_kind="field")
        if args.command == "simulate-temperature-field":
            return _simulate(
                args.config,
                args.resume_run,
                sweep_kind="temperature_field",
            )
        if args.command == "simulate-gate":
            return _simulate(args.config, args.resume_run, sweep_kind="gate")
        if args.command == "diagnose-hardware":
            return _diagnose_hardware_command(args.config)
        if args.command == "run-hardware":
            return _run_hardware_command(
                args.config,
                diagnostic_run_id=args.diagnostic_run_id,
                confirmation=args.confirm,
                resume_run_id=args.resume_run,
            )
        if args.command == "run-hardware-frequency":
            return _run_hardware_command(
                args.config,
                diagnostic_run_id=args.diagnostic_run_id,
                confirmation=args.confirm,
                resume_run_id=args.resume_run,
                sweep_kind="frequency",
            )
        if args.command == "run-hardware-field":
            return _run_hardware_command(
                args.config,
                diagnostic_run_id=args.diagnostic_run_id,
                confirmation=args.confirm,
                resume_run_id=args.resume_run,
                sweep_kind="field",
            )
        if args.command == "run-hardware-temperature-field":
            return _run_hardware_command(
                args.config,
                diagnostic_run_id=args.diagnostic_run_id,
                confirmation=args.confirm,
                resume_run_id=args.resume_run,
                sweep_kind="temperature_field",
            )
        if args.command == "run-hardware-gate":
            return _run_hardware_command(
                args.config,
                diagnostic_run_id=args.diagnostic_run_id,
                confirmation=args.confirm,
                resume_run_id=args.resume_run,
                sweep_kind="gate",
            )
        if args.command == "export":
            with RunStore(args.database) as store:
                output = store.export_accepted_csv(args.run_id, args.destination)
            print(output)
            return 0
        if args.command == "export-samples":
            with RunStore(args.database) as store:
                output = store.export_instrument_samples_csv(args.run_id, args.destination)
            print(output)
            return 0
        if args.command == "export-transport":
            with RunStore(args.database) as store:
                output = store.export_transport_readings_csv(
                    args.run_id,
                    args.destination,
                )
            print(output)
            return 0
        if args.command == "export-transport-summary":
            with RunStore(args.database) as store:
                output = store.export_transport_summary_csv(
                    args.run_id,
                    args.destination,
                )
            print(output)
            return 0
        if args.command == "plot-data":
            return _plot_data_command(
                args.source,
                args.output_dir,
                run_id=args.run_id,
                channel_1_role=args.channel_1_role,
                channel_2_role=args.channel_2_role,
                gate_calibration=args.gate_calibration,
                formats=args.formats,
            )
        if args.command == "inspect-eto-data":
            parsed = load_eto_data(args.data_file)
            print(json.dumps(parsed.summary(), indent=2, sort_keys=True))
            return 0
        if args.command == "follow-eto-data":
            return _follow_eto_command(
                args.data_file,
                args.database,
                sample_name=args.sample_name,
                channel_1_role=args.channel_1_role,
                channel_2_role=args.channel_2_role,
                resume_run_id=args.resume_run,
                poll_s=args.poll_s,
                stop_after_idle_s=args.stop_after_idle_s,
                once=args.once,
                final=args.final,
            )
        if args.command == "inspect-multivu-ole":
            print(json.dumps(inspect_active_multivu_ole(args.progid), indent=2, sort_keys=True))
            return 0
    except (
        AuthorizationError,
        ConfigError,
        DiagnosticError,
        EtoDataError,
        HardwareRunError,
        OleInspectionError,
        PlotDataError,
        StoreError,
    ) as exc:
        print(f"Operation refused: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Operation aborted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Operation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2
