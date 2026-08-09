from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

from ppms_control.plotting import (
    GateCalibration,
    LeakageRecord,
    PlotDataError,
    PlotDataset,
    PlotRecord,
    generate_publication_plots,
    list_sqlite_runs,
    load_gate_calibration,
)
from ppms_control.store import RunStore


HAS_MATPLOTLIB = importlib.util.find_spec("matplotlib") is not None
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / ".test-tmp"
TEST_TEMP_ROOT.mkdir(exist_ok=True)
TEST_OUTPUT_ROOT = TEST_TEMP_ROOT / "plotting-unittest"
TEST_OUTPUT_ROOT.mkdir(exist_ok=True)


def _record(
    source: str,
    observation_index: int,
    *,
    signal: str = "xx",
    harmonic: int = 1,
    temperature_k: float = 10.0,
    field_t: float = 1.0,
    current_a: float = 5e-6,
    frequency_hz: float = 17.7,
    x_v: float = 1e-3,
    ratio_db: float | None = None,
    angle_deg: float | None = None,
    top_gate_v: float | None = None,
    bottom_gate_v: float | None = None,
) -> PlotRecord:
    return PlotRecord(
        source=source,
        observation_index=observation_index,
        backend="synthetic",
        signal=signal,
        instrument_channel=signal,
        harmonic=harmonic,
        timestamp_s=float(observation_index),
        temperature_k=temperature_k,
        field_t=field_t,
        gate_top_voltage_v=top_gate_v,
        gate_bottom_voltage_v=bottom_gate_v,
        sample_position_deg=angle_deg,
        drive_current_a=current_a,
        frequency_hz=frequency_hz,
        x_v=x_v,
        y_v=x_v / 10.0,
        amplitude_v=abs(x_v) * 1.005,
        phase_deg=5.0,
        ratio_db=ratio_db,
        phase_resolved=True,
    )


def _publication_dataset() -> PlotDataset:
    records: list[PlotRecord] = []
    observation = 0

    for current_index, current in enumerate((1e-6, 2e-6, 3e-6, 4e-6, 5e-6)):
        for signal in ("xx", "xy"):
            for harmonic in (1, 2, 3):
                observation += 1
                voltage = (
                    (120.0 if signal == "xx" else 20.0) * current
                    if harmonic == 1
                    else (2e6 if harmonic == 2 else 2e11) * current**harmonic
                )
                records.append(
                    _record(
                        "current.dat",
                        observation,
                        signal=signal,
                        harmonic=harmonic,
                        current_a=current,
                        x_v=voltage,
                        ratio_db=(-35.0 if harmonic == 2 else -52.0),
                    )
                )

    for frequency in (10.0, 30.0, 100.0):
        for signal in ("xx", "xy"):
            for harmonic in (1, 2, 3):
                observation += 1
                voltage = 1e-3 / harmonic / (1.0 + frequency / 200.0)
                records.append(
                    _record(
                        "frequency.dat",
                        observation,
                        signal=signal,
                        harmonic=harmonic,
                        frequency_hz=frequency,
                        x_v=voltage,
                        ratio_db=(-30.0 if harmonic == 2 else -45.0),
                    )
                )

    for temperature in (5.0, 10.0, 15.0):
        for field in (-1.0, 1.0, 2.0):
            observation += 1
            first_voltage = 100.0 * 5e-6
            second_voltage = (
                250.0 * first_voltage * abs(field) * 5e-6 / 2.0
                + temperature * 1e-9
                + (2e-8 if field > 0 else -2e-8)
            )
            records.extend(
                (
                    _record(
                        "temperature_field.dat",
                        observation,
                        harmonic=1,
                        temperature_k=temperature,
                        field_t=field,
                        x_v=first_voltage,
                    ),
                    _record(
                        "temperature_field.dat",
                        observation,
                        harmonic=2,
                        temperature_k=temperature,
                        field_t=field,
                        x_v=second_voltage,
                        ratio_db=-42.0,
                    ),
                )
            )

    for angle in (0.0, 90.0, 180.0, 270.0):
        observation += 1
        records.append(
            _record(
                "angle.dat",
                observation,
                harmonic=2,
                angle_deg=angle,
                x_v=(1.0 + angle / 360.0) * 1e-6,
            )
        )

    leakage: list[LeakageRecord] = []
    for top in (-1.0, 1.0):
        for bottom in (-1.0, 1.0):
            observation += 1
            records.append(
                _record(
                    "gate.dat",
                    observation,
                    top_gate_v=top,
                    bottom_gate_v=bottom,
                    x_v=(1000.0 + 10.0 * top + 5.0 * bottom) * 5e-6,
                )
            )
            leakage.append(
                LeakageRecord(
                    source="gate.dat",
                    sequence_index=observation,
                    sample_index=0,
                    gate_top_voltage_v=top,
                    gate_bottom_voltage_v=bottom,
                    top_current_a=(2.0 + abs(top)) * 1e-10,
                    bottom_current_a=(2.0 + abs(bottom)) * 1e-10,
                )
            )

    return PlotDataset(
        records=tuple(records),
        leakage=tuple(leakage),
        metadata={"input_kind": "synthetic_test"},
    )


