from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import unittest
from uuid import uuid4

from ppms_control.cli import _monitor_run_command
from ppms_control.config import load_config
from ppms_control.models import (
    AttemptResult,
    AveragedPair,
    GateState,
    InstrumentSample,
    LockinPairReading,
    LockinReading,
    MeasurementCondition,
    PhysicalState,
    PPMSState,
    TransportReading,
)
from ppms_control.monitoring import MonitorError, RunMonitor, render_monitor_snapshot
from ppms_control.store import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / ".test-tmp"
TEST_TEMP_ROOT.mkdir(exist_ok=True)
CONFIG = load_config(PROJECT_ROOT / "config" / "simulation.toml")


def _database_path() -> Path:
    return TEST_TEMP_ROOT / f"monitor-{uuid4()}.sqlite"


def _condition(sequence_index: int = 0) -> MeasurementCondition:
    return MeasurementCondition(
        sequence_index=sequence_index,
        source_voltage_v=0.004,
        estimated_current_a=4e-8,
        frequency_hz=17.777,
        temperature_k=10.0,
        field_t=0.1,
        gate_top_voltage_v=0.2,
        gate_bottom_voltage_v=-0.2,
    )


def _instrument_sample(
    sample_index: int,
    *,
    overload: bool = False,
    top_current_a: float | None = 1e-11,
    top_compliance_a: float = 1e-8,
    stable: bool = True,
) -> InstrumentSample:
    lockins = LockinPairReading(
        requested_harmonic=1,
        xx=LockinReading(4e-6, 1e-7, 17.777, 1, True, overload),
        xy=LockinReading(2e-7, 1e-8, 17.777, 1, True, False),
    )
    state = PhysicalState(
        source_voltage_v=0.004,
        source_frequency_hz=17.777,
        gate_top=GateState(0.2, True, top_compliance_a, top_current_a),
        gate_bottom=GateState(-0.2, True, 1e-8, 2e-11),
        ppms=PPMSState(
            temperature_k=10.01,
            temperature_status="Stable",
            field_t=0.1001,
            field_status="Holding",
            chamber_status="Sealed",
            sample_position_deg=0.0,
            position_status="Holding",
            stable=stable,
        ),
    )
    return InstrumentSample(
        condition=_condition(),
        attempt_index=1,
        sample_index=sample_index,
        lockins=lockins,
        state=state,
    )


