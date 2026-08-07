from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping
from uuid import uuid4

from ppms_control.analysis import summarize_transport_rows
from ppms_control.models import AttemptResult, InstrumentSample, TransportReading


class StoreError(RuntimeError):
    """Raised when run provenance cannot be created or resumed safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            self.path = Path(":memory:")
            connection_target: str | Path = ":memory:"
        else:
            self.path = Path(path).resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection_target = self.path
        self._connection = sqlite3.connect(connection_target)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                protocol TEXT NOT NULL,
                sample_name TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                station_snapshot_json TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                kind TEXT NOT NULL,
                details_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                condition_id TEXT NOT NULL,
                sequence_index INTEGER NOT NULL,
                attempt_index INTEGER NOT NULL,
                current_a REAL NOT NULL,
                source_voltage_v REAL,
                estimated_current_a REAL,
                source_frequency_hz REAL,
                gate_top_voltage_v REAL,
                gate_bottom_voltage_v REAL,
                temperature_k REAL NOT NULL,
                field_t REAL NOT NULL,
                xx_x_v REAL,
                xx_y_v REAL,
                xx_x_std_v REAL,
                xx_y_std_v REAL,
                xx_frequency_hz REAL,
                xy_x_v REAL,
                xy_y_v REAL,
                xy_x_std_v REAL,
                xy_y_std_v REAL,
                xy_frequency_hz REAL,
                accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
                flags_json TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (run_id, condition_id, attempt_index)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_attempt_per_condition
            ON attempts(run_id, condition_id)
            WHERE accepted = 1;

            CREATE TABLE IF NOT EXISTS instrument_samples (
                sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                condition_id TEXT NOT NULL,
                sequence_index INTEGER NOT NULL,
                attempt_index INTEGER NOT NULL,
                sample_index INTEGER NOT NULL,
                source_voltage_set_v REAL NOT NULL,
                source_voltage_read_v REAL NOT NULL,
                estimated_current_a REAL NOT NULL,
                source_frequency_set_hz REAL NOT NULL,
                source_frequency_read_hz REAL NOT NULL,
                sr830_x_v REAL NOT NULL,
                sr830_y_v REAL NOT NULL,
                sr830_frequency_hz REAL NOT NULL,
                sr830_harmonic INTEGER NOT NULL,
                sr830_reference_locked INTEGER NOT NULL,
                sr830_overload INTEGER NOT NULL,
                sr865a_x_v REAL NOT NULL,
                sr865a_y_v REAL NOT NULL,
                sr865a_frequency_hz REAL NOT NULL,
                sr865a_harmonic INTEGER NOT NULL,
                sr865a_reference_locked INTEGER NOT NULL,
                sr865a_overload INTEGER NOT NULL,
                gate_top_voltage_v REAL NOT NULL,
                gate_top_output_enabled INTEGER NOT NULL,
                gate_top_compliance_a REAL NOT NULL,
                gate_top_measured_current_a REAL NOT NULL,
                gate_top_current_available INTEGER NOT NULL,
                gate_bottom_voltage_v REAL NOT NULL,
                gate_bottom_output_enabled INTEGER NOT NULL,
                gate_bottom_compliance_a REAL NOT NULL,
                gate_bottom_measured_current_a REAL NOT NULL,
                gate_bottom_current_available INTEGER NOT NULL,
                ppms_temperature_set_k REAL NOT NULL,
                ppms_temperature_read_k REAL NOT NULL,
                ppms_temperature_status TEXT NOT NULL,
                ppms_field_set_t REAL NOT NULL,
                ppms_field_read_t REAL NOT NULL,
                ppms_field_status TEXT NOT NULL,
                ppms_chamber_status TEXT NOT NULL,
                ppms_sample_position_deg REAL,
                ppms_position_status TEXT,
                ppms_stable INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (run_id, condition_id, attempt_index, sample_index)
            );

            CREATE TABLE IF NOT EXISTS transport_readings (
                reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                sequence_index INTEGER,
                backend TEXT NOT NULL,
                signal TEXT NOT NULL CHECK (signal IN ('xx', 'xy')),
                instrument_channel TEXT NOT NULL,
                harmonic INTEGER NOT NULL CHECK (harmonic IN (1, 2, 3)),
                timestamp_s REAL NOT NULL,
                temperature_k REAL NOT NULL,
                field_t REAL NOT NULL,
                gate_top_voltage_v REAL,
                gate_bottom_voltage_v REAL,
                sample_position_deg REAL,
                drive_current_a REAL,
                frequency_hz REAL,
                x_v REAL,
                y_v REAL,
                amplitude_v REAL,
                phase_deg REAL,
                ratio_db REAL,
                phase_resolved INTEGER NOT NULL CHECK (phase_resolved IN (0, 1)),
                source_row INTEGER,
                comment TEXT NOT NULL,
                status_code INTEGER,
                quality_flags_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS transport_readings_by_run
            ON transport_readings(run_id, sequence_index, signal, harmonic);

            CREATE TABLE IF NOT EXISTS eto_follow_checkpoints (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                source_path TEXT NOT NULL,
                offset_bytes INTEGER NOT NULL,
                line_number INTEGER NOT NULL,
                records_read INTEGER NOT NULL,
                anchor_sha256 TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, source_path)
            );
            """
        )
        attempt_columns = {
            str(row[1]) for row in self._connection.execute("PRAGMA table_info(attempts)")
        }
        if "source_voltage_v" not in attempt_columns:
            self._connection.execute("ALTER TABLE attempts ADD COLUMN source_voltage_v REAL")
        if "estimated_current_a" not in attempt_columns:
            self._connection.execute("ALTER TABLE attempts ADD COLUMN estimated_current_a REAL")
        if "source_frequency_hz" not in attempt_columns:
            self._connection.execute("ALTER TABLE attempts ADD COLUMN source_frequency_hz REAL")
        if "gate_top_voltage_v" not in attempt_columns:
            self._connection.execute("ALTER TABLE attempts ADD COLUMN gate_top_voltage_v REAL")
        if "gate_bottom_voltage_v" not in attempt_columns:
            self._connection.execute("ALTER TABLE attempts ADD COLUMN gate_bottom_voltage_v REAL")
        sample_columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(instrument_samples)")
        }
        if "gate_top_current_available" not in sample_columns:
            self._connection.execute(
                "ALTER TABLE instrument_samples "
                "ADD COLUMN gate_top_current_available INTEGER NOT NULL DEFAULT 1"
            )
        if "gate_bottom_current_available" not in sample_columns:
            self._connection.execute(
                "ALTER TABLE instrument_samples "
                "ADD COLUMN gate_bottom_current_available INTEGER NOT NULL DEFAULT 1"
            )
        if "source_frequency_set_hz" not in sample_columns:
            self._connection.execute(
                "ALTER TABLE instrument_samples "
                "ADD COLUMN source_frequency_set_hz REAL NOT NULL DEFAULT 0"
            )
        if "source_frequency_read_hz" not in sample_columns:
            self._connection.execute(
                "ALTER TABLE instrument_samples "
                "ADD COLUMN source_frequency_read_hz REAL NOT NULL DEFAULT 0"
            )
        if "ppms_sample_position_deg" not in sample_columns:
            self._connection.execute(
                "ALTER TABLE instrument_samples ADD COLUMN ppms_sample_position_deg REAL"
            )
        if "ppms_position_status" not in sample_columns:
            self._connection.execute(
                "ALTER TABLE instrument_samples ADD COLUMN ppms_position_status TEXT"
            )
        transport_columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(transport_readings)")
        }
        if "gate_top_voltage_v" not in transport_columns:
            self._connection.execute(
                "ALTER TABLE transport_readings ADD COLUMN gate_top_voltage_v REAL"
            )
        if "gate_bottom_voltage_v" not in transport_columns:
            self._connection.execute(
                "ALTER TABLE transport_readings ADD COLUMN gate_bottom_voltage_v REAL"
            )
        self._connection.commit()

    def start_run(
        self,
        *,
        protocol: str,
        sample_name: str,
        config_json: str,
        station_snapshot_json: str,
        resume_run_id: str | None = None,
    ) -> str:
        config_hash = sha256(config_json.encode("utf-8")).hexdigest()
        if resume_run_id is not None:
            row = self._connection.execute(
                "SELECT protocol, config_hash, status FROM runs WHERE run_id = ?",
                (resume_run_id,),
            ).fetchone()
            if row is None:
                raise StoreError(f"Run {resume_run_id} does not exist.")
            if row["protocol"] != protocol or row["config_hash"] != config_hash:
                raise StoreError("Resume refused because protocol or configuration changed.")
            if row["status"] == "completed":
                raise StoreError("Resume refused because the run is already completed.")
            self._connection.execute(
                "UPDATE runs SET status = 'running', ended_at = NULL, station_snapshot_json = ? WHERE run_id = ?",
                (station_snapshot_json, resume_run_id),
            )
            self._connection.commit()
            self.record_event(resume_run_id, "INFO", "run_resumed", {})
            return resume_run_id

        run_id = str(uuid4())
        self._connection.execute(
            """
            INSERT INTO runs(
                run_id, protocol, sample_name, config_hash, config_json,
                station_snapshot_json, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                run_id,
                protocol,
                sample_name,
                config_hash,
                config_json,
                station_snapshot_json,
                _now(),
            ),
        )
        self._connection.commit()
        self.record_event(run_id, "INFO", "run_started", {})
        return run_id

    def finish_run(self, run_id: str, status: str) -> None:
        if status not in {"completed", "failed", "aborted"}:
            raise StoreError(f"Invalid terminal run status: {status}")
        self._connection.execute(
            "UPDATE runs SET status = ?, ended_at = ? WHERE run_id = ?",
            (status, _now(), run_id),
        )
        self._connection.commit()
        self.record_event(run_id, "INFO", "run_finished", {"status": status})

    def update_station_snapshot(self, run_id: str, station_snapshot_json: str) -> None:
        cursor = self._connection.execute(
            "UPDATE runs SET station_snapshot_json = ? WHERE run_id = ?",
            (station_snapshot_json, run_id),
        )
        if cursor.rowcount != 1:
            raise StoreError(f"Run {run_id} does not exist.")
        self._connection.commit()

    def run_status(self, run_id: str) -> str:
        row = self._connection.execute(
            "SELECT status FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StoreError(f"Run {run_id} does not exist.")
        return str(row["status"])

    def record_event(
        self,
        run_id: str,
        level: str,
        kind: str,
        details: dict[str, object],
    ) -> None:
        self._connection.execute(
            "INSERT INTO events(run_id, created_at, level, kind, details_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, _now(), level, kind, json.dumps(details, default=str, sort_keys=True)),
        )
        self._connection.commit()

    def record_attempt(self, run_id: str, result: AttemptResult) -> None:
        reading = result.reading
        self._connection.execute(
            """
            INSERT INTO attempts(
                run_id, condition_id, sequence_index, attempt_index,
                current_a, source_voltage_v, estimated_current_a, source_frequency_hz,
                gate_top_voltage_v, gate_bottom_voltage_v, temperature_k, field_t,
                xx_x_v, xx_y_v, xx_x_std_v, xx_y_std_v, xx_frequency_hz,
                xy_x_v, xy_y_v, xy_x_std_v, xy_y_std_v, xy_frequency_hz,
                accepted, flags_json, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                result.condition.condition_id,
                result.condition.sequence_index,
                result.attempt_index,
                result.condition.estimated_current_a,
                result.condition.source_voltage_v,
                result.condition.estimated_current_a,
                result.condition.frequency_hz,
                result.condition.gate_top_voltage_v,
                result.condition.gate_bottom_voltage_v,
                result.condition.temperature_k,
                result.condition.field_t,
                reading.xx_x_v,
                reading.xx_y_v,
                reading.xx_x_std_v,
                reading.xx_y_std_v,
                reading.xx_frequency_hz,
                reading.xy_x_v,
                reading.xy_y_v,
                reading.xy_x_std_v,
                reading.xy_y_std_v,
                reading.xy_frequency_hz,
                int(result.accepted),
                json.dumps(result.flags),
                result.error,
                _now(),
            ),
        )
        self._connection.commit()

    def record_instrument_sample(self, run_id: str, sample: InstrumentSample) -> None:
        condition = sample.condition
        lockins = sample.lockins
        state = sample.state
        self._connection.execute(
            """
            INSERT INTO instrument_samples(
                run_id, condition_id, sequence_index, attempt_index, sample_index,
                source_voltage_set_v, source_voltage_read_v, estimated_current_a,
                source_frequency_set_hz, source_frequency_read_hz,
                sr830_x_v, sr830_y_v, sr830_frequency_hz, sr830_harmonic,
                sr830_reference_locked, sr830_overload,
                sr865a_x_v, sr865a_y_v, sr865a_frequency_hz, sr865a_harmonic,
                sr865a_reference_locked, sr865a_overload,
                gate_top_voltage_v, gate_top_output_enabled, gate_top_compliance_a,
                gate_top_measured_current_a, gate_top_current_available,
                gate_bottom_voltage_v, gate_bottom_output_enabled,
                gate_bottom_compliance_a, gate_bottom_measured_current_a,
                gate_bottom_current_available,
                ppms_temperature_set_k, ppms_temperature_read_k,
                ppms_temperature_status, ppms_field_set_t, ppms_field_read_t,
                ppms_field_status, ppms_chamber_status, ppms_sample_position_deg,
                ppms_position_status, ppms_stable, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                condition.condition_id,
                condition.sequence_index,
                sample.attempt_index,
                sample.sample_index,
                condition.source_voltage_v,
                state.source_voltage_v,
                condition.estimated_current_a,
                condition.frequency_hz,
                state.source_frequency_hz,
                lockins.xx.x_v,
                lockins.xx.y_v,
                lockins.xx.frequency_hz,
                lockins.xx.harmonic,
                int(lockins.xx.reference_locked),
                int(lockins.xx.overload),
                lockins.xy.x_v,
                lockins.xy.y_v,
                lockins.xy.frequency_hz,
                lockins.xy.harmonic,
                int(lockins.xy.reference_locked),
                int(lockins.xy.overload),
                state.gate_top.source_voltage_v,
                int(state.gate_top.output_enabled),
                state.gate_top.compliance_a,
                state.gate_top.measured_current_a or 0.0,
                int(state.gate_top.measured_current_a is not None),
                state.gate_bottom.source_voltage_v,
                int(state.gate_bottom.output_enabled),
                state.gate_bottom.compliance_a,
                state.gate_bottom.measured_current_a or 0.0,
                int(state.gate_bottom.measured_current_a is not None),
                condition.temperature_k,
                state.ppms.temperature_k,
                state.ppms.temperature_status,
                condition.field_t,
                state.ppms.field_t,
                state.ppms.field_status,
                state.ppms.chamber_status,
                state.ppms.sample_position_deg,
                state.ppms.position_status,
                int(state.ppms.stable),
                _now(),
            ),
        )
        self._connection.commit()

    def _insert_transport_reading(self, run_id: str, reading: TransportReading) -> None:
        self._connection.execute(
            """
            INSERT INTO transport_readings(
                run_id, sequence_index, backend, signal, instrument_channel, harmonic,
                timestamp_s, temperature_k, field_t,
                gate_top_voltage_v, gate_bottom_voltage_v, sample_position_deg,
                drive_current_a, frequency_hz, x_v, y_v, amplitude_v, phase_deg,
                ratio_db, phase_resolved, source_row, comment, status_code,
                quality_flags_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                reading.sequence_index,
                reading.backend,
                reading.signal,
                reading.instrument_channel,
                reading.harmonic,
                reading.timestamp_s,
                reading.temperature_k,
                reading.field_t,
                reading.gate_top_voltage_v,
                reading.gate_bottom_voltage_v,
                reading.sample_position_deg,
                reading.drive_current_a,
                reading.frequency_hz,
                reading.x_v,
                reading.y_v,
                reading.amplitude_v,
                reading.phase_deg,
                reading.ratio_db,
                int(reading.phase_resolved),
                reading.source_row,
                reading.comment,
                reading.status_code,
                json.dumps(reading.quality_flags),
                _now(),
            ),
        )

    def record_transport_reading(self, run_id: str, reading: TransportReading) -> None:
        self._insert_transport_reading(run_id, reading)
        self._connection.commit()

    def load_eto_follow_checkpoint(
        self,
        run_id: str,
        source_path: str | Path,
    ) -> dict[str, object] | None:
        source = str(Path(source_path).resolve())
        row = self._connection.execute(
            """
            SELECT offset_bytes, line_number, records_read, anchor_sha256
            FROM eto_follow_checkpoints
            WHERE run_id = ? AND source_path = ?
            """,
            (run_id, source),
        ).fetchone()
        if row is None:
            return None
        return {
            "offset_bytes": int(row["offset_bytes"]),
            "line_number": int(row["line_number"]),
            "records_read": int(row["records_read"]),
            "anchor_sha256": str(row["anchor_sha256"]),
        }

    def record_eto_follow_batch(
        self,
        run_id: str,
        source_path: str | Path,
        readings: tuple[TransportReading, ...],
        checkpoint: Mapping[str, object],
    ) -> None:
        """Commit new ETO readings and their restart cursor atomically."""

        source = str(Path(source_path).resolve())
        try:
            for reading in readings:
                self._insert_transport_reading(run_id, reading)
            self._connection.execute(
                """
                INSERT INTO eto_follow_checkpoints(
                    run_id, source_path, offset_bytes, line_number,
                    records_read, anchor_sha256, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_path) DO UPDATE SET
                    offset_bytes = excluded.offset_bytes,
                    line_number = excluded.line_number,
                    records_read = excluded.records_read,
                    anchor_sha256 = excluded.anchor_sha256,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    source,
                    int(checkpoint["offset_bytes"]),
                    int(checkpoint["line_number"]),
                    int(checkpoint["records_read"]),
                    str(checkpoint["anchor_sha256"]),
                    _now(),
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def accepted_condition_ids(self, run_id: str) -> set[str]:
        rows = self._connection.execute(
            "SELECT condition_id FROM attempts WHERE run_id = ? AND accepted = 1",
            (run_id,),
        ).fetchall()
        return {str(row["condition_id"]) for row in rows}

    def attempt_count(self, run_id: str, *, accepted: bool | None = None) -> int:
        if accepted is None:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM attempts WHERE run_id = ?", (run_id,)
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM attempts WHERE run_id = ? AND accepted = ?",
                (run_id, int(accepted)),
            ).fetchone()
        return int(row["count"])

    def instrument_sample_count(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM instrument_samples WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["count"])

    def transport_reading_count(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM transport_readings WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["count"])

    def require_completed_diagnostic(self, run_id: str, config_json: str) -> None:
        config_hash = sha256(config_json.encode("utf-8")).hexdigest()
        row = self._connection.execute(
            """
            SELECT protocol, config_hash, status
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise StoreError(f"Diagnostic run {run_id} does not exist.")
        if row["protocol"] != "read_only_hardware_diagnostic":
            raise StoreError("The supplied run is not a hardware diagnostic.")
        if row["config_hash"] != config_hash:
            raise StoreError("The diagnostic configuration does not match the current configuration.")
        if row["status"] != "completed":
            raise StoreError("The hardware diagnostic did not complete successfully.")

    def export_accepted_csv(self, run_id: str, destination: str | Path) -> Path:
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = self._connection.execute(
            """
            SELECT condition_id, sequence_index, source_voltage_v, estimated_current_a,
                   source_frequency_hz, gate_top_voltage_v, gate_bottom_voltage_v,
                   temperature_k, field_t,
                   xx_x_v, xx_y_v, xx_x_std_v, xx_y_std_v, xx_frequency_hz,
                   xy_x_v, xy_y_v, xy_x_std_v, xy_y_std_v, xy_frequency_hz,
                   flags_json, created_at
            FROM attempts
            WHERE run_id = ? AND accepted = 1
            ORDER BY sequence_index
            """,
            (run_id,),
        ).fetchall()
        fieldnames = list(rows[0].keys()) if rows else []
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(dict(row) for row in rows)
        return output

    def export_instrument_samples_csv(self, run_id: str, destination: str | Path) -> Path:
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = self._connection.execute(
            """
            SELECT s.*, a.accepted, a.flags_json, a.error
            FROM instrument_samples AS s
            LEFT JOIN attempts AS a
              ON a.run_id = s.run_id
             AND a.condition_id = s.condition_id
             AND a.attempt_index = s.attempt_index
            WHERE s.run_id = ?
            ORDER BY s.sequence_index, s.attempt_index, s.sample_index
            """,
            (run_id,),
        ).fetchall()
        fieldnames = list(rows[0].keys()) if rows else []
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(dict(row) for row in rows)
        return output

    def export_transport_readings_csv(self, run_id: str, destination: str | Path) -> Path:
        """Export the backend-independent long table with plot-ready ratios."""

        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = self._connection.execute(
            """
            SELECT reading_id, run_id, sequence_index, backend, signal,
                   instrument_channel, harmonic, timestamp_s, temperature_k,
                   field_t, gate_top_voltage_v, gate_bottom_voltage_v,
                   sample_position_deg, drive_current_a, frequency_hz,
                   x_v, y_v, amplitude_v, phase_deg, ratio_db, phase_resolved,
                   CASE
                       WHEN drive_current_a IS NOT NULL AND drive_current_a != 0
                            AND x_v IS NOT NULL
                       THEN x_v / drive_current_a
                   END AS x_over_drive_current_ohm,
                   CASE
                       WHEN drive_current_a IS NOT NULL AND drive_current_a != 0
                            AND y_v IS NOT NULL
                       THEN y_v / drive_current_a
                   END AS y_over_drive_current_ohm,
                   CASE
                       WHEN drive_current_a IS NOT NULL AND drive_current_a != 0
                            AND amplitude_v IS NOT NULL
                       THEN amplitude_v / drive_current_a
                   END AS amplitude_over_drive_current_ohm,
                   source_row, comment, status_code, quality_flags_json,
                   created_at
            FROM transport_readings
            WHERE run_id = ?
            ORDER BY
                CASE WHEN sequence_index IS NULL THEN 1 ELSE 0 END,
                sequence_index,
                CASE WHEN source_row IS NULL THEN 1 ELSE 0 END,
                source_row,
                timestamp_s,
                signal,
                harmonic,
                reading_id
            """,
            (run_id,),
        ).fetchall()
        fieldnames = list(rows[0].keys()) if rows else []
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(dict(row) for row in rows)
        return output

    def export_transport_summary_csv(self, run_id: str, destination: str | Path) -> Path:
        """Export one averaged row per observation, signal, and harmonic."""

        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = self._connection.execute(
            """
            SELECT reading_id, sequence_index, backend, signal,
                   instrument_channel, harmonic, timestamp_s, temperature_k,
                   field_t, gate_top_voltage_v, gate_bottom_voltage_v,
                   sample_position_deg, drive_current_a, frequency_hz,
                   x_v, y_v, amplitude_v, phase_deg, ratio_db, phase_resolved,
                   source_row, comment, status_code, quality_flags_json
            FROM transport_readings
            WHERE run_id = ?
            ORDER BY reading_id
            """,
            (run_id,),
        ).fetchall()
        summaries = summarize_transport_rows(rows)
        fieldnames = list(summaries[0]) if summaries else []
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(summaries)
        return output

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
