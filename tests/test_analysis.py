from __future__ import annotations

import unittest

from ppms_control.analysis import summarize_transport_rows


def _row(
    reading_id: int,
    *,
    phase_deg: float,
    sequence_index: int | None = 0,
    source_row: int | None = None,
) -> dict[str, object]:
    return {
        "reading_id": reading_id,
        "sequence_index": sequence_index,
        "source_row": source_row,
        "backend": "sr_lockin" if sequence_index is not None else "eto",
        "signal": "xx",
        "instrument_channel": "sr830" if sequence_index is not None else "eto_ch2",
        "harmonic": 1,
        "timestamp_s": float(reading_id),
        "temperature_k": 15.0,
        "field_t": 0.0,
        "sample_position_deg": 0.0,
        "drive_current_a": 1e-6,
        "frequency_hz": 17.777,
        "x_v": 1e-3,
        "y_v": 0.0,
        "amplitude_v": 1e-3,
        "phase_deg": phase_deg,
        "ratio_db": None,
        "phase_resolved": 1,
        "comment": "",
        "status_code": None,
        "quality_flags_json": "[]",
    }


class TransportAnalysisTests(unittest.TestCase):
    def test_phase_uses_circular_mean_across_wraparound(self) -> None:
        summary = summarize_transport_rows(
            [_row(1, phase_deg=179.0), _row(2, phase_deg=-179.0)]
        )[0]
        self.assertAlmostEqual(abs(summary["phase_deg_mean"]), 180.0)
        self.assertLess(summary["phase_deg_std"], 2.0)
        self.assertEqual(summary["sample_count"], 2)

    def test_eto_source_rows_are_not_paired_or_averaged_together(self) -> None:
        summaries = summarize_transport_rows(
            [
                _row(1, phase_deg=10.0, sequence_index=None, source_row=5),
                _row(2, phase_deg=20.0, sequence_index=None, source_row=6),
            ]
        )
        self.assertEqual(len(summaries), 2)
        self.assertEqual(
            [summary["observation_index"] for summary in summaries],
            [5, 6],
        )


if __name__ == "__main__":
    unittest.main()
