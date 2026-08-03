from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from ppms_control.acquisition import MeasurementEngine
from ppms_control.config import ConfigError, load_config
from ppms_control.instruments import build_simulated_bundle
from ppms_control.protocols import run_current_sweep
from ppms_control.safety import SafeStation
from ppms_control.store import RunStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ppms-control")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="Validate a TOML configuration")
    validate.add_argument("config", type=Path)

    simulate = subparsers.add_parser("simulate", help="Run the current-sweep simulation")
    simulate.add_argument("config", type=Path)
    simulate.add_argument("--resume-run", default=None)

    export = subparsers.add_parser("export", help="Export accepted attempts to CSV")
    export.add_argument("database", type=Path)
    export.add_argument("run_id")
    export.add_argument("destination", type=Path)
    return parser


def _simulate(config_path: Path, resume_run_id: str | None) -> int:
    config = load_config(config_path)
    bundle = build_simulated_bundle(config)
    safe_station = SafeStation(bundle, config)
    store = RunStore(config.data.database_path)
    run_id: str | None = None
    terminal_status = "failed"
    exit_code = 1
    output: dict[str, object] | None = None
    try:
        snapshot_json = json.dumps(safe_station.qcodes_snapshot, default=str, sort_keys=True)
        run_id = store.start_run(
            protocol="fixed_environment_current_sweep",
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
            config.instruments,
        )
        measured = run_current_sweep(engine, store, run_id, config.current_sweep)
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            config = load_config(args.config)
            print(f"Valid simulation configuration: {args.config.resolve()}")
            print(f"Database: {config.data.database_path}")
            return 0
        if args.command == "simulate":
            return _simulate(args.config, args.resume_run)
        if args.command == "export":
            with RunStore(args.database) as store:
                output = store.export_accepted_csv(args.run_id, args.destination)
            print(output)
            return 0
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    return 2
