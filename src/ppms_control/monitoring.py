from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Mapping


class MonitorError(RuntimeError):
    """Raised when a run cannot be selected or monitored safely."""


@dataclass(frozen=True)
class MonitorSnapshot:
    run: Mapping[str, object]
    progress: Mapping[str, object]
    instrument: Mapping[str, object] | None
    transport: tuple[Mapping[str, object], ...]
    events: tuple[Mapping[str, object], ...]
    warnings: tuple[str, ...]


_SWEEP_SECTIONS = {
    "fixed_environment_voltage_sweep": "voltage_sweep",
    "authorized_hardware_voltage_sweep": "voltage_sweep",
    "fixed_environment_frequency_sweep": "frequency_sweep",
    "authorized_hardware_frequency_sweep": "frequency_sweep",
    "fixed_excitation_field_sweep": "field_sweep",
    "authorized_hardware_field_sweep": "field_sweep",
    "fixed_excitation_temperature_field_sweep": "temperature_field_sweep",
    "authorized_hardware_temperature_field_sweep": "temperature_field_sweep",
    "fixed_environment_gate_sweep": "gate_sweep",
    "authorized_hardware_gate_sweep": "gate_sweep",
}


def _planned_conditions(protocol: str, config: Mapping[str, object]) -> int | None:
    section_name = _SWEEP_SECTIONS.get(protocol)
    if section_name is None:
        return None
    section = config.get(section_name)
    if not isinstance(section, dict):
        return None
    try:
        if section_name == "temperature_field_sweep":
            return int(section["temperature_points"]) * int(section["field_points"])
        if section_name == "gate_sweep":
            top = int(section["top_gate_points"])
            if section.get("mode") == "paired":
                return top
            return top * int(section["bottom_gate_points"])
        return int(section["points"])
    except (KeyError, TypeError, ValueError):
        return None


