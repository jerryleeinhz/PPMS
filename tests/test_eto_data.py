from __future__ import annotations

import csv
import io
from pathlib import Path
import unittest

from ppms_control.eto_data import (
    EtoDataError,
    EtoDataFollower,
    EtoFollowCheckpoint,
    eto_transport_readings,
    load_eto_data,
)
from ppms_control.eto_follow import ingest_eto_increment
from ppms_control.store import RunStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = PROJECT_ROOT / ".test-tmp"


def _eto_text(rows: list[dict[str, str]]) -> str:
    global_columns = [
        "Comment",
        "Time Stamp (s)",
        "Temperature (K)",
        "Field (Oe)",
        "Sample Position (deg)",
        "Chamber Pressure (Torr)",
    ]
    channel_stems = [
        ("Resistance", "Ohms"),
        ("Phase Angle", "deg"),
        ("I-V Current", "mA"),
        ("I-V Voltage", "V"),
        ("Frequency", "Hz"),
        ("Averaging Time", "s"),
        ("AC Current", "mA"),
        ("DC Current", "mA"),
        ("Voltage Ampl", "V"),
        ("In Phase Voltage Ampl", "V"),
        ("Quadrature Voltage", "V"),
    ]
    channel_columns: list[str] = []
    for channel in (1, 2):
        channel_columns.extend(
            f"{name} Ch{channel} ({unit})" for name, unit in channel_stems
        )
        channel_columns.extend(
            [
                f"Gain Ch{channel}",
                f"2nd Harmonic Ch{channel} (dB)",
                f"3rd Harmonic Ch{channel} (dB)",
            ]
        )
    status_columns = [
        "ETO Status Code",
        "ETO Measurement Mode",
        "Temperature Status (code)",
        "Field Status (code)",
        "Chamber Status (code)",
    ]
    output = io.StringIO()
    output.write("[Header]\nBYAPP, Electrical Transport Option, Release 1.2.0 Build 0\n[Data]\n")
    writer = csv.DictWriter(output, fieldnames=global_columns + channel_columns + status_columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


class EtoDataTests(unittest.TestCase):
    def _write(self, text: str, filename: str) -> Path:
        TEST_TEMP_ROOT.mkdir(exist_ok=True)
        path = TEST_TEMP_ROOT / filename
        path.write_text(text, encoding="utf-8")
        return path

    def test_parses_alternating_channels_and_converts_units(self) -> None:
        rows = [
            {
                "Time Stamp (s)": "3994836634.0",
                "Temperature (K)": "15.0",
                "Field (Oe)": "-10000",
                "Sample Position (deg)": "12.5",
                "Chamber Pressure (Torr)": "12.12",
                "Frequency Ch1 (Hz)": "0.4359754",
                "AC Current Ch1 (mA)": "0.02",
                "Voltage Ampl Ch1 (V)": "0.2",
                "In Phase Voltage Ampl Ch1 (V)": "0.18",
                "Quadrature Voltage Ch1 (V)": "0.08",
                "2nd Harmonic Ch1 (dB)": "-20",
                "3rd Harmonic Ch1 (dB)": "-40",
                "ETO Status Code": "33848192",
                "ETO Measurement Mode": "0",
                "Temperature Status (code)": "1",
                "Field Status (code)": "4",
                "Chamber Status (code)": "1",
            },
            {
                "Comment": "Ch. 2 Input voltage exceeded compliance limit.",
                "Time Stamp (s)": "3994836650.0",
                "Temperature (K)": "15.1",
                "Field (Oe)": "5000",
                "Frequency Ch2 (Hz)": "0.5086185",
                "AC Current Ch2 (mA)": "0.04",
                "Voltage Ampl Ch2 (V)": "0.5",
                "2nd Harmonic Ch2 (dB)": "-6.020599913",
                "3rd Harmonic Ch2 (dB)": "-20",
            },
        ]
        parsed = load_eto_data(self._write(_eto_text(rows), "eto_alternating.dat"))

        self.assertEqual(len(parsed.records), 2)
        first, second = parsed.records
        self.assertEqual(first.active_channels, (1,))
        self.assertEqual(second.active_channels, (2,))
        self.assertAlmostEqual(first.field_t, -1.0)
        self.assertAlmostEqual(first.channel_1.ac_current_a, 20e-6)
        self.assertAlmostEqual(first.channel_1.harmonic_amplitude_v(2), 0.02)
        self.assertAlmostEqual(first.channel_1.harmonic_amplitude_v(3), 0.002)
        self.assertAlmostEqual(second.channel_2.harmonic_amplitude_v(2), 0.25)
        self.assertIn("compliance", second.comment)

        summary = parsed.summary()
        self.assertEqual(summary["active_channel_records"]["channel_1"], 1)
        self.assertEqual(summary["active_channel_records"]["channel_2"], 1)
        self.assertEqual(summary["field_range_t"], [-1.0, 0.5])
        self.assertEqual(summary["channel_1"]["ac_current_range_a"], [20e-6, 20e-6])
        self.assertEqual(
            summary["comment_counts"],
            {"Ch. 2 Input voltage exceeded compliance limit.": 1},
        )

        normalized = eto_transport_readings(parsed, {1: "xy", 2: "xx"})
        self.assertEqual(len(normalized), 6)
        self.assertEqual(
            [(item.signal, item.harmonic) for item in normalized],
            [("xy", 1), ("xy", 2), ("xy", 3), ("xx", 1), ("xx", 2), ("xx", 3)],
        )
        self.assertTrue(normalized[0].phase_resolved)
        self.assertFalse(normalized[1].phase_resolved)
        self.assertIsNone(normalized[1].x_v)

        with RunStore(":memory:") as store:
            run_id = store.start_run(
                protocol="eto_file_import",
                sample_name="TEST",
                config_json="{}",
                station_snapshot_json="{}",
            )
            for reading in normalized:
                store.record_transport_reading(run_id, reading)
            self.assertEqual(store.transport_reading_count(run_id), 6)

    def test_normalization_requires_an_explicit_role_for_each_active_channel(self) -> None:
        row = {
            "Time Stamp (s)": "1",
            "Temperature (K)": "15",
            "Field (Oe)": "0",
            "Voltage Ampl Ch2 (V)": "0.1",
        }
        parsed = load_eto_data(self._write(_eto_text([row]), "eto_missing_role.dat"))
        with self.assertRaisesRegex(EtoDataError, "no signal role"):
            eto_transport_readings(parsed, {1: "xy"})

    def test_rejects_file_without_data_marker(self) -> None:
        path = self._write("[Header]\n", "eto_no_data.dat")
        with self.assertRaisesRegex(EtoDataError, r"\[Data\]"):
            load_eto_data(path)

    def test_rejects_nonfinite_measurement(self) -> None:
        row = {
            "Time Stamp (s)": "1",
            "Temperature (K)": "nan",
            "Field (Oe)": "0",
        }
        path = self._write(_eto_text([row]), "eto_nonfinite.dat")
        with self.assertRaisesRegex(EtoDataError, "not finite"):
            load_eto_data(path)

    def test_incremental_follower_defers_partial_row_and_resumes_from_checkpoint(
        self,
    ) -> None:
        rows = [
            {
                "Time Stamp (s)": "1",
                "Temperature (K)": "15.0",
                "Field (Oe)": "0",
                "Voltage Ampl Ch1 (V)": "0.1",
            },
            {
                "Time Stamp (s)": "2",
                "Temperature (K)": "15.1",
                "Field (Oe)": "100",
                "Voltage Ampl Ch2 (V)": "0.2",
            },
        ]
        complete_text = _eto_text(rows)
        lines = complete_text.splitlines(keepends=True)
        partial_row = lines[-1]
        split_at = len(partial_row) // 2
        path = self._write(
            "".join(lines[:-1]) + partial_row[:split_at],
            "eto_growing.dat",
        )

        follower = EtoDataFollower(path)
        first = follower.poll()
        self.assertEqual([record.timestamp_s for record in first.records], [1.0])
        checkpoint = EtoFollowCheckpoint.from_dict(follower.checkpoint.as_dict())

        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(partial_row[split_at:])
        resumed = EtoDataFollower(path, checkpoint=checkpoint)
        second = resumed.poll()
        self.assertEqual([record.timestamp_s for record in second.records], [2.0])
        self.assertEqual(resumed.poll().records, ())

        full = load_eto_data(path)
        self.assertEqual(
            [record.row_number for record in first.records + second.records],
            [record.row_number for record in full.records],
        )

    def test_incremental_follower_detects_changed_consumed_data(self) -> None:
        row = {
            "Time Stamp (s)": "1",
            "Temperature (K)": "15.0",
            "Field (Oe)": "0",
            "Voltage Ampl Ch1 (V)": "0.1",
        }
        path = self._write(_eto_text([row]), "eto_rewritten.dat")
        follower = EtoDataFollower(path)
        self.assertEqual(len(follower.poll().records), 1)
        path.write_bytes(path.read_bytes().replace(b"15.0", b"16.0", 1))
        with self.assertRaisesRegex(EtoDataError, "consumed.*changed"):
            follower.poll()

    def test_incremental_follower_can_flush_final_row_without_newline(self) -> None:
        row = {
            "Time Stamp (s)": "1",
            "Temperature (K)": "15",
            "Field (Oe)": "0",
            "Voltage Ampl Ch1 (V)": "0.1",
        }
        path = self._write(_eto_text([row]).rstrip("\r\n"), "eto_final_row.dat")
        follower = EtoDataFollower(path)
        self.assertEqual(follower.poll().records, ())
        self.assertEqual(len(follower.poll(final=True).records), 1)

    def test_incremental_ingest_commits_readings_and_checkpoint_without_duplicates(
        self,
    ) -> None:
        rows = [
            {
                "Time Stamp (s)": "1",
                "Temperature (K)": "15.0",
                "Field (Oe)": "0",
                "Voltage Ampl Ch1 (V)": "0.1",
            },
            {
                "Time Stamp (s)": "2",
                "Temperature (K)": "15.1",
                "Field (Oe)": "100",
                "Voltage Ampl Ch2 (V)": "0.2",
            },
        ]
        complete_text = _eto_text(rows)
        lines = complete_text.splitlines(keepends=True)
        partial_row = lines[-1]
        split_at = len(partial_row) // 2
        path = self._write(
            "".join(lines[:-1]) + partial_row[:split_at],
            "eto_ingest_growing.dat",
        )

        with RunStore(":memory:") as store:
            run_id = store.start_run(
                protocol="eto_file_follow",
                sample_name="TEST",
                config_json="{}",
                station_snapshot_json="{}",
            )
            first = ingest_eto_increment(store, run_id, path, {1: "xy", 2: "xx"})
            self.assertEqual(first.new_records, 1)
            self.assertEqual(first.new_transport_readings, 1)

            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(partial_row[split_at:])
            second = ingest_eto_increment(store, run_id, path, {1: "xy", 2: "xx"})
            third = ingest_eto_increment(store, run_id, path, {1: "xy", 2: "xx"})
            self.assertEqual(second.new_records, 1)
            self.assertEqual(second.total_records, 2)
            self.assertEqual(third.new_records, 0)
            self.assertEqual(store.transport_reading_count(run_id), 2)
            saved = store.load_eto_follow_checkpoint(run_id, path)
            self.assertEqual(saved["records_read"], 2)


if __name__ == "__main__":
    unittest.main()
