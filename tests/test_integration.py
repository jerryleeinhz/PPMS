from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from ppms_control.acquisition import AcquisitionError, MeasurementEngine
from ppms_control.config import DataConfig, load_config
from ppms_control.instruments import build_simulated_bundle
from ppms_control.protocols import current_sweep_conditions, run_current_sweep
from ppms_control.safety import SafeStation
from ppms_control.store import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_OUTPUT = PROJECT_ROOT / ".test-tmp" / "accepted.csv"


class SimulationIntegrationTests(unittest.TestCase):
    def _config(self, database: Path):
        base = load_config(PROJECT_ROOT / "config" / "simulation.toml")
        return replace(base, data=DataConfig(database))

    @staticmethod
    def _start_run(store: RunStore, station: SafeStation, config, resume_run_id=None) -> str:
        return store.start_run(
            protocol="fixed_environment_current_sweep",
            sample_name=config.runtime.sample_name,
            config_json=config.canonical_json(),
            station_snapshot_json=json.dumps(station.qcodes_snapshot, default=str, sort_keys=True),
            resume_run_id=resume_run_id,
        )

    def test_end_to_end_current_sweep_and_csv_export(self) -> None:
        config = self._config(Path(":memory:"))
        bundle = build_simulated_bundle(config)
        station = SafeStation(bundle, config)
        try:
            with RunStore(":memory:") as store:
                run_id = self._start_run(store, station, config)
                engine = MeasurementEngine(
                    station, store, run_id, config.acquisition, config.instruments
                )
                measured = run_current_sweep(engine, store, run_id, config.current_sweep)
                store.finish_run(run_id, "completed")
                self.assertEqual(measured, config.current_sweep.points)
                self.assertEqual(
                    store.attempt_count(run_id, accepted=True), config.current_sweep.points
                )
                store.export_accepted_csv(run_id, CSV_OUTPUT)
            self.assertEqual(len(CSV_OUTPUT.read_text(encoding="utf-8").splitlines()), 10)
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
                    station, store, run_id, config.acquisition, config.instruments
                )
                condition = current_sweep_conditions(config.current_sweep)[0]
                with self.assertRaises(AcquisitionError):
                    engine.acquire(condition)
                self.assertEqual(
                    store.attempt_count(run_id, accepted=False),
                    config.acquisition.max_attempts,
                )
                self.assertEqual(float(bundle.sr830.source_current_a.get()), 0.0)
                store.finish_run(run_id, "failed")
        finally:
            station.safe_shutdown()
            bundle.close()

    def test_resume_skips_already_accepted_conditions(self) -> None:
        config = self._config(Path(":memory:"))
        conditions = current_sweep_conditions(config.current_sweep)

        first_bundle = build_simulated_bundle(config)
        first_station = SafeStation(first_bundle, config)
        second_bundle = None
        second_station = None
        try:
            with RunStore(":memory:") as store:
                run_id = self._start_run(store, first_station, config)
                first_engine = MeasurementEngine(
                    first_station, store, run_id, config.acquisition, config.instruments
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
                    config.instruments,
                )
                measured = run_current_sweep(
                    second_engine, store, resumed_id, config.current_sweep
                )
                store.finish_run(resumed_id, "completed")
                self.assertEqual(measured, config.current_sweep.points - 3)
                self.assertEqual(
                    store.attempt_count(resumed_id, accepted=True),
                    config.current_sweep.points,
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
