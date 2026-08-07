from __future__ import annotations

from collections import defaultdict
import json
import math
from statistics import fmean, stdev
from typing import Iterable, Mapping


_NUMERIC_FIELDS = (
    "timestamp_s",
    "temperature_k",
    "field_t",
    "sample_position_deg",
    "drive_current_a",
    "frequency_hz",
    "x_v",
    "y_v",
    "amplitude_v",
    "phase_deg",
    "ratio_db",
)


def _mean_and_std(
    rows: list[Mapping[str, object]],
    field: str,
) -> tuple[float | None, float | None]:
    values = [float(row[field]) for row in rows if row[field] is not None]
    if not values:
        return None, None
    return fmean(values), stdev(values) if len(values) > 1 else 0.0


def _circular_mean_and_std_deg(
    rows: list[Mapping[str, object]],
    field: str,
) -> tuple[float | None, float | None]:
    values = [math.radians(float(row[field])) for row in rows if row[field] is not None]
    if not values:
        return None, None
    mean_sin = fmean(math.sin(value) for value in values)
    mean_cos = fmean(math.cos(value) for value in values)
    resultant = math.hypot(mean_sin, mean_cos)
    if resultant < 1e-15:
        return None, None
    mean_deg = math.degrees(math.atan2(mean_sin, mean_cos))
    std_deg = 0.0 if len(values) == 1 else math.degrees(
        math.sqrt(max(0.0, -2.0 * math.log(min(1.0, resultant))))
    )
    return mean_deg, std_deg


def summarize_transport_rows(
    rows: Iterable[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Average repeats without pairing distinct channels or ETO source rows."""

    groups: dict[tuple[object, ...], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        if row["sequence_index"] is not None:
            observation_kind = "sequence_index"
            observation_index = int(row["sequence_index"])
        elif row["source_row"] is not None:
            observation_kind = "source_row"
            observation_index = int(row["source_row"])
        else:
            observation_kind = "reading_id"
            observation_index = int(row["reading_id"])
        key = (
            str(row["backend"]),
            observation_kind,
            observation_index,
            str(row["signal"]),
            str(row["instrument_channel"]),
            int(row["harmonic"]),
        )
        groups[key].append(row)

    summaries: list[dict[str, object]] = []
    for key, grouped_rows in groups.items():
        backend, observation_kind, observation_index, signal, channel, harmonic = key
        summary: dict[str, object] = {
            "backend": backend,
            "observation_kind": observation_kind,
            "observation_index": observation_index,
            "signal": signal,
            "instrument_channel": channel,
            "harmonic": harmonic,
            "sample_count": len(grouped_rows),
            "phase_resolved": int(
                all(bool(row["phase_resolved"]) for row in grouped_rows)
            ),
        }
        for field in _NUMERIC_FIELDS:
            if field == "phase_deg":
                mean_value, std_value = _circular_mean_and_std_deg(grouped_rows, field)
            else:
                mean_value, std_value = _mean_and_std(grouped_rows, field)
            summary[f"{field}_mean"] = mean_value
            summary[f"{field}_std"] = std_value

        drive_current = summary["drive_current_a_mean"]
        for source_field, destination in (
            ("x_v_mean", "x_over_drive_current_ohm"),
            ("y_v_mean", "y_over_drive_current_ohm"),
            ("amplitude_v_mean", "amplitude_over_drive_current_ohm"),
        ):
            value = summary[source_field]
            summary[destination] = (
                None
                if value is None or drive_current in (None, 0.0)
                else float(value) / float(drive_current)
            )

        comments = sorted(
            {str(row["comment"]) for row in grouped_rows if str(row["comment"])}
        )
        status_codes = sorted(
            {int(row["status_code"]) for row in grouped_rows if row["status_code"] is not None}
        )
        flags: set[str] = set()
        for row in grouped_rows:
            flags.update(json.loads(str(row["quality_flags_json"])))
        summary["comments"] = " | ".join(comments)
        summary["status_codes"] = json.dumps(status_codes)
        summary["quality_flags_json"] = json.dumps(sorted(flags))
        summaries.append(summary)

    summaries.sort(
        key=lambda row: (
            str(row["backend"]),
            str(row["observation_kind"]),
            int(row["observation_index"]),
            str(row["signal"]),
            int(row["harmonic"]),
        )
    )
    return tuple(summaries)