def _seconds_since(timestamp: object, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


class RunMonitor:
    """Read committed run state without opening any instrument connection."""

    def __init__(
        self,
        database: str | Path,
        *,
        run_id: str | None = None,
        latest_running: bool = False,
    ) -> None:
        if (run_id is not None) == latest_running:
            raise MonitorError("Select exactly one of run_id or latest_running.")
        self.database = Path(database).resolve()
        if not self.database.is_file():
            raise MonitorError(f"SQLite database does not exist: {self.database}")
        try:
            self._connection = sqlite3.connect(
                f"file:{self.database.as_posix()}?mode=ro",
                uri=True,
                timeout=2.0,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA query_only = ON")
            self._connection.execute("PRAGMA busy_timeout = 2000")
            self._connection.execute("SELECT 1 FROM runs LIMIT 1")
        except sqlite3.Error as exc:
            if hasattr(self, "_connection"):
                self._connection.close()
            raise MonitorError(f"Cannot open SQLite database read-only: {exc}") from exc

        if latest_running:
            row = self._connection.execute(
                """
                SELECT run_id
                FROM runs
                WHERE status = 'running'
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                self.close()
                raise MonitorError("No running run exists in the database.")
            self.run_id = str(row["run_id"])
        else:
            assert run_id is not None
            row = self._connection.execute(
                "SELECT run_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                self.close()
                raise MonitorError(f"Unknown run_id: {run_id}")
            self.run_id = run_id

    def snapshot(self, *, now: datetime | None = None) -> MonitorSnapshot:
        try:
            self._connection.execute("BEGIN")
            return self._read_snapshot(now=now)
        except sqlite3.Error as exc:
            raise MonitorError(f"Cannot read a consistent monitor snapshot: {exc}") from exc
        finally:
            if self._connection.in_transaction:
                self._connection.rollback()

    def _read_snapshot(self, *, now: datetime | None = None) -> MonitorSnapshot:
        current_time = now or datetime.now(timezone.utc)
        run_row = self._connection.execute(
            """
            SELECT run_id, protocol, sample_name, status, started_at, ended_at,
                   config_json
            FROM runs
            WHERE run_id = ?
            """,
            (self.run_id,),
        ).fetchone()
        if run_row is None:
            raise MonitorError(f"Run disappeared from the database: {self.run_id}")
        run = dict(run_row)
        try:
            config = json.loads(str(run.pop("config_json")))
        except (TypeError, json.JSONDecodeError):
            config = {}
        if not isinstance(config, dict):
            config = {}

        counts = self._connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM attempts
                 WHERE run_id = ? AND accepted = 1) AS accepted,
                (SELECT COUNT(*) FROM attempts
                 WHERE run_id = ?) AS attempts,
                (SELECT COUNT(*) FROM attempts
                 WHERE run_id = ? AND accepted = 0) AS rejected,
                (SELECT COUNT(*) FROM instrument_samples
                 WHERE run_id = ?) AS samples,
                (SELECT COUNT(*) FROM transport_readings
                 WHERE run_id = ?) AS transport
            """,
            (self.run_id, self.run_id, self.run_id, self.run_id, self.run_id),
        ).fetchone()
        latest_attempt_row = self._connection.execute(
            """
            SELECT sequence_index, attempt_index, accepted, flags_json, error, created_at
            FROM attempts
            WHERE run_id = ?
            ORDER BY attempt_id DESC
            LIMIT 1
            """,
            (self.run_id,),
        ).fetchone()
        instrument_row = self._connection.execute(
            """
            SELECT *
            FROM instrument_samples
            WHERE run_id = ?
            ORDER BY sample_id DESC
            LIMIT 1
            """,
            (self.run_id,),
        ).fetchone()
        instrument = dict(instrument_row) if instrument_row is not None else None
        transport_rows = self._connection.execute(
            """
            WITH latest_readings AS (
                SELECT MAX(reading_id) AS reading_id
                FROM transport_readings
                WHERE run_id = ?
                GROUP BY signal, instrument_channel, harmonic
            )
            SELECT reading_id, sequence_index, backend, signal,
                   instrument_channel, harmonic, timestamp_s, temperature_k,
                   field_t, gate_top_voltage_v, gate_bottom_voltage_v,
                   sample_position_deg, drive_current_a, frequency_hz,
                   x_v, y_v, amplitude_v, phase_deg, ratio_db,
                   status_code, quality_flags_json, comment, created_at
            FROM transport_readings
            WHERE reading_id IN (SELECT reading_id FROM latest_readings)
            ORDER BY signal, instrument_channel, harmonic
            """,
            (self.run_id,),
        ).fetchall()
        transport = tuple(dict(row) for row in transport_rows)
        event_rows = self._connection.execute(
            """
            SELECT created_at, level, kind, details_json
            FROM events
            WHERE run_id = ?
            ORDER BY event_id DESC
            LIMIT 5
            """,
            (self.run_id,),
        ).fetchall()
        events = tuple(dict(row) for row in event_rows)
        checkpoint_row = self._connection.execute(
            """
            SELECT source_path, offset_bytes, line_number, records_read, updated_at
            FROM eto_follow_checkpoints
            WHERE run_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (self.run_id,),
        ).fetchone()
        checkpoint = dict(checkpoint_row) if checkpoint_row is not None else None

        sequence_index: int | None = None
        last_data_at: object = None
        if instrument is not None:
            sequence_index = int(instrument["sequence_index"])
            last_data_at = instrument["created_at"]
        if transport:
            latest_transport = max(
                transport,
                key=lambda row: int(row["reading_id"]),
            )
            sequence = latest_transport.get("sequence_index")
            if sequence_index is None:
                sequence_index = int(sequence) if sequence is not None else None
            transport_created_at = latest_transport.get("created_at")
            instrument_age = _seconds_since(last_data_at, current_time)
            transport_age = _seconds_since(transport_created_at, current_time)
            if instrument_age is None or (
                transport_age is not None and transport_age < instrument_age
            ):
                last_data_at = transport_created_at

        planned = _planned_conditions(str(run["protocol"]), config)
        accepted = int(counts["accepted"])
        progress = {
            "accepted_conditions": accepted,
            "attempt_count": int(counts["attempts"]),
            "rejected_attempt_count": int(counts["rejected"]),
            "instrument_sample_count": int(counts["samples"]),
            "transport_reading_count": int(counts["transport"]),
            "planned_conditions": planned,
            "percent_complete": (
                min(100.0, accepted / planned * 100.0) if planned else None
            ),
            "current_sequence_index": sequence_index,
            "last_data_at": last_data_at,
            "last_data_age_s": _seconds_since(last_data_at, current_time),
            "checkpoint_age_s": (
                _seconds_since(checkpoint["updated_at"], current_time)
                if checkpoint is not None
                else None
            ),
            "latest_attempt": (
                dict(latest_attempt_row) if latest_attempt_row is not None else None
            ),
            "eto_checkpoint": checkpoint,
        }
        warnings = self._warnings(run, config, instrument, transport)
        if latest_attempt_row is not None:
            try:
                attempt_flags = json.loads(str(latest_attempt_row["flags_json"]))
            except json.JSONDecodeError:
                attempt_flags = ["invalid_attempt_flags_json"]
            if attempt_flags:
                warnings.append(
                    "Latest attempt flags: "
                    + ", ".join(str(flag) for flag in attempt_flags)
                )
            if latest_attempt_row["error"]:
                warnings.append(f"Latest attempt error: {latest_attempt_row['error']}")
        return MonitorSnapshot(
            run=run,
            progress=progress,
            instrument=instrument,
            transport=transport,
            events=events,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _warnings(
        run: Mapping[str, object],
        config: Mapping[str, object],
        instrument: Mapping[str, object] | None,
        transport: tuple[Mapping[str, object], ...],
    ) -> list[str]:
        warnings: list[str] = []
        status = str(run["status"])
        if status in {"failed", "aborted"}:
            warnings.append(f"Run status is {status}.")
        protocol = str(run["protocol"])
        if instrument is None and not protocol.startswith("eto_"):
            warnings.append("No raw instrument sample has been committed yet.")
        if instrument is not None:
            for prefix, label in (("sr830", "SR830"), ("sr865a", "SR865A")):
                if not bool(instrument[f"{prefix}_reference_locked"]):
                    warnings.append(f"{label} reference is unlocked.")
                if bool(instrument[f"{prefix}_overload"]):
                    warnings.append(f"{label} reports overload.")
            if not bool(instrument["ppms_stable"]):
                warnings.append("PPMS state is not stable at the latest sample.")

            acquisition = config.get("acquisition")
            if isinstance(acquisition, dict):
                checks = (
                    (
                        "Source voltage",
                        "source_voltage_set_v",
                        "source_voltage_read_v",
                        "source_voltage_tolerance_v",
                    ),
                    (
                        "Reference frequency",
                        "source_frequency_set_hz",
                        "source_frequency_read_hz",
                        "reference_frequency_tolerance_hz",
                    ),
                    (
                        "PPMS temperature",
                        "ppms_temperature_set_k",
                        "ppms_temperature_read_k",
                        "temperature_tolerance_k",
                    ),
                    (
                        "PPMS field",
                        "ppms_field_set_t",
                        "ppms_field_read_t",
                        "field_tolerance_t",
                    ),
                )
                for label, set_key, read_key, tolerance_key in checks:
                    setpoint = _number(instrument[set_key])
                    readback = _number(instrument[read_key])
                    tolerance = _number(acquisition.get(tolerance_key))
                    if (
                        setpoint is not None
                        and readback is not None
                        and tolerance is not None
                        and abs(setpoint - readback) > tolerance
                    ):
                        warnings.append(
                            f"{label} readback differs from setpoint by "
                            f"{abs(setpoint - readback):.3g}, above tolerance "
                            f"{tolerance:.3g}."
                        )

            safety = config.get("safety")
            leakage_limit = (
                _number(safety.get("gate_leakage_limit_a"))
                if isinstance(safety, dict)
                else None
            )
            compliance_limit = (
                _number(safety.get("gate_compliance_limit_a"))
                if isinstance(safety, dict)
                else None
            )
            for prefix, label in (
                ("gate_top", "Top gate"),
                ("gate_bottom", "Bottom gate"),
            ):
                enabled = bool(instrument[f"{prefix}_output_enabled"])
                available = bool(instrument[f"{prefix}_current_available"])
                current = _number(instrument[f"{prefix}_measured_current_a"])
                compliance = _number(instrument[f"{prefix}_compliance_a"])
                if enabled and not available:
                    warnings.append(f"{label} current is unavailable while output is on.")
                if (
                    compliance is not None
                    and compliance_limit is not None
                    and compliance > compliance_limit
                ):
                    warnings.append(
                        f"{label} compliance {compliance:.3g} A exceeds the "
                        f"configured limit {compliance_limit:.3g} A."
                    )
                if (
                    available
                    and current is not None
                    and leakage_limit is not None
                    and abs(current) >= leakage_limit
                ):
                    warnings.append(
                        f"{label} leakage {current:.3g} A reached the software "
                        f"limit {leakage_limit:.3g} A."
                    )

        quality_flags: set[str] = set()
        for row in transport:
            try:
                flags = json.loads(str(row.get("quality_flags_json", "[]")))
            except json.JSONDecodeError:
                flags = ["invalid_quality_flags_json"]
            if isinstance(flags, list):
                quality_flags.update(str(flag) for flag in flags)
            comment = str(row.get("comment", ""))
            if "compliance" in comment.lower():
                quality_flags.add("compliance_comment")
        if quality_flags:
            warnings.append("Latest transport flags: " + ", ".join(sorted(quality_flags)))
        return warnings

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "RunMonitor":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _format_number(value: object, unit: str = "") -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:.6g}{unit}"


def _format_gate(instrument: Mapping[str, object], prefix: str) -> str:
    enabled = "ON" if bool(instrument[f"{prefix}_output_enabled"]) else "OFF"
    current = (
        _format_number(instrument[f"{prefix}_measured_current_a"], " A")
        if bool(instrument[f"{prefix}_current_available"])
        else "unavailable"
    )
    return (
        f"V={_format_number(instrument[f'{prefix}_voltage_v'], ' V')}, "
        f"output={enabled}, I={current}, "
        f"compliance={_format_number(instrument[f'{prefix}_compliance_a'], ' A')}"
    )


def render_monitor_snapshot(snapshot: MonitorSnapshot) -> str:
    run = snapshot.run
    progress = snapshot.progress
    lines = [
        "PPMS RUN MONITOR — READ-ONLY SQLITE",
        f"Run: {run['run_id']}",
        f"Sample: {run['sample_name']}  Protocol: {run['protocol']}",
        f"Status: {run['status']}  Started: {run['started_at']}",
    ]
    planned = progress["planned_conditions"]
    if planned is None:
        progress_text = f"accepted={progress['accepted_conditions']}"
    else:
        progress_text = (
            f"{progress['accepted_conditions']}/{planned} "
            f"({float(progress['percent_complete']):.1f}%)"
        )
    lines.extend(
        (
            "",
            "PROGRESS",
            f"  Conditions: {progress_text}  Attempts: {progress['attempt_count']}  "
            f"Rejected: {progress['rejected_attempt_count']}",
            f"  Raw samples: {progress['instrument_sample_count']}  "
            f"Transport rows: {progress['transport_reading_count']}",
            f"  Sequence index: {progress['current_sequence_index']}  "
            f"Last data age: {_format_number(progress['last_data_age_s'], ' s')}",
        )
    )
    if run["status"] != "running":
        lines.append(
            "  NOTE: Terminal run; values below are the last acquired sample, "
            "not a post-run live hardware query."
        )
    checkpoint = progress["eto_checkpoint"]
    if isinstance(checkpoint, dict):
        lines.append(
            f"  ETO checkpoint: records={checkpoint['records_read']}  "
            f"line={checkpoint['line_number']}  "
            f"heartbeat age={_format_number(progress['checkpoint_age_s'], ' s')}  "
            f"source={checkpoint['source_path']}"
        )
    latest_attempt = progress["latest_attempt"]
    if isinstance(latest_attempt, dict):
        lines.append(
            f"  Latest attempt: seq={latest_attempt['sequence_index']}  "
            f"attempt={latest_attempt['attempt_index']}  "
            f"accepted={'YES' if latest_attempt['accepted'] else 'NO'}"
        )

    instrument = snapshot.instrument
    if instrument is None:
        if str(run["protocol"]).startswith("eto_"):
            lines.extend(
                (
                    "",
                    "TRANSPORT-ONLY / ETO",
                    "  This backend does not record separate SR/SMU/PPMS raw samples.",
                )
            )
        else:
            lines.extend(("", "LATEST INSTRUMENT SAMPLE", "  Waiting for a raw sample."))
    else:
        lines.extend(
            (
                "",
                "LATEST COMMITTED CONDITION / SOURCE",
                f"  condition={str(instrument['condition_id'])[:12]}  "
                f"attempt={instrument['attempt_index']}  sample={instrument['sample_index']}",
                f"  Vrms set/read={_format_number(instrument['source_voltage_set_v'], ' V')} / "
                f"{_format_number(instrument['source_voltage_read_v'], ' V')}  "
                f"I_est={_format_number(instrument['estimated_current_a'], ' A')}",
                f"  frequency set/read={_format_number(instrument['source_frequency_set_hz'], ' Hz')} / "
                f"{_format_number(instrument['source_frequency_read_hz'], ' Hz')}",
                "",
                "LOCK-INS",
                f"  SR830 h{instrument['sr830_harmonic']}: "
                f"X={_format_number(instrument['sr830_x_v'], ' V')}  "
                f"Y={_format_number(instrument['sr830_y_v'], ' V')}  "
                f"R={_format_number(math.hypot(float(instrument['sr830_x_v']), float(instrument['sr830_y_v'])), ' V')}  "
                f"f={_format_number(instrument['sr830_frequency_hz'], ' Hz')}  "
                f"lock={'YES' if instrument['sr830_reference_locked'] else 'NO'}  "
                f"overload={'YES' if instrument['sr830_overload'] else 'NO'}",
                f"  SR865A h{instrument['sr865a_harmonic']}: "
                f"X={_format_number(instrument['sr865a_x_v'], ' V')}  "
                f"Y={_format_number(instrument['sr865a_y_v'], ' V')}  "
                f"R={_format_number(math.hypot(float(instrument['sr865a_x_v']), float(instrument['sr865a_y_v'])), ' V')}  "
                f"f={_format_number(instrument['sr865a_frequency_hz'], ' Hz')}  "
                f"lock={'YES' if instrument['sr865a_reference_locked'] else 'NO'}  "
                f"overload={'YES' if instrument['sr865a_overload'] else 'NO'}",
                "",
                "GATES",
                "  Top:    " + _format_gate(instrument, "gate_top"),
                "  Bottom: " + _format_gate(instrument, "gate_bottom"),
                "",
                "PPMS",
                f"  T set/read={_format_number(instrument['ppms_temperature_set_k'], ' K')} / "
                f"{_format_number(instrument['ppms_temperature_read_k'], ' K')}  "
                f"status={instrument['ppms_temperature_status']}",
                f"  B set/read={_format_number(instrument['ppms_field_set_t'], ' T')} / "
                f"{_format_number(instrument['ppms_field_read_t'], ' T')}  "
                f"status={instrument['ppms_field_status']}",
                f"  stable={'YES' if instrument['ppms_stable'] else 'NO'}  "
                f"chamber={instrument['ppms_chamber_status']}  "
                f"angle={_format_number(instrument['ppms_sample_position_deg'], ' deg')}  "
                f"position_status={instrument['ppms_position_status'] or 'n/a'}",
            )
        )

    lines.extend(("", "LATEST TRANSPORT BY SIGNAL / HARMONIC"))
    if not snapshot.transport:
        lines.append("  Waiting for transport readings.")
    for row in snapshot.transport:
        voltage = row["x_v"] if row["x_v"] is not None else row["amplitude_v"]
        voltage_label = "X" if row["x_v"] is not None else "|V|"
        response = None
        response_label = None
        current = _number(row["drive_current_a"])
        if row["harmonic"] == 1 and current not in (None, 0.0):
            numeric_voltage = _number(voltage)
            if numeric_voltage is not None:
                response = numeric_voltage / current
                response_label = "R_X" if row["x_v"] is not None else "|Z|"
        response_text = (
            f"  {response_label}={_format_number(response, ' ohm')}"
            if response is not None
            else ""
        )
        metadata = []
        if row["ratio_db"] is not None:
            metadata.append(f"ratio={_format_number(row['ratio_db'], ' dB')}")
        if row["status_code"] is not None:
            metadata.append(f"status_code={row['status_code']}")
        metadata_text = f"  {'  '.join(metadata)}" if metadata else ""
        lines.append(
            f"  {row['signal']} ch={row['instrument_channel']} "
            f"h{row['harmonic']} seq={row['sequence_index']}: "
            f"{voltage_label}={_format_number(voltage, ' V')}  "
            f"phase={_format_number(row['phase_deg'], ' deg')}"
            f"{response_text}"
            f"{metadata_text}"
        )
        if str(run["protocol"]).startswith("eto_"):
            lines.append(
                f"    T={_format_number(row['temperature_k'], ' K')}  "
                f"B={_format_number(row['field_t'], ' T')}  "
                f"angle={_format_number(row['sample_position_deg'], ' deg')}  "
                f"I={_format_number(row['drive_current_a'], ' A')}  "
                f"f={_format_number(row['frequency_hz'], ' Hz')}"
            )

    lines.extend(("", "WARNINGS"))
    if snapshot.warnings:
        lines.extend(f"  [!!] {warning}" for warning in snapshot.warnings)
    else:
        lines.append("  None in the latest committed data.")

    lines.extend(("", "RECENT EVENTS"))
    if not snapshot.events:
        lines.append("  None.")
    for event in snapshot.events:
        details = str(event["details_json"])
        if len(details) > 120:
            details = details[:117] + "..."
        lines.append(
            f"  {event['created_at']} {event['level']} {event['kind']} {details}"
        )
    lines.extend(
        (
            "",
            "Ctrl+C in this monitor window stops monitoring only; it does not stop the run.",
        )
    )
    return "\n".join(lines)