class RunMonitorTests(unittest.TestCase):
    def test_reads_new_wal_samples_and_surfaces_safety_warnings(self) -> None:
        database = _database_path()
        with RunStore(database) as store:
            run_id = store.start_run(
                protocol="authorized_hardware_voltage_sweep",
                sample_name="MONITOR_TEST",
                config_json=CONFIG.canonical_json(),
                station_snapshot_json="{}",
            )
            store.record_instrument_sample(run_id, _instrument_sample(0))
            store.record_attempt(
                run_id,
                AttemptResult(
                    condition=_condition(),
                    attempt_index=1,
                    reading=AveragedPair.empty(),
                    accepted=True,
                    flags=(),
                ),
            )
            with RunMonitor(database, run_id=run_id) as monitor:
                first = monitor.snapshot(
                    now=datetime.now(timezone.utc)
                )
                self.assertEqual(first.progress["planned_conditions"], 9)
                self.assertEqual(first.progress["accepted_conditions"], 1)
                self.assertEqual(first.instrument["sample_index"], 0)

                store.record_instrument_sample(
                    run_id,
                    _instrument_sample(
                        1,
                        overload=True,
                        top_current_a=2e-9,
                        top_compliance_a=2e-8,
                        stable=False,
                    ),
                )
                second = monitor.snapshot()
                self.assertEqual(second.instrument["sample_index"], 1)
                warning_text = " ".join(second.warnings)
                self.assertIn("SR830 reports overload", warning_text)
                self.assertIn("Top gate leakage", warning_text)
                self.assertIn("Top gate compliance", warning_text)
                self.assertIn("PPMS state is not stable", warning_text)

                store.finish_run(run_id, "completed")
                final = monitor.snapshot()
                self.assertEqual(final.run["status"], "completed")

        rendered = render_monitor_snapshot(final)
        self.assertIn("READ-ONLY SQLITE", rendered)
        self.assertIn("Ctrl+C in this monitor window stops monitoring only", rendered)

    def test_latest_running_supports_eto_without_raw_instrument_samples(self) -> None:
        database = _database_path()
        with RunStore(database) as store:
            run_id = store.start_run(
                protocol="eto_file_follow",
                sample_name="ETO_MONITOR_TEST",
                config_json=json.dumps({"source": "test.dat"}),
                station_snapshot_json="{}",
            )
            store.record_transport_reading(
                run_id,
                TransportReading(
                    backend="eto",
                    signal="xx",
                    instrument_channel="2",
                    harmonic=1,
                    timestamp_s=1.0,
                    temperature_k=15.0,
                    field_t=0.5,
                    sample_position_deg=0.0,
                    drive_current_a=1e-4,
                    frequency_hz=17.0,
                    x_v=1e-3,
                    y_v=0.0,
                    amplitude_v=1e-3,
                    phase_deg=0.0,
                    ratio_db=None,
                    phase_resolved=True,
                    sequence_index=3,
                    status_code=7,
                ),
            )
            store.record_transport_reading(
                run_id,
                TransportReading(
                    backend="eto",
                    signal="xx",
                    instrument_channel="1",
                    harmonic=1,
                    timestamp_s=1.1,
                    temperature_k=15.1,
                    field_t=0.6,
                    sample_position_deg=1.0,
                    drive_current_a=1e-4,
                    frequency_hz=17.0,
                    x_v=2e-3,
                    y_v=0.0,
                    amplitude_v=2e-3,
                    phase_deg=0.0,
                    ratio_db=None,
                    phase_resolved=True,
                    sequence_index=4,
                    status_code=8,
                ),
            )
            store.record_eto_follow_batch(
                run_id,
                TEST_TEMP_ROOT / "eto-monitor.dat",
                (),
                {
                    "offset_bytes": 100,
                    "line_number": 5,
                    "records_read": 2,
                    "anchor_sha256": "abc",
                },
            )
            with RunMonitor(database, latest_running=True) as monitor:
                snapshot = monitor.snapshot()

        self.assertEqual(snapshot.run["run_id"], run_id)
        self.assertIsNone(snapshot.instrument)
        self.assertEqual(len(snapshot.transport), 2)
        self.assertIsNotNone(snapshot.progress["last_data_age_s"])
        self.assertIsNotNone(snapshot.progress["checkpoint_age_s"])
        self.assertNotIn(
            "No raw instrument sample",
            " ".join(snapshot.warnings),
        )
        rendered = render_monitor_snapshot(snapshot)
        self.assertIn("TRANSPORT-ONLY / ETO", rendered)
        self.assertNotIn("Waiting for a raw sample", rendered)
        self.assertIn("ch=1", rendered)
        self.assertIn("ch=2", rendered)
        self.assertIn("T=15.1 K", rendered)
        self.assertIn("heartbeat age=", rendered)
        self.assertIn("status_code=7", rendered)

    def test_once_cli_and_invalid_selection(self) -> None:
        database = _database_path()
        with RunStore(database) as store:
            run_id = store.start_run(
                protocol="fixed_environment_voltage_sweep",
                sample_name="CLI_MONITOR_TEST",
                config_json=CONFIG.canonical_json(),
                station_snapshot_json="{}",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = _monitor_run_command(
                    database,
                    run_id=run_id,
                    latest_running=False,
                    refresh_s=0.1,
                    once=True,
                )

        self.assertEqual(exit_code, 0)
        self.assertIn(run_id, output.getvalue())
        with self.assertRaises(MonitorError):
            RunMonitor(database)
        with self.assertRaises(MonitorError):
            _monitor_run_command(
                database,
                run_id=run_id,
                latest_running=False,
                refresh_s=float("nan"),
                once=True,
            )


if __name__ == "__main__":
    unittest.main()
