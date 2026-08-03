from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from ppms_control.models import AttemptResult


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
            """
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
                current_a, temperature_k, field_t,
                xx_x_v, xx_y_v, xx_x_std_v, xx_y_std_v, xx_frequency_hz,
                xy_x_v, xy_y_v, xy_x_std_v, xy_y_std_v, xy_frequency_hz,
                accepted, flags_json, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                result.condition.condition_id,
                result.condition.sequence_index,
                result.attempt_index,
                result.condition.current_a,
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

    def export_accepted_csv(self, run_id: str, destination: str | Path) -> Path:
        output = Path(destination).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = self._connection.execute(
            """
            SELECT condition_id, sequence_index, current_a, temperature_k, field_t,
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

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