class GateCalibrationTests(unittest.TestCase):
    def test_calibration_is_strict_and_converts_coordinates(self) -> None:
        path = TEST_OUTPUT_ROOT / "calibration.toml"
        path.write_text(
            """
[gate_calibration]
top_capacitance_f_per_m2 = 0.01
bottom_capacitance_f_per_m2 = 0.02
top_offset_v = 0.1
bottom_offset_v = -0.2
""".strip(),
            encoding="utf-8",
        )
        calibration = load_gate_calibration(path)

        density, displacement = calibration.convert(1.1, 0.8)
        self.assertGreater(density, 0.0)
        self.assertAlmostEqual(displacement, 0.005)

    def test_calibration_rejects_unknown_keys(self) -> None:
        path = TEST_OUTPUT_ROOT / "invalid-calibration.toml"
        path.write_text(
            """
[gate_calibration]
top_capacitance_f_per_m2 = 0.01
bottom_capacitance_f_per_m2 = 0.02
assumed_dielectric = 3.0
""".strip(),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(PlotDataError, "unknown"):
                load_gate_calibration(path)

    def test_recent_sqlite_runs_can_be_listed_read_only(self) -> None:
        database = TEST_OUTPUT_ROOT / "run-list.sqlite"
        with RunStore(database) as store:
            run_id = store.start_run(
                protocol="notebook_run_list_test",
                sample_name="RUN_LIST_SAMPLE",
                config_json="{}",
                station_snapshot_json="{}",
            )
            store.finish_run(run_id, "completed")

        rows = list_sqlite_runs(database, limit=5)
        selected = next(row for row in rows if row["run_id"] == run_id)
        self.assertEqual(selected["status"], "completed")
        self.assertEqual(selected["sample_name"], "RUN_LIST_SAMPLE")


@unittest.skipUnless(HAS_MATPLOTLIB, "matplotlib analysis extra is not installed")
class PublicationPlotTests(unittest.TestCase):
    def test_generates_paper_notebook_and_gate_products(self) -> None:
        calibration = GateCalibration(0.01, 0.02)
        output = TEST_OUTPUT_ROOT / "publication"
        result = generate_publication_plots(
            _publication_dataset(),
            output,
            calibration=calibration,
            formats=("png",),
        )
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        statuses = {
            entry["key"]: entry["status"] for entry in manifest["figures"]
        }
        expected = {
            "current_response",
            "harmonic_ratio_db",
            "harmonic_scaling",
            "frequency_response",
            "temperature_dependence",
            "field_dependence",
            "angle_dependence",
            "magnetochiral_gamma",
            "temperature_field_v2_over_b_map",
            "transport_phase_overview",
            "gate_resistance_map",
            "gate_leakage",
            "n_d_resistance_map",
        }
        self.assertTrue(expected.issubset(statuses))
        self.assertTrue(all(statuses[key] == "generated" for key in expected))
        self.assertEqual(statuses["nernst_temperature_field_map"], "skipped")
        self.assertTrue(Path(result["analysis_records_csv"]).is_file())
        self.assertTrue(Path(result["fit_summary_csv"]).is_file())

    def test_generates_paired_gate_linecut_for_non_grid_path(self) -> None:
        records = tuple(
            _record(
                "paired_gate",
                index,
                top_gate_v=top,
                bottom_gate_v=bottom,
                x_v=(1000.0 + index) * 5e-6,
            )
            for index, (top, bottom) in enumerate(
                ((-1.0, -2.0), (0.0, 0.0), (1.0, 2.0)), start=1
            )
        )
        dataset = PlotDataset(records, (), {"input_kind": "synthetic_test"})
        output = TEST_OUTPUT_ROOT / "paired-gate"
        result = generate_publication_plots(dataset, output, formats=("png",))
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        statuses = {entry["key"]: entry["status"] for entry in manifest["figures"]}
        self.assertEqual(statuses["paired_gate_linecut"], "generated")


if __name__ == "__main__":
    unittest.main()
