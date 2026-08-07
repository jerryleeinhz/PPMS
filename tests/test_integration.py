from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from ppms_control.acquisition import AcquisitionError, MeasurementEngine
from ppms_control.config import DataConfig, load_config
from ppms_control.instruments import build_simulated_bundle
from ppms_control.protocols import (
    field_sweep_conditions,
    frequency_sweep_conditions,
    prepare_field_sweep,
    prepare_temperature_field_sweep,
    run_field_sweep,
    run_frequency_sweep,
    run_temperature_field_sweep,
    run_voltage_sweep,
    temperature_field_sweep_conditions,
    voltage_sweep_conditions,
)
from ppms_control.safety import SafeStation
from ppms_control.store import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_OUTPUT = PROJECT_ROOT / ".test-tmp" / "accepted.csv"
SAMPLES_CSV_OUTPUT = PROJECT_ROOT / ".test-tmp" / "instrument_samples.csv"
TRANSPORT_CSV_OUTPUT = PROJECT_ROOT / ".test-tmp" / "transport_readings.csv"
TRANSPORT_SUMMARY_CSV_OUTPUT = PROJECT_ROOT / ".test-tmp" / "transport_summary.csv"


class SimulationIntegrationTests(unittest.TestCase):
    def _config(self, database: Path):
        base = load_config(PROJECT_ROOT / "config" / "simulation.toml")
        return replace(base, data=DataConfig(database))

    @staticmethod
    def _start_run(store: RunStore, station: SafeStation, config, resume_run_id=None) -> str:
        return store.start_run(
            protocol="fixed_environment_voltage_sweep",
            sample_name=config.runtime.sample_name,
            config_json=config.canonical_json(),
            station_snapshot_json=json.dumps(station.qcodes_snapshot, default=str, sort_keys=True),
            resume_run_id=resume_run_id,
        )

    def test_end_to_end_voltage_sweep_records_every_instrument_sample(self) -> None:
        config = self._config(Path(":memory:"))
        bundle = build_simulated_bundle(config)
        station = SafeStation(bundle, config)
        try:
            with RunStore(":memory:") as store:
                run_id = self._start_run(store, station, config)
                engine = MeasurementEngine(
                    station, store, run_id, config.acquisition
                )
                measured = run_voltage_sweep(
                    engine,
                    store,
                    run_id,
                    config.voltage_sweep,
                    frequency_hz=config.instruments.reference_frequency_hz,
                    series_resistance_ohm=config.instruments.series_resistance_ohm,
                )
                store.finish_run(run_id, "completed")
                self.assertEqual(measured, config.voltage_sweep.points)
                self.assertEqual(
                    store.attempt_count(run_id, accepted=True), config.voltage_sweep.points
                )
                self.assertEqual(
                    store.instrument_sample_count(run_id),
                    config.voltage_sweep.points * config.acquisition.averages * 3,
                )
                self.assertEqual(
                    store.transport_reading_count(run_id),
                    config.voltage_sweep.points * config.acquisition.averages * 6,
                )
                transport_rows = store._connection.execute(
                    """
                    SELECT backend, signal, instrument_channel, harmonic,
                           phase_resolved, drive_current_a, quality_flags_json
                    FROM transport_readings
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
                self.assertEqual(
                    {
                        (
                            row["backend"],
                            row["signal"],
                            row["instrument_channel"],
                            row["harmonic"],
                            row["phase_resolved"],
                        )
                        for row in transport_rows
                    },
                    {
                        ("sr_lockin", signal, instrument, harmonic, 1)
                        for signal, instrument in (("xx", "sr830"), ("xy", "sr865a"))
                        for harmonic in (1, 2, 3)
                    },
                )
                self.assertTrue(
                    all(row["drive_current_a"] is not None for row in transport_rows)
                )
                self.assertTrue(
                    all(json.loads(row["quality_flags_json"]) == [] for row in transport_rows)
                )
                harmonic_rows = store._connection.execute(
                    """
                    SELECT sr830_harmonic, sr865a_harmonic
                    FROM instrument_samples
                    WHERE run_id = ?
                    ORDER BY sample_id
                    LIMIT ?
                    """,
                    (run_id, config.acquisition.averages * 3),
                ).fetchall()
                self.assertEqual(
                    [
                        (row["sr830_harmonic"], row["sr865a_harmonic"])
                        for row in harmonic_rows
                    ],
                    [
                        (harmonic, harmonic)
                        for harmonic in (1, 2, 3)
                        for _ in range(config.acquisition.averages)
                    ],
                )
                store.export_accepted_csv(run_id, CSV_OUTPUT)
                store.export_instrument_samples_csv(run_id, SAMPLES_CSV_OUTPUT)
                store.export_transport_readings_csv(run_id, TRANSPORT_CSV_OUTPUT)
                store.export_transport_summary_csv(run_id, TRANSPORT_SUMMARY_CSV_OUTPUT)
            self.assertEqual(len(CSV_OUTPUT.read_text(encoding="utf-8").splitlines()), 10)
            self.assertEqual(
                len(SAMPLES_CSV_OUTPUT.read_text(encoding="utf-8").splitlines()),
                config.voltage_sweep.points * config.acquisition.averages * 3 + 1,
            )
            header = SAMPLES_CSV_OUTPUT.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("source_voltage_read_v", header)
            self.assertIn("gate_top_measured_current_a", header)
            self.assertIn("ppms_temperature_read_k", header)
            transport_lines = TRANSPORT_CSV_OUTPUT.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                len(transport_lines),
                config.voltage_sweep.points * config.acquisition.averages * 6 + 1,
            )
            self.assertIn("x_over_drive_current_ohm", transport_lines[0])
            self.assertIn("amplitude_over_drive_current_ohm", transport_lines[0])
            summary_lines = TRANSPORT_SUMMARY_CSV_OUTPUT.read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(
                len(summary_lines),
                config.voltage_sweep.points * 6 + 1,
            )
            self.assertIn("sample_count", summary_lines[0])
            self.assertTrue(
                all(
                    f",{config.acquisition.averages}," in line
                    for line in summary_lines[1:]
                )
            )
        finally:
            station.safe_shutdown()
            bundle.close()

    def test_field_sweep_moves_environment_and_records_rotator_state(self) -> None:
        config = self._config(Path(":memory:"))
        conditions = field_sweep_conditions(
            config.field_sweep,
            series_resistance_ohm=config.instruments.series_resistance_ohm,
        )
        bundle = build_simulated_bundle(config)
        station = SafeStation(bundle, config)
        try:
            with RunStore(":memory:") as store:
                run_id = store.start_run(
                    protocol="fixed_excitation_field_sweep",
                    sample_name=config.runtime.sample_name,
                    config_json=config.canonical_json(),
                    station_snapshot_json="{}",
                )
                engine = MeasurementEngine(
                    station,
                    store,
                    run_id,
                    config.acquisition,
                )
                prepare_field_sweep(station, config.field_sweep)
                measured = run_field_sweep(
                    engine,
                    station,
                    store,
                    run_id,
                    config.field_sweep,
                    series_resistance_ohm=config.instruments.series_resistance_ohm,
                )
                self.assertEqual(measured, config.field_sweep.points)
                rows = store._connection.execute(
                    """
                    SELECT DISTINCT field_t, sample_position_deg
                    FROM transport_readings
                    WHERE run_id = ?
                    ORDER BY field_t
                    """,
                    (run_id,),
                ).fetchall()
                self.assertEqual(
                    [row["field_t"] for row in rows],
                    sorted(condition.field_t for condition in conditions),
                )
                self.assertTrue(all(row["sample_position_deg"] == 0.0 for row in rows))
        finally:
            station.safe_shutdown()
            bundle.close()

    def test_temperature_field_sweep_records_complete_grid(self) -> None:
        config = self._config(Path(":memory:"))
        conditions = temperature_field_sweep_conditions(
            config.temperature_field_sweep,
            series_resistance_ohm=config.instruments.series_resistance_ohm,
        )
        expected_points = (
            config.temperature_field_sweep.temperature_points
            * config.temperature_field_sweep.field_points
        )
        self.assertEqual(len(conditions), expected_points)
        bundle = build_simulated_bundle(config)
        station = SafeStation(bundle, config)
        try:
            with RunStore(":memory:") as store:
                run_id = store.start_run(
                    protocol="fixed_excitation_temperature_field_sweep",
                    sample_name=config.runtime.sample_name,
                    config_json=config.canonical_json(),
                    station_snapshot_json="{}",
                )
                engine = MeasurementEngine(
                    station,
                    store,
                    run_id,
                    config.acquisition,
                )
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
                self.assertEqual(measured, expected_points)
                rows = store._connection.execute(
                    """
                    SELECT DISTINCT temperature_k, field_t
                    FROM transport_readings
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchall()
                self.assertEqual(
                    {(row["temperature_k"], row["field_t"]) for row in rows},
                    {(condition.temperature_k, condition.field_t) for condition in conditions},
                )
        finally:
            station.safe_shutdown()
            bundle.close()

    def test_unlocked_reference_rejects_every_attempt(self) -> None:
        config = self._config(Path(":memory:"))
        bundle = build_simulated_bundle(config)
        station = SafeStation(bundle, config)
        bundle.sr865a.reference_locked.set(False)
        try:
            with RunStore(":memory:") as store:
                run_id = self._start_run(store, station, config)
                engine = MeasurementEngine(
                    station, store, run_id, config.acquisition
                )
                condition = voltage_sweep_conditions(
                    config.voltage_sweep,
                    frequency_hz=config.instruments.reference_frequency_hz,
                    series_resistance_ohm=config.instruments.series_resistance_ohm,
                )[0]
                with self.assertRaises(AcquisitionError):
                    engine.acquire(condition)
                self.assertEqual(
                    store.attempt_count(run_id, accepted=False),
                    config.acquisition.max_attempts,
                )
                self.assertEqual(
                    float(bundle.sr830.source_voltage_v.get()),
                    config.safety.source_safe_idle_voltage_v,
                )
                store.finish_run(run_id, "failed")
        finally:
            station.safe_shutdown()
            bundle.close()

    def test_frequency_sweep_sets_distinct_frequencies_and_records_six_signals(self) -> None:
        config = self._config(Path(":memory:"))
        conditions = frequency_sweep_conditions(
            config.frequency_sweep,
            series_resistance_ohm=config.instruments.series_resistance_ohm,
        )
        self.assertEqual(len({item.condition_id for item in conditions}), len(conditions))

        bundle = build_simulated_bundle(config)
        station = SafeStation(bundle, config)
        try:
            with RunStore(":memory:") as store:
                run_id = store.start_run(
                    protocol="fixed_environment_frequency_sweep",
                    sample_name=config.runtime.sample_name,
                    config_json=config.canonical_json(),
                    station_snapshot_json="{}",
                )
                engine = MeasurementEngine(
                    station,
                    store,
                    run_id,
                    config.acquisition,
                )
                measured = run_frequency_sweep(
                    engine,
                    store,
                    run_id,
                    config.frequency_sweep,
                    series_resistance_ohm=config.instruments.series_resistance_ohm,
                )
                self.assertEqual(measured, config.frequency_sweep.points)
                self.assertEqual(
                    store.instrument_sample_count(run_id),
                    config.frequency_sweep.points * config.acquisition.averages * 3,
                )
                self.assertEqual(
                    store.transport_reading_count(run_id),
                    config.frequency_sweep.points * config.acquisition.averages * 6,
                )
                rows = store._connection.execute(
                    """
                    SELECT DISTINCT source_frequency_set_hz, source_frequency_read_hz
                    FROM instrument_samples
                    WHERE run_id = ?
                    ORDER BY source_frequency_set_hz
                    """,
                    (run_id,),
                ).fetchall()
                self.assertEqual(
                    [row["source_frequency_set_hz"] for row in rows],
                    sorted(item.frequency_hz for item in conditions),
                )
                self.assertTrue(
                    all(
                        row["source_frequency_set_hz"] == row["source_frequency_read_hz"]
                        for row in rows
                    )
                )
        finally:
            station.safe_shutdown()
            bundle.close()

    def test_resume_skips_already_accepted_conditions(self) -> None:
        config = self._config(Path(":memory:"))
        conditions = voltage_sweep_conditions(
            config.voltage_sweep,
            frequency_hz=config.instruments.reference_frequency_hz,
            series_resistance_ohm=config.instruments.series_resistance_ohm,
        )

        first_bundle = build_simulated_bundle(config)
        first_station = SafeStation(first_bundle, config)
        second_bundle = None
        second_station = None
        try:
            with RunStore(":memory:") as store:
                run_id = self._start_run(store, first_station, config)
                first_engine = MeasurementEngine(
                    first_station, store, run_id, config.acquisition
                )
                for condition in conditions[:3]:
                    first_engine.acquire(condition)
                store.finish_run(run_id, "failed")
                first_station.safe_shutdown()
                first_bundle.close()

                second_bundle = build_simulated_bundle(config)
                second_station = SafeStation(second_bundle, config)
                resumed_id = self._start_run(
                    store, second_station, config, resume_run_id=run_id
                )
                second_engine = MeasurementEngine(
                    second_station,
                    store,
                    resumed_id,
                    config.acquisition,
                )
                measured = run_voltage_sweep(
                    second_engine,
                    store,
                    resumed_id,
                    config.voltage_sweep,
                    frequency_hz=config.instruments.reference_frequency_hz,
                    series_resistance_ohm=config.instruments.series_resistance_ohm,
                )
                store.finish_run(resumed_id, "completed")
                self.assertEqual(measured, config.voltage_sweep.points - 3)
                self.assertEqual(
                    store.attempt_count(resumed_id, accepted=True),
                    config.voltage_sweep.points,
                )
        finally:
            if second_station is not None:
                second_station.safe_shutdown()
            if second_bundle is not None:
                second_bundle.close()
            else:
                first_station.safe_shutdown()
                first_bundle.close()


if __name__ == "__main__":
    unittest.main()
