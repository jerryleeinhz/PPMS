from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import math
from pathlib import Path
from typing import Any, Mapping

from ppms_control.models import TransportReading


class EtoDataError(ValueError):
    """Raised when a MultiVu ETO data file cannot be interpreted safely."""


@dataclass(frozen=True)
class EtoFollowCheckpoint:
    """A restart-safe cursor positioned after the last consumed complete line."""

    offset_bytes: int
    line_number: int
    records_read: int
    anchor_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "offset_bytes": self.offset_bytes,
            "line_number": self.line_number,
            "records_read": self.records_read,
            "anchor_sha256": self.anchor_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EtoFollowCheckpoint":
        try:
            checkpoint = cls(
                offset_bytes=int(value["offset_bytes"]),
                line_number=int(value["line_number"]),
                records_read=int(value["records_read"]),
                anchor_sha256=str(value["anchor_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EtoDataError("Invalid ETO follow checkpoint.") from exc
        if (
            checkpoint.offset_bytes < 0
            or checkpoint.line_number < 0
            or checkpoint.records_read < 0
            or len(checkpoint.anchor_sha256) != 64
        ):
            raise EtoDataError("Invalid ETO follow checkpoint values.")
        return checkpoint


def _optional_float(value: str | None, *, field: str, row_number: int) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise EtoDataError(f"Row {row_number}: {field} is not a number: {value!r}") from exc
    if not math.isfinite(parsed):
        raise EtoDataError(f"Row {row_number}: {field} is not finite.")
    return parsed


def _optional_int(value: str | None, *, field: str, row_number: int) -> int | None:
    parsed = _optional_float(value, field=field, row_number=row_number)
    if parsed is None:
        return None
    if not parsed.is_integer():
        raise EtoDataError(f"Row {row_number}: {field} is not an integer: {value!r}")
    return int(parsed)


@dataclass(frozen=True)
class EtoChannelReading:
    channel: int
    resistance_ohm: float | None
    phase_deg: float | None
    iv_current_a: float | None
    iv_voltage_v: float | None
    frequency_hz: float | None
    averaging_time_s: float | None
    ac_current_a: float | None
    dc_current_a: float | None
    voltage_amplitude_v: float | None
    in_phase_voltage_v: float | None
    quadrature_voltage_v: float | None
    gain: float | None
    second_harmonic_db: float | None
    third_harmonic_db: float | None

    def harmonic_amplitude_v(self, harmonic: int) -> float | None:
        """Return an unsigned amplitude derived from the ETO dB ratio.

        ETO 1.2 stores the second and third harmonics in dB relative to the
        fundamental voltage amplitude.  The conversion cannot recover a sign,
        quadrature component, or harmonic phase.
        """

        if harmonic == 2:
            ratio_db = self.second_harmonic_db
        elif harmonic == 3:
            ratio_db = self.third_harmonic_db
        else:
            raise ValueError("ETO harmonic amplitude is available only for harmonic 2 or 3.")
        if self.voltage_amplitude_v is None or ratio_db is None:
            return None
        return self.voltage_amplitude_v * 10.0 ** (ratio_db / 20.0)


@dataclass(frozen=True)
class EtoRecord:
    row_number: int
    comment: str
    timestamp_s: float
    temperature_k: float
    field_t: float
    sample_position_deg: float | None
    chamber_pressure_torr: float | None
    channel_1: EtoChannelReading | None
    channel_2: EtoChannelReading | None
    eto_status_code: int | None
    eto_measurement_mode: int | None
    temperature_status_code: int | None
    field_status_code: int | None
    chamber_status_code: int | None

    @property
    def active_channels(self) -> tuple[int, ...]:
        return tuple(
            channel
            for channel, reading in ((1, self.channel_1), (2, self.channel_2))
            if reading is not None
        )


@dataclass(frozen=True)
class EtoDataFile:
    path: Path
    header_lines: tuple[str, ...]
    columns: tuple[str, ...]
    records: tuple[EtoRecord, ...]

    def summary(self) -> dict[str, object]:
        counts = {"channel_1": 0, "channel_2": 0, "both": 0, "neither": 0}
        for record in self.records:
            active = record.active_channels
            if active == (1,):
                counts["channel_1"] += 1
            elif active == (2,):
                counts["channel_2"] += 1
            elif active == (1, 2):
                counts["both"] += 1
            else:
                counts["neither"] += 1

        def limits(values: list[float]) -> list[float] | None:
            return [min(values), max(values)] if values else None

        temperatures = [record.temperature_k for record in self.records]
        fields = [record.field_t for record in self.records]
        positions = [
            record.sample_position_deg
            for record in self.records
            if record.sample_position_deg is not None
        ]

        def channel_summary(channel: int) -> dict[str, object]:
            readings = [
                record.channel_1 if channel == 1 else record.channel_2
                for record in self.records
            ]
            active = [reading for reading in readings if reading is not None]

            def reading_limits(attribute: str) -> list[float] | None:
                values = [
                    value
                    for reading in active
                    if (value := getattr(reading, attribute)) is not None
                ]
                return limits(values)

            return {
                "records": len(active),
                "ac_current_range_a": reading_limits("ac_current_a"),
                "frequency_range_hz": reading_limits("frequency_hz"),
                "voltage_amplitude_range_v": reading_limits("voltage_amplitude_v"),
                "second_harmonic_range_db": reading_limits("second_harmonic_db"),
                "third_harmonic_range_db": reading_limits("third_harmonic_db"),
            }

        comment_counts = Counter(
            record.comment for record in self.records if record.comment
        )
        return {
            "path": str(self.path),
            "records": len(self.records),
            "columns": len(self.columns),
            "active_channel_records": counts,
            "commented_records": sum(bool(record.comment) for record in self.records),
            "comment_counts": dict(comment_counts.most_common(10)),
            "channel_1": channel_summary(1),
            "channel_2": channel_summary(2),
            "temperature_range_k": limits(temperatures),
            "field_range_t": limits(fields),
            "sample_position_range_deg": limits(positions),
            "harmonic_storage": "dB_relative_to_fundamental",
            "derived_harmonic_voltage": "unsigned_amplitude_only",
        }


_GLOBAL_COLUMNS = (
    "Time Stamp (s)",
    "Temperature (K)",
    "Field (Oe)",
    "Sample Position (deg)",
    "Chamber Pressure (Torr)",
    "ETO Status Code",
    "ETO Measurement Mode",
    "Temperature Status (code)",
    "Field Status (code)",
    "Chamber Status (code)",
)


def _channel_columns(channel: int) -> tuple[str, ...]:
    suffix = f"Ch{channel}"
    return (
        f"Resistance {suffix} (Ohms)",
        f"Phase Angle {suffix} (deg)",
        f"I-V Current {suffix} (mA)",
        f"I-V Voltage {suffix} (V)",
        f"Frequency {suffix} (Hz)",
        f"Averaging Time {suffix} (s)",
        f"AC Current {suffix} (mA)",
        f"DC Current {suffix} (mA)",
        f"Voltage Ampl {suffix} (V)",
        f"In Phase Voltage Ampl {suffix} (V)",
        f"Quadrature Voltage {suffix} (V)",
        f"Gain {suffix}",
        f"2nd Harmonic {suffix} (dB)",
        f"3rd Harmonic {suffix} (dB)",
    )


def _parse_channel(
    row: dict[str, str], channel: int, *, row_number: int
) -> EtoChannelReading | None:
    columns = _channel_columns(channel)
    if not any((row.get(column) or "").strip() for column in columns):
        return None

    def number(column: str) -> float | None:
        return _optional_float(row.get(column), field=column, row_number=row_number)

    suffix = f"Ch{channel}"
    ac_current_ma = number(f"AC Current {suffix} (mA)")
    dc_current_ma = number(f"DC Current {suffix} (mA)")
    iv_current_ma = number(f"I-V Current {suffix} (mA)")
    return EtoChannelReading(
        channel=channel,
        resistance_ohm=number(f"Resistance {suffix} (Ohms)"),
        phase_deg=number(f"Phase Angle {suffix} (deg)"),
        iv_current_a=None if iv_current_ma is None else iv_current_ma / 1000.0,
        iv_voltage_v=number(f"I-V Voltage {suffix} (V)"),
        frequency_hz=number(f"Frequency {suffix} (Hz)"),
        averaging_time_s=number(f"Averaging Time {suffix} (s)"),
        ac_current_a=None if ac_current_ma is None else ac_current_ma / 1000.0,
        dc_current_a=None if dc_current_ma is None else dc_current_ma / 1000.0,
        voltage_amplitude_v=number(f"Voltage Ampl {suffix} (V)"),
        in_phase_voltage_v=number(f"In Phase Voltage Ampl {suffix} (V)"),
        quadrature_voltage_v=number(f"Quadrature Voltage {suffix} (V)"),
        gain=number(f"Gain {suffix}"),
        second_harmonic_db=number(f"2nd Harmonic {suffix} (dB)"),
        third_harmonic_db=number(f"3rd Harmonic {suffix} (dB)"),
    )


def _validate_columns(columns: tuple[str, ...]) -> None:
    required = {"Comment", *_GLOBAL_COLUMNS, *_channel_columns(1), *_channel_columns(2)}
    missing = sorted(required.difference(columns))
    if missing:
        raise EtoDataError("ETO data file is missing required columns: " + ", ".join(missing))


def _parse_csv_row(
    line: str,
    columns: tuple[str, ...],
    *,
    row_number: int,
) -> dict[str, str]:
    try:
        values = next(csv.reader([line], strict=True))
    except csv.Error as exc:
        raise EtoDataError(f"Row {row_number}: invalid CSV data: {exc}") from exc
    if len(values) != len(columns):
        raise EtoDataError(
            f"Row {row_number}: expected {len(columns)} columns, found {len(values)}."
        )
    return dict(zip(columns, values, strict=True))


def _parse_record(row: dict[str, str], *, row_number: int) -> EtoRecord:
    timestamp_s = _optional_float(
        row.get("Time Stamp (s)"), field="Time Stamp (s)", row_number=row_number
    )
    temperature_k = _optional_float(
        row.get("Temperature (K)"), field="Temperature (K)", row_number=row_number
    )
    field_oe = _optional_float(
        row.get("Field (Oe)"), field="Field (Oe)", row_number=row_number
    )
    if timestamp_s is None or temperature_k is None or field_oe is None:
        raise EtoDataError(
            f"Row {row_number}: timestamp, temperature, and field are required."
        )
    return EtoRecord(
        row_number=row_number,
        comment=(row.get("Comment") or "").strip(),
        timestamp_s=timestamp_s,
        temperature_k=temperature_k,
        field_t=field_oe / 10_000.0,
        sample_position_deg=_optional_float(
            row.get("Sample Position (deg)"),
            field="Sample Position (deg)",
            row_number=row_number,
        ),
        chamber_pressure_torr=_optional_float(
            row.get("Chamber Pressure (Torr)"),
            field="Chamber Pressure (Torr)",
            row_number=row_number,
        ),
        channel_1=_parse_channel(row, 1, row_number=row_number),
        channel_2=_parse_channel(row, 2, row_number=row_number),
        eto_status_code=_optional_int(
            row.get("ETO Status Code"), field="ETO Status Code", row_number=row_number
        ),
        eto_measurement_mode=_optional_int(
            row.get("ETO Measurement Mode"),
            field="ETO Measurement Mode",
            row_number=row_number,
        ),
        temperature_status_code=_optional_int(
            row.get("Temperature Status (code)"),
            field="Temperature Status (code)",
            row_number=row_number,
        ),
        field_status_code=_optional_int(
            row.get("Field Status (code)"),
            field="Field Status (code)",
            row_number=row_number,
        ),
        chamber_status_code=_optional_int(
            row.get("Chamber Status (code)"),
            field="Chamber Status (code)",
            row_number=row_number,
        ),
    )


_FOLLOW_ANCHOR_BYTES = 4096


def _anchor_for_prefix(path: Path, offset_bytes: int) -> str:
    start = max(0, offset_bytes - _FOLLOW_ANCHOR_BYTES)
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(offset_bytes - start)
    except OSError as exc:
        raise EtoDataError(f"Cannot read ETO data file {path}: {exc}") from exc
    return sha256(payload).hexdigest()


class EtoDataFollower:
    """Incrementally read complete rows from an ETO file that is still growing."""

    def __init__(
        self,
        path: str | Path,
        *,
        checkpoint: EtoFollowCheckpoint | None = None,
    ) -> None:
        self.path = Path(path).resolve()
        self.header_lines: tuple[str, ...] = ()
        self.columns: tuple[str, ...] = ()
        self._saw_data_marker = False
        self._offset_bytes = 0
        self._line_number = 0
        self._records_read = 0
        self._anchor_sha256 = sha256(b"").hexdigest()
        if checkpoint is not None:
            self._restore(checkpoint)

    @property
    def checkpoint(self) -> EtoFollowCheckpoint:
        return EtoFollowCheckpoint(
            offset_bytes=self._offset_bytes,
            line_number=self._line_number,
            records_read=self._records_read,
            anchor_sha256=self._anchor_sha256,
        )

    def _restore(self, checkpoint: EtoFollowCheckpoint) -> None:
        try:
            file_size = self.path.stat().st_size
        except OSError as exc:
            raise EtoDataError(f"Cannot inspect ETO data file {self.path}: {exc}") from exc
        if file_size < checkpoint.offset_bytes:
            raise EtoDataError("ETO data file is shorter than the saved checkpoint.")
        if _anchor_for_prefix(self.path, checkpoint.offset_bytes) != checkpoint.anchor_sha256:
            raise EtoDataError("ETO data before the saved checkpoint has changed.")
        try:
            with self.path.open("rb") as handle:
                prefix = handle.read(checkpoint.offset_bytes)
            lines = prefix.decode("utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise EtoDataError(f"Cannot restore ETO follow state for {self.path}: {exc}") from exc
        if len(lines) != checkpoint.line_number:
            raise EtoDataError("ETO checkpoint line count does not match the source file.")
        try:
            data_index = lines.index("[Data]")
        except ValueError:
            self.header_lines = tuple(lines)
        else:
            self.header_lines = tuple(lines[:data_index])
            self._saw_data_marker = True
            if data_index + 1 < len(lines):
                try:
                    self.columns = tuple(
                        next(csv.reader([lines[data_index + 1]], strict=True))
                    )
                except csv.Error as exc:
                    raise EtoDataError(f"Invalid ETO column header: {exc}") from exc
                _validate_columns(self.columns)
        self._offset_bytes = checkpoint.offset_bytes
        self._line_number = checkpoint.line_number
        self._records_read = checkpoint.records_read
        self._anchor_sha256 = checkpoint.anchor_sha256

    def _validate_unchanged_prefix(self) -> int:
        try:
            file_size = self.path.stat().st_size
        except OSError as exc:
            raise EtoDataError(f"Cannot inspect ETO data file {self.path}: {exc}") from exc
        if file_size < self._offset_bytes:
            raise EtoDataError("ETO data file was truncated while being followed.")
        if _anchor_for_prefix(self.path, self._offset_bytes) != self._anchor_sha256:
            raise EtoDataError("ETO data already consumed by the follower has changed.")
        return file_size

    def poll(self, *, final: bool = False) -> EtoDataFile:
        """Return only newly completed records since the previous poll.

        A trailing partial line is left unread until a later poll.  Set
        ``final=True`` only after the writer has stopped to consume a final row
        that does not end with a newline.
        """

        file_size = self._validate_unchanged_prefix()
        if file_size == self._offset_bytes:
            return EtoDataFile(self.path, self.header_lines, self.columns, ())
        try:
            with self.path.open("rb") as handle:
                handle.seek(self._offset_bytes)
                available = handle.read()
        except OSError as exc:
            raise EtoDataError(f"Cannot read ETO data file {self.path}: {exc}") from exc

        if final:
            complete_size = len(available)
        else:
            last_newline = available.rfind(b"\n")
            complete_size = 0 if last_newline < 0 else last_newline + 1
        if complete_size == 0:
            return EtoDataFile(self.path, self.header_lines, self.columns, ())

        payload = available[:complete_size]
        encoding = "utf-8-sig" if self._offset_bytes == 0 else "utf-8"
        try:
            lines = payload.decode(encoding).splitlines()
        except UnicodeDecodeError as exc:
            raise EtoDataError(f"ETO data contains invalid UTF-8: {exc}") from exc

        records: list[EtoRecord] = []
        mutable_header = list(self.header_lines)
        columns = self.columns
        saw_data_marker = self._saw_data_marker
        line_number = self._line_number
        for line in lines:
            line_number += 1
            if not columns:
                if saw_data_marker:
                    try:
                        columns = tuple(next(csv.reader([line], strict=True)))
                    except csv.Error as exc:
                        raise EtoDataError(f"Invalid ETO column header: {exc}") from exc
                    _validate_columns(columns)
                elif line == "[Data]":
                    saw_data_marker = True
                else:
                    mutable_header.append(line)
                continue
            if not line.strip():
                continue
            row = _parse_csv_row(line, columns, row_number=line_number)
            records.append(_parse_record(row, row_number=line_number))

        self.header_lines = tuple(mutable_header)
        self.columns = columns
        self._saw_data_marker = saw_data_marker
        self._line_number = line_number
        self._offset_bytes += complete_size
        self._records_read += len(records)
        self._anchor_sha256 = _anchor_for_prefix(self.path, self._offset_bytes)
        return EtoDataFile(
            path=self.path,
            header_lines=self.header_lines,
            columns=self.columns,
            records=tuple(records),
        )


def load_eto_data(path: str | Path) -> EtoDataFile:
    source = Path(path).resolve()
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise EtoDataError(f"Cannot read ETO data file {source}: {exc}") from exc

    try:
        data_index = lines.index("[Data]")
    except ValueError as exc:
        raise EtoDataError(f"{source} does not contain a [Data] section.") from exc
    if data_index + 1 >= len(lines):
        raise EtoDataError(f"{source} has no column header after [Data].")

    try:
        columns = tuple(next(csv.reader([lines[data_index + 1]], strict=True)))
    except csv.Error as exc:
        raise EtoDataError(f"Invalid ETO column header: {exc}") from exc
    _validate_columns(columns)

    records: list[EtoRecord] = []
    for row_number, line in enumerate(lines[data_index + 2 :], start=data_index + 3):
        if not line.strip():
            continue
        row = _parse_csv_row(line, columns, row_number=row_number)
        records.append(_parse_record(row, row_number=row_number))

    return EtoDataFile(
        path=source,
        header_lines=tuple(lines[:data_index]),
        columns=columns,
        records=tuple(records),
    )


def eto_transport_readings(
    data: EtoDataFile,
    channel_roles: Mapping[int, str],
) -> tuple[TransportReading, ...]:
    """Normalize ETO rows without pairing channels or inventing harmonic phase."""

    invalid_channels = sorted(set(channel_roles).difference({1, 2}))
    invalid_roles = sorted(set(channel_roles.values()).difference({"xx", "xy"}))
    if invalid_channels:
        raise EtoDataError(f"Unsupported ETO channel mappings: {invalid_channels}")
    if invalid_roles:
        raise EtoDataError(f"Unsupported ETO signal roles: {invalid_roles}")

    readings: list[TransportReading] = []
    for record in data.records:
        for channel_number, channel in ((1, record.channel_1), (2, record.channel_2)):
            if channel is None:
                continue
            try:
                role = channel_roles[channel_number]
            except KeyError as exc:
                raise EtoDataError(
                    f"ETO row {record.row_number} contains channel {channel_number}, "
                    "but no signal role was configured for it."
                ) from exc

            common = {
                "backend": "eto",
                "signal": role,
                "instrument_channel": f"eto_ch{channel_number}",
                "timestamp_s": record.timestamp_s,
                "temperature_k": record.temperature_k,
                "field_t": record.field_t,
                "sample_position_deg": record.sample_position_deg,
                "drive_current_a": channel.ac_current_a,
                "frequency_hz": channel.frequency_hz,
                "source_row": record.row_number,
                "comment": record.comment,
                "status_code": record.eto_status_code,
            }
            readings.append(
                TransportReading(
                    harmonic=1,
                    x_v=channel.in_phase_voltage_v,
                    y_v=channel.quadrature_voltage_v,
                    amplitude_v=channel.voltage_amplitude_v,
                    phase_deg=channel.phase_deg,
                    ratio_db=None,
                    phase_resolved=(
                        channel.in_phase_voltage_v is not None
                        and channel.quadrature_voltage_v is not None
                    ),
                    **common,
                )
            )
            for harmonic, ratio_db in (
                (2, channel.second_harmonic_db),
                (3, channel.third_harmonic_db),
            ):
                if ratio_db is None:
                    continue
                readings.append(
                    TransportReading(
                        harmonic=harmonic,
                        x_v=None,
                        y_v=None,
                        amplitude_v=channel.harmonic_amplitude_v(harmonic),
                        phase_deg=None,
                        ratio_db=ratio_db,
                        phase_resolved=False,
                        **common,
                    )
                )
    return tuple(readings)
