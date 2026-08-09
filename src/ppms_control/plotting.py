from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import tomllib
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from ppms_control.analysis import summarize_transport_rows
from ppms_control.eto_data import eto_transport_readings, load_eto_data


class PlotDataError(ValueError):
    """Raised when analysis input is missing, ambiguous, or unsupported."""


@dataclass(frozen=True)
class PlotRecord:
    source: str
    observation_index: int
    backend: str
    signal: str
    instrument_channel: str
    harmonic: int
    timestamp_s: float
    temperature_k: float
    field_t: float
    gate_top_voltage_v: float | None
    gate_bottom_voltage_v: float | None
    sample_position_deg: float | None
    drive_current_a: float | None
    frequency_hz: float | None
    x_v: float | None
    y_v: float | None
    amplitude_v: float | None
    phase_deg: float | None
    ratio_db: float | None
    phase_resolved: bool
    comment: str = ""
    quality_flags: tuple[str, ...] = ()

    @property
    def voltage_v(self) -> float | None:
        """Prefer signed in-phase voltage and fall back to unsigned amplitude."""

        return self.x_v if self.x_v is not None else self.amplitude_v

    @property
    def resistance_ohm(self) -> float | None:
        if self.voltage_v is None or self.drive_current_a in (None, 0.0):
            return None
        return self.voltage_v / self.drive_current_a


@dataclass(frozen=True)
class LeakageRecord:
    source: str
    sequence_index: int
    sample_index: int
    gate_top_voltage_v: float
    gate_bottom_voltage_v: float
    top_current_a: float | None
    bottom_current_a: float | None


@dataclass(frozen=True)
class PlotDataset:
    records: tuple[PlotRecord, ...]
    leakage: tuple[LeakageRecord, ...]
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class GateCalibration:
    top_capacitance_f_per_m2: float
    bottom_capacitance_f_per_m2: float
    top_offset_v: float = 0.0
    bottom_offset_v: float = 0.0
    density_offset_per_m2: float = 0.0
    displacement_offset_c_per_m2: float = 0.0

    def convert(self, top_v: float, bottom_v: float) -> tuple[float, float]:
        elementary_charge_c = 1.602176634e-19
        top_charge = self.top_capacitance_f_per_m2 * (top_v - self.top_offset_v)
        bottom_charge = self.bottom_capacitance_f_per_m2 * (
            bottom_v - self.bottom_offset_v
        )
        density = (top_charge + bottom_charge) / elementary_charge_c
        density += self.density_offset_per_m2
        displacement = (bottom_charge - top_charge) / 2.0
        displacement += self.displacement_offset_c_per_m2
        return density, displacement


_CALIBRATION_KEYS = {
    "top_capacitance_f_per_m2",
    "bottom_capacitance_f_per_m2",
    "top_offset_v",
    "bottom_offset_v",
    "density_offset_per_m2",
    "displacement_offset_c_per_m2",
}


def load_gate_calibration(path: str | Path) -> GateCalibration:
    source = Path(path).resolve()
    try:
        root = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PlotDataError(f"Cannot read gate calibration {source}: {exc}") from exc
    section = root.get("gate_calibration")
    if not isinstance(section, dict):
        raise PlotDataError("Gate calibration requires a [gate_calibration] section.")
    actual = set(section)
    required = {"top_capacitance_f_per_m2", "bottom_capacitance_f_per_m2"}
    missing = required - actual
    unknown = actual - _CALIBRATION_KEYS
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise PlotDataError("Invalid gate calibration: " + "; ".join(details))

    values: dict[str, float] = {}
    for key in _CALIBRATION_KEYS:
        raw = section.get(key, 0.0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PlotDataError(f"gate_calibration.{key} must be numeric.")
        value = float(raw)
        if not math.isfinite(value):
            raise PlotDataError(f"gate_calibration.{key} must be finite.")
        values[key] = value
    for key in required:
        if values[key] <= 0:
            raise PlotDataError(f"gate_calibration.{key} must be positive.")
    return GateCalibration(**values)


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise PlotDataError(f"SQLite database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_sqlite_run(database: str | Path, run_id: str) -> PlotDataset:
    source = Path(database).resolve()
    with _readonly_connection(source) as connection:
        run = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise PlotDataError(f"Unknown run_id: {run_id}")
        rows = connection.execute(
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
        samples = connection.execute(
            """
            SELECT sequence_index, sample_index,
                   gate_top_voltage_v, gate_bottom_voltage_v,
                   gate_top_measured_current_a, gate_top_current_available,
                   gate_bottom_measured_current_a, gate_bottom_current_available
            FROM instrument_samples
            WHERE run_id = ?
            ORDER BY sequence_index, attempt_index, sample_index
            """,
            (run_id,),
        ).fetchall()

    records = tuple(
        PlotRecord(
            source=run_id,
            observation_index=int(summary["observation_index"]),
            backend=str(summary["backend"]),
            signal=str(summary["signal"]),
            instrument_channel=str(summary["instrument_channel"]),
            harmonic=int(summary["harmonic"]),
            timestamp_s=float(summary["timestamp_s_mean"]),
            temperature_k=float(summary["temperature_k_mean"]),
            field_t=float(summary["field_t_mean"]),
            gate_top_voltage_v=_optional_float(summary["gate_top_voltage_v_mean"]),
            gate_bottom_voltage_v=_optional_float(summary["gate_bottom_voltage_v_mean"]),
            sample_position_deg=_optional_float(summary["sample_position_deg_mean"]),
            drive_current_a=_optional_float(summary["drive_current_a_mean"]),
            frequency_hz=_optional_float(summary["frequency_hz_mean"]),
            x_v=_optional_float(summary["x_v_mean"]),
            y_v=_optional_float(summary["y_v_mean"]),
            amplitude_v=_optional_float(summary["amplitude_v_mean"]),
            phase_deg=_optional_float(summary["phase_deg_mean"]),
            ratio_db=_optional_float(summary["ratio_db_mean"]),
            phase_resolved=bool(summary["phase_resolved"]),
            comment=str(summary["comments"]),
            quality_flags=tuple(json.loads(str(summary["quality_flags_json"]))),
        )
        for summary in summaries
    )
    leakage = tuple(
        LeakageRecord(
            source=run_id,
            sequence_index=int(row["sequence_index"]),
            sample_index=int(row["sample_index"]),
            gate_top_voltage_v=float(row["gate_top_voltage_v"]),
            gate_bottom_voltage_v=float(row["gate_bottom_voltage_v"]),
            top_current_a=(
                float(row["gate_top_measured_current_a"])
                if bool(row["gate_top_current_available"])
                else None
            ),
            bottom_current_a=(
                float(row["gate_bottom_measured_current_a"])
                if bool(row["gate_bottom_current_available"])
                else None
            ),
        )
        for row in samples
    )
    if not records:
        raise PlotDataError(f"Run {run_id} contains no transport readings.")
    metadata = {
        "input_kind": "sqlite_run",
        "database": str(source),
        "run_id": run_id,
        "protocol": str(run["protocol"]),
        "sample_name": str(run["sample_name"]),
        "run_status": str(run["status"]),
    }
    return PlotDataset(records=records, leakage=leakage, metadata=metadata)


def load_eto_path(
    source: str | Path,
    channel_roles: Mapping[int, str],
) -> PlotDataset:
    root = Path(source).resolve()
    if root.is_file():
        files = (root,)
        relative_root = root.parent
    elif root.is_dir():
        files = tuple(sorted(root.rglob("*.dat")))
        relative_root = root
    else:
        raise PlotDataError(f"ETO input does not exist: {root}")
    if not files:
        raise PlotDataError(f"No .dat files found under {root}")

    records: list[PlotRecord] = []
    for path in files:
        data = load_eto_data(path)
        label = str(path.relative_to(relative_root)).replace("\\", "/")
        for reading in eto_transport_readings(data, channel_roles):
            flags: list[str] = list(reading.quality_flags)
            if "compliance" in reading.comment.lower():
                flags.append("eto_compliance_comment")
            records.append(
                PlotRecord(
                    source=label,
                    observation_index=int(reading.source_row or len(records)),
                    backend=reading.backend,
                    signal=reading.signal,
                    instrument_channel=reading.instrument_channel,
                    harmonic=reading.harmonic,
                    timestamp_s=reading.timestamp_s,
                    temperature_k=reading.temperature_k,
                    field_t=reading.field_t,
                    gate_top_voltage_v=reading.gate_top_voltage_v,
                    gate_bottom_voltage_v=reading.gate_bottom_voltage_v,
                    sample_position_deg=reading.sample_position_deg,
                    drive_current_a=reading.drive_current_a,
                    frequency_hz=reading.frequency_hz,
                    x_v=reading.x_v,
                    y_v=reading.y_v,
                    amplitude_v=reading.amplitude_v,
                    phase_deg=reading.phase_deg,
                    ratio_db=reading.ratio_db,
                    phase_resolved=reading.phase_resolved,
                    comment=reading.comment,
                    quality_flags=tuple(sorted(set(flags))),
                )
            )
    if not records:
        raise PlotDataError(f"ETO input {root} contains no active channel readings.")
    return PlotDataset(
        records=tuple(records),
        leakage=(),
        metadata={
            "input_kind": "eto_path",
            "source": str(root),
            "files": [str(path) for path in files],
            "channel_roles": {str(key): value for key, value in channel_roles.items()},
        },
    )


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def _condition_key(
    record: PlotRecord,
    varying: str,
) -> tuple[tuple[str, object], ...]:
    values: dict[str, object] = {
        "source": record.source,
        "current": _rounded(record.drive_current_a, 9),
        "frequency": _rounded(record.frequency_hz, 3),
        "temperature": round(record.temperature_k, 1),
        "field": round(record.field_t, 5),
        "angle": _rounded(record.sample_position_deg, 2),
        "top_gate": _rounded(record.gate_top_voltage_v, 5),
        "bottom_gate": _rounded(record.gate_bottom_voltage_v, 5),
    }
    values.pop(varying, None)
    return tuple((key, values[key]) for key in sorted(values))


def _group_sweeps(
    records: Iterable[PlotRecord],
    varying: str,
) -> dict[tuple[tuple[str, object], ...], list[PlotRecord]]:
    groups: dict[tuple[tuple[str, object], ...], list[PlotRecord]] = defaultdict(list)
    for record in records:
        groups[_condition_key(record, varying)].append(record)
    return groups


def _median_series(
    records: Iterable[PlotRecord],
    x_value: Callable[[PlotRecord], float | None],
    y_value: Callable[[PlotRecord], float | None],
    *,
    x_digits: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    bins: dict[float, list[float]] = defaultdict(list)
    for record in records:
        x = x_value(record)
        y = y_value(record)
        if x is None or y is None or not (math.isfinite(x) and math.isfinite(y)):
            continue
        bins[round(float(x), x_digits)].append(float(y))
    items = sorted((x, float(np.median(values))) for x, values in bins.items())
    return (
        np.asarray([item[0] for item in items], dtype=float),
        np.asarray([item[1] for item in items], dtype=float),
    )


def _has_range(values: Iterable[float | None], minimum_unique: int = 2) -> bool:
    finite = {round(float(value), 10) for value in values if value is not None}
    return len(finite) >= minimum_unique


def _has_span(
    values: Iterable[float | None],
    minimum_span: float,
    minimum_unique: int = 2,
) -> bool:
    finite = sorted(
        {round(float(value), 10) for value in values if value is not None}
    )
    return len(finite) >= minimum_unique and finite[-1] - finite[0] >= minimum_span


def _short_label(key: tuple[tuple[str, object], ...]) -> str:
    values = dict(key)
    source = Path(str(values.pop("source", "series"))).stem[:18]
    parts = [source]
    labels = {
        "current": ("I", 1e3, "mA"),
        "frequency": ("f", 1.0, "Hz"),
        "temperature": ("T", 1.0, "K"),
        "field": ("B", 1.0, "T"),
        "angle": ("angle", 1.0, "deg"),
        "top_gate": ("Vt", 1.0, "V"),
        "bottom_gate": ("Vb", 1.0, "V"),
    }
    for name, value in values.items():
        if value is None or name not in labels:
            continue
        label, scale, unit = labels[name]
        parts.append(f"{label}={float(value) * scale:g}{unit}")
    return ", ".join(parts[:4])


def _grid(
    triples: Iterable[tuple[float, float, float]],
) -> tuple[np.ndarray, np.ndarray, np.ma.MaskedArray] | None:
    collected: dict[tuple[float, float], list[float]] = defaultdict(list)
    for x, y, z in triples:
        if all(math.isfinite(value) for value in (x, y, z)):
            collected[(round(x, 10), round(y, 10))].append(z)
    if not collected:
        return None
    xs = np.asarray(sorted({key[0] for key in collected}), dtype=float)
    ys = np.asarray(sorted({key[1] for key in collected}), dtype=float)
    if len(xs) < 2 or len(ys) < 2:
        return None
    z_grid = np.full((len(ys), len(xs)), np.nan, dtype=float)
    x_index = {value: index for index, value in enumerate(xs)}
    y_index = {value: index for index, value in enumerate(ys)}
    for (x, y), values in collected.items():
        z_grid[y_index[y], x_index[x]] = float(np.median(values))
    return xs, ys, np.ma.masked_invalid(z_grid)


def _write_records_csv(dataset: PlotDataset, destination: Path) -> Path:
    fields = list(asdict(dataset.records[0]))
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in dataset.records:
            row = asdict(record)
            row["quality_flags"] = json.dumps(record.quality_flags)
            writer.writerow(row)
    return destination


class _PlotSuite:
    def __init__(
        self,
        dataset: PlotDataset,
        output_dir: Path,
        formats: Sequence[str],
        calibration: GateCalibration | None,
    ) -> None:
        self.dataset = dataset
        self.output_dir = output_dir
        self.formats = tuple(formats)
        self.calibration = calibration
        self.entries: list[dict[str, object]] = []
        self.fit_rows: list[dict[str, object]] = []

    def generated(
        self,
        key: str,
        figure: object,
        description: str,
        source_fields: Sequence[str],
    ) -> None:
        if any(
            "compliance" in record.comment.lower()
            or any("compliance" in flag.lower() for flag in record.quality_flags)
            for record in self.dataset.records
        ):
            figure.text(
                0.99,
                0.005,
                "WARNING: input contains compliance flags",
                ha="right",
                va="bottom",
                fontsize=8,
                color="crimson",
            )
        files: list[str] = []
        for extension in self.formats:
            path = self.output_dir / f"{key}.{extension}"
            figure.savefig(path, dpi=300, bbox_inches="tight")
            files.append(str(path))
        self.entries.append(
            {
                "key": key,
                "status": "generated",
                "description": description,
                "source_fields": list(source_fields),
                "files": files,
            }
        )

    def skipped(
        self,
        key: str,
        description: str,
        reason: str,
        source_fields: Sequence[str],
    ) -> None:
        self.entries.append(
            {
                "key": key,
                "status": "skipped",
                "description": description,
                "source_fields": list(source_fields),
                "reason": reason,
                "files": [],
            }
        )

    def run(self) -> dict[str, object]:
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt

        plt.style.use("seaborn-v0_8-whitegrid")
        self._plot_current_response(plt)
        self._plot_harmonic_ratio_db(plt)
        self._plot_harmonic_scaling(plt)
        self._plot_current_nonlinearity(plt)
        self._plot_frequency_response(plt)
        self._plot_frequency_normalized(plt)
        self._plot_temperature_dependence(plt)
        self._plot_field_dependence(plt)
        self._plot_angle_dependence(plt)
        self._plot_gamma(plt)
        self._plot_temperature_field_map(plt)
        self._plot_transport_phase_overview(plt)
        self._plot_gate_resistance_map(plt)
        self._plot_gate_leakage(plt)
        self._plot_paired_gate_linecut(plt)
        self._plot_n_d_map(plt)
        self._record_unavailable_paper_panels()
        plt.close("all")

        records_csv = _write_records_csv(
            self.dataset,
            self.output_dir / "analysis_records.csv",
        )
        fit_csv: str | None = None
        if self.fit_rows:
            fit_path = self.output_dir / "fit_summary.csv"
            fieldnames = sorted({key for row in self.fit_rows for key in row})
            with fit_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.fit_rows)
            fit_csv = str(fit_path)

        manifest = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "input": dict(self.dataset.metadata),
            "record_count": len(self.dataset.records),
            "leakage_record_count": len(self.dataset.leakage),
            "analysis_records_csv": str(records_csv),
            "fit_summary_csv": fit_csv,
            "gate_calibration": asdict(self.calibration) if self.calibration else None,
            "figures": self.entries,
            "limitations": [
                "Signed X is used when available; ETO 2w/3w remain unsigned amplitudes.",
                "Plots show measured transport and do not by themselves establish a mechanism.",
                "Nernst, scattering-rate, and Hall-coefficient panels require independent inputs.",
            ],
            "quality_warning": (
                "Input contains compliance-related comments or quality flags."
                if any(
                    "compliance" in record.comment.lower()
                    or any("compliance" in flag.lower() for flag in record.quality_flags)
                    for record in self.dataset.records
                )
                else None
            ),
        }
        manifest_path = self.output_dir / "analysis_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        manifest["manifest"] = str(manifest_path)
        return manifest

    def _plot_current_response(self, plt: object) -> None:
        records = [record for record in self.dataset.records if record.drive_current_a is not None]
        if not _has_range((abs(record.drive_current_a or 0.0) for record in records)):
            self.skipped(
                "current_response",
                "Fundamental and harmonic voltage versus AC current.",
                "Fewer than two drive-current amplitudes are present.",
                ("drive_current_a", "x_v", "amplitude_v", "harmonic"),
            )
            return
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
        for column, signal in enumerate(("xy", "xx")):
            signal_records = [record for record in records if record.signal == signal]
            for row, harmonics in enumerate(((1,), (2, 3))):
                ax = axes[row, column]
                plotted = 0
                for harmonic in harmonics:
                    harmonic_records = [
                        record for record in signal_records if record.harmonic == harmonic
                    ]
                    groups = _group_sweeps(harmonic_records, "current")
                    eligible = sorted(
                        groups.items(),
                        key=lambda item: len(item[1]),
                        reverse=True,
                    )
                    for key, group in eligible[:8]:
                        x, y = _median_series(
                            group,
                            lambda record: abs(record.drive_current_a or 0.0) * 1e3,
                            lambda record: (
                                record.voltage_v * 1e6
                                if record.voltage_v is not None
                                else None
                            ),
                        )
                        if len(x) < 2:
                            continue
                        style = "o-" if harmonic != 3 else "s--"
                        ax.plot(x, y, style, ms=3, label=f"{harmonic}w {_short_label(key)}")
                        plotted += 1
                ax.set_xlabel("AC current amplitude (mA)")
                ax.set_ylabel("Voltage (uV)")
                ax.set_title(f"{signal}: {'fundamental' if row == 0 else 'harmonics'}")
                if plotted:
                    ax.legend(fontsize=7, ncol=2)
        fig.suptitle("Current response (signed X where available, otherwise amplitude)")
        fig.tight_layout()
        self.generated(
            "current_response",
            fig,
            "Paper Fig. 2(h,i) and CrSBr current/harmonic overview.",
            ("drive_current_a", "signal", "harmonic", "x_v", "amplitude_v"),
        )

    def _plot_harmonic_scaling(self, plt: object) -> None:
        candidates = [
            record
            for record in self.dataset.records
            if record.harmonic in (2, 3)
            and record.drive_current_a not in (None, 0.0)
            and record.voltage_v is not None
        ]
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
        fit_count = 0
        for column, signal in enumerate(("xy", "xx")):
            for row, harmonic in enumerate((2, 3)):
                ax = axes[row, column]
                groups = _group_sweeps(
                    [r for r in candidates if r.signal == signal and r.harmonic == harmonic],
                    "current",
                )
                eligible = sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)
                for key, group in eligible[:6]:
                    x, y = _median_series(
                        group,
                        lambda record, order=harmonic: (abs(record.drive_current_a or 0.0) * 1e3)
                        ** order,
                        lambda record: abs(record.voltage_v or 0.0) * 1e6,
                    )
                    if len(x) < 3 or np.ptp(x) == 0:
                        continue
                    slope, intercept = np.polyfit(x, y, 1)
                    fitted = slope * x + intercept
                    ss_total = float(np.sum((y - np.mean(y)) ** 2))
                    r_squared = (
                        float(1.0 - np.sum((y - fitted) ** 2) / ss_total)
                        if ss_total > 0
                        else math.nan
                    )
                    ax.plot(x, y, "o", ms=4, label=f"{_short_label(key)} R2={r_squared:.3f}")
                    ax.plot(x, fitted, "--", lw=1.2)
                    self.fit_rows.append(
                        {
                            "analysis": "harmonic_scaling",
                            "signal": signal,
                            "harmonic": harmonic,
                            "series": _short_label(key),
                            "points": len(x),
                            "slope_uV_per_mA_power": float(slope),
                            "intercept_uV": float(intercept),
                            "r_squared": r_squared,
                        }
                    )
                    fit_count += 1
                ax.set_xlabel(f"|I|^{harmonic} (mA^{harmonic})")
                ax.set_ylabel(f"|V{harmonic}w| (uV)")
                ax.set_title(f"{signal} {harmonic}w scaling")
                if ax.lines:
                    ax.legend(fontsize=7)
        if not fit_count:
            plt.close(fig)
            self.skipped(
                "harmonic_scaling",
                "Linear tests of |V2w| versus I^2 and |V3w| versus I^3.",
                "No constant-condition series contains at least three current amplitudes.",
                ("drive_current_a", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig.tight_layout()
        self.generated(
            "harmonic_scaling",
            fig,
            "CrSBr harmonic scaling with explicit linear-fit coefficients.",
            ("drive_current_a", "signal", "harmonic", "x_v", "amplitude_v"),
        )

    def _plot_harmonic_ratio_db(self, plt: object) -> None:
        records = [
            record
            for record in self.dataset.records
            if record.harmonic in (2, 3)
            and record.ratio_db is not None
            and record.drive_current_a is not None
        ]
        if not records or not _has_range(
            (abs(record.drive_current_a or 0.0) for record in records)
        ):
            self.skipped(
                "harmonic_ratio_db",
                "ETO second- and third-harmonic ratios relative to the fundamental.",
                "No ETO dB-ratio current series is available.",
                ("drive_current_a", "ratio_db", "harmonic"),
            )
            return
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), squeeze=False)
        plotted = 0
        for column, signal in enumerate(("xy", "xx")):
            for harmonic in (2, 3):
                groups = _group_sweeps(
                    [r for r in records if r.signal == signal and r.harmonic == harmonic],
                    "current",
                )
                for key, group in sorted(
                    groups.items(), key=lambda item: len(item[1]), reverse=True
                )[:8]:
                    x, y = _median_series(
                        group,
                        lambda record: abs(record.drive_current_a or 0.0) * 1e3,
                        lambda record: record.ratio_db,
                    )
                    if len(x) < 2:
                        continue
                    style = "o-" if harmonic == 2 else "s--"
                    axes[0, column].plot(
                        x,
                        y,
                        style,
                        ms=3,
                        label=f"{harmonic}w {_short_label(key)}",
                    )
                    plotted += 1
            axes[0, column].set(
                xlabel="AC current amplitude (mA)",
                ylabel="Harmonic / fundamental (dB)",
                title=signal,
            )
            if axes[0, column].lines:
                axes[0, column].legend(fontsize=7, ncol=2)
        if not plotted:
            plt.close(fig)
            self.skipped(
                "harmonic_ratio_db",
                "ETO second- and third-harmonic ratios relative to the fundamental.",
                "No constant-condition dB-ratio series has at least two current points.",
                ("drive_current_a", "ratio_db", "harmonic"),
            )
            return
        fig.tight_layout()
        self.generated(
            "harmonic_ratio_db",
            fig,
            "CrSBr ETO harmonic ratios as stored by MultiVu.",
            ("drive_current_a", "signal", "harmonic", "ratio_db"),
        )

    def _plot_current_nonlinearity(self, plt: object) -> None:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), squeeze=False)
        fitted = 0
        for column, signal in enumerate(("xy", "xx")):
            groups = _group_sweeps(
                [
                    record
                    for record in self.dataset.records
                    if record.signal == signal
                    and record.harmonic == 1
                    and record.drive_current_a is not None
                    and record.voltage_v is not None
                ],
                "current",
            )
            for key, group in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True):
                x, y = _median_series(
                    group,
                    lambda record: abs(record.drive_current_a or 0.0) * 1e3,
                    lambda record: (record.voltage_v or 0.0) * 1e6,
                )
                if len(x) < 4 or np.ptp(x) == 0:
                    continue
                slope, intercept = np.polyfit(x, y, 1)
                residual = y - (slope * x + intercept)
                axes[0, column].plot(x, residual, "o-", ms=4, label=_short_label(key))
                self.fit_rows.append(
                    {
                        "analysis": "fundamental_linear_baseline",
                        "signal": signal,
                        "series": _short_label(key),
                        "points": len(x),
                        "slope_uV_per_mA": float(slope),
                        "intercept_uV": float(intercept),
                    }
                )
                fitted += 1
                break
            axes[0, column].axhline(0.0, color="black", lw=0.8)
            axes[0, column].set(
                xlabel="AC current amplitude (mA)",
                ylabel="V1w - linear fit (uV)",
                title=signal,
            )
            if axes[0, column].get_legend_handles_labels()[1]:
                axes[0, column].legend(fontsize=8)
        if not fitted:
            plt.close(fig)
            self.skipped(
                "current_nonlinearity",
                "Residual fundamental voltage after a linear current fit.",
                "No fundamental series contains at least four current amplitudes.",
                ("drive_current_a", "x_v", "amplitude_v"),
            )
            return
        fig.tight_layout()
        self.generated(
            "current_nonlinearity",
            fig,
            "CrSBr quick nonlinearity diagnostic after subtracting the linear baseline.",
            ("drive_current_a", "signal", "x_v", "amplitude_v"),
        )

    def _plot_frequency_response(self, plt: object) -> None:
        records = [record for record in self.dataset.records if record.frequency_hz is not None]
        if not _has_range((record.frequency_hz for record in records)):
            self.skipped(
                "frequency_response",
                "Voltage amplitude and phase versus frequency.",
                "Fewer than two frequencies are present.",
                ("frequency_hz", "amplitude_v", "phase_deg"),
            )
            return
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
        plotted = 0
        for column, signal in enumerate(("xy", "xx")):
            for harmonic in (1, 2, 3):
                groups = _group_sweeps(
                    [r for r in records if r.signal == signal and r.harmonic == harmonic],
                    "frequency",
                )
                for key, group in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:6]:
                    x, y = _median_series(
                        group,
                        lambda record: record.frequency_hz,
                        lambda record: (
                            abs(record.amplitude_v) * 1e6
                            if record.amplitude_v is not None
                            else (
                                abs(record.voltage_v) * 1e6
                                if record.voltage_v is not None
                                else None
                            )
                        ),
                    )
                    if len(x) < 2 or np.any(x <= 0):
                        continue
                    axes[0, column].loglog(x, y, "o-", ms=3, label=f"{harmonic}w {_short_label(key)}")
                    plotted += 1
                    if harmonic == 1:
                        px, py = _median_series(
                            group,
                            lambda record: record.frequency_hz,
                            lambda record: record.phase_deg,
                        )
                        if len(px) >= 2:
                            axes[1, column].semilogx(px, py, "o-", ms=3, label=_short_label(key))
            axes[0, column].set(xlabel="Frequency (Hz)", ylabel="Voltage amplitude (uV)", title=signal)
            axes[1, column].set(xlabel="Frequency (Hz)", ylabel="Fundamental phase (deg)")
            if axes[0, column].lines:
                axes[0, column].legend(fontsize=7)
            if axes[1, column].lines:
                axes[1, column].legend(fontsize=7)
        if not plotted:
            plt.close(fig)
            self.skipped(
                "frequency_response",
                "Voltage amplitude and phase versus frequency.",
                "No constant-condition frequency series has at least two points.",
                ("frequency_hz", "amplitude_v", "phase_deg"),
            )
            return
        fig.tight_layout()
        self.generated(
            "frequency_response",
            fig,
            "CrSBr fundamental/harmonic frequency response and fundamental phase.",
            ("frequency_hz", "signal", "harmonic", "amplitude_v", "phase_deg"),
        )

    def _plot_frequency_normalized(self, plt: object) -> None:
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
        plotted = 0
        for column, signal in enumerate(("xy", "xx")):
            for row, harmonic in enumerate((2, 3)):
                records = [
                    record
                    for record in self.dataset.records
                    if record.signal == signal
                    and record.harmonic == harmonic
                    and record.frequency_hz is not None
                    and record.drive_current_a not in (None, 0.0)
                    and record.voltage_v is not None
                ]
                groups = _group_sweeps(records, "frequency")
                for key, group in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:8]:
                    x, y = _median_series(
                        group,
                        lambda record: record.frequency_hz,
                        lambda record, order=harmonic: abs(record.voltage_v or 0.0)
                        / abs(record.drive_current_a or 1.0) ** order,
                    )
                    if len(x) < 2 or np.any(x <= 0) or np.any(y <= 0):
                        continue
                    axes[row, column].loglog(x, y, "o-", ms=3, label=_short_label(key))
                    plotted += 1
                axes[row, column].set(
                    xlabel="Frequency (Hz)",
                    ylabel=f"|V{harmonic}w| / |I|^{harmonic} (V/A^{harmonic})",
                    title=f"{signal} {harmonic}w",
                )
                if axes[row, column].lines:
                    axes[row, column].legend(fontsize=7)
        if not plotted:
            plt.close(fig)
            self.skipped(
                "frequency_normalized_harmonics",
                "Frequency dependence of |V2w|/I^2 and |V3w|/I^3.",
                "No harmonic frequency series with nonzero current is available.",
                ("frequency_hz", "drive_current_a", "harmonic", "amplitude_v"),
            )
            return
        fig.tight_layout()
        self.generated(
            "frequency_normalized_harmonics",
            fig,
            "CrSBr normalized nonlinear harmonic frequency response.",
            ("frequency_hz", "drive_current_a", "signal", "harmonic", "amplitude_v"),
        )

    def _plot_temperature_dependence(self, plt: object) -> None:
        if not _has_span(
            (record.temperature_k for record in self.dataset.records), 0.01
        ):
            self.skipped(
                "temperature_dependence",
                "Harmonic voltage versus temperature at fixed conditions.",
                "Fewer than two temperatures are present.",
                ("temperature_k", "field_t", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig, axes = plt.subplots(3, 2, figsize=(12, 11), squeeze=False)
        plotted = 0
        for column, signal in enumerate(("xy", "xx")):
            for row, harmonic in enumerate((1, 2, 3)):
                records = [
                    r
                    for r in self.dataset.records
                    if r.signal == signal and r.harmonic == harmonic and r.voltage_v is not None
                ]
                groups = _group_sweeps(records, "temperature")
                for key, group in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:8]:
                    x, y = _median_series(
                        group,
                        lambda record: record.temperature_k,
                        lambda record: (record.voltage_v or 0.0) * 1e6,
                        x_digits=3,
                    )
                    if len(x) < 2 or float(np.ptp(x)) < 0.01:
                        continue
                    axes[row, column].plot(x, y, "o-", ms=3, label=_short_label(key))
                    plotted += 1
                axes[row, column].set(
                    xlabel="Temperature (K)",
                    ylabel=f"V{harmonic}w (uV)",
                    title=f"{signal} {harmonic}w",
                )
                if axes[row, column].lines:
                    axes[row, column].legend(fontsize=7)
        if not plotted:
            plt.close(fig)
            self.skipped(
                "temperature_dependence",
                "Harmonic voltage versus temperature at fixed conditions.",
                "No constant-condition temperature series has at least two points.",
                ("temperature_k", "field_t", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig.tight_layout()
        self.generated(
            "temperature_dependence",
            fig,
            "Paper Fig. 3(d) and CrSBr temperature overview.",
            ("temperature_k", "field_t", "signal", "harmonic", "x_v", "amplitude_v"),
        )

    def _plot_field_dependence(self, plt: object) -> None:
        if not _has_range((record.field_t for record in self.dataset.records)):
            self.skipped(
                "field_dependence",
                "First-, second-, and third-harmonic field traces.",
                "Fewer than two magnetic fields are present.",
                ("field_t", "timestamp_s", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig, axes = plt.subplots(3, 2, figsize=(12, 11), squeeze=False)
        plotted = 0
        for column, signal in enumerate(("xy", "xx")):
            for row, harmonic in enumerate((1, 2, 3)):
                records = [
                    r
                    for r in self.dataset.records
                    if r.signal == signal and r.harmonic == harmonic and r.voltage_v is not None
                ]
                groups = _group_sweeps(records, "field")
                for key, group in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:8]:
                    ordered = sorted(group, key=lambda record: (record.timestamp_s, record.observation_index))
                    x = np.asarray([record.field_t for record in ordered], dtype=float)
                    y = np.asarray([(record.voltage_v or 0.0) * 1e6 for record in ordered], dtype=float)
                    if len({round(value, 9) for value in x}) < 2:
                        continue
                    axes[row, column].plot(x, y, "o-", ms=3, label=_short_label(key))
                    plotted += 1
                axes[row, column].set(
                    xlabel="Magnetic field (T)",
                    ylabel=f"V{harmonic}w (uV)",
                    title=f"{signal} {harmonic}w",
                )
                if axes[row, column].lines:
                    axes[row, column].legend(fontsize=7)
        if not plotted:
            plt.close(fig)
            self.skipped(
                "field_dependence",
                "First-, second-, and third-harmonic field traces.",
                "No constant-condition magnetic-field series has at least two points.",
                ("field_t", "timestamp_s", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig.tight_layout()
        self.generated(
            "field_dependence",
            fig,
            "Paper Fig. 2(d-g), Fig. 3(e), and CrSBr field-loop overview; acquisition order is preserved.",
            ("field_t", "timestamp_s", "signal", "harmonic", "x_v", "amplitude_v"),
        )

    def _plot_angle_dependence(self, plt: object) -> None:
        records = [
            record
            for record in self.dataset.records
            if record.sample_position_deg is not None and record.harmonic == 2
        ]
        if not _has_range((record.sample_position_deg for record in records), 3):
            self.skipped(
                "angle_dependence",
                "Second-harmonic voltage versus sample angle for multiple fields.",
                "At least three distinct rotator angles are required.",
                ("sample_position_deg", "field_t", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), squeeze=False)
        plotted = 0
        for column, signal in enumerate(("xy", "xx")):
            groups = _group_sweeps([r for r in records if r.signal == signal], "angle")
            for key, group in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:10]:
                x, y = _median_series(
                    group,
                    lambda record: record.sample_position_deg,
                    lambda record: (record.voltage_v or 0.0) * 1e6,
                    x_digits=4,
                )
                if len(x) < 3:
                    continue
                axes[0, column].plot(x, y, "o-", ms=3, label=_short_label(key))
                plotted += 1
            axes[0, column].set(
                xlabel="Sample position (deg)",
                ylabel="V2w (uV)",
                title=signal,
            )
            if axes[0, column].lines:
                axes[0, column].legend(fontsize=7)
        if not plotted:
            plt.close(fig)
            self.skipped(
                "angle_dependence",
                "Second-harmonic voltage versus sample angle for multiple fields.",
                "No constant-condition angle series has at least three points.",
                ("sample_position_deg", "field_t", "harmonic", "x_v", "amplitude_v"),
            )
            return
        fig.tight_layout()
        self.generated(
            "angle_dependence",
            fig,
            "Paper Fig. 3(a-c); the rotation plane must be supplied by run metadata or the operator.",
            ("sample_position_deg", "field_t", "signal", "harmonic", "x_v", "amplitude_v"),
        )

    def _paired_harmonics(self, signal: str = "xx") -> list[tuple[PlotRecord, PlotRecord]]:
        groups: dict[tuple[str, int, str], dict[int, PlotRecord]] = defaultdict(dict)
        for record in self.dataset.records:
            if record.signal == signal:
                groups[(record.source, record.observation_index, record.instrument_channel)][
                    record.harmonic
                ] = record
        return [
            (harmonics[1], harmonics[2])
            for harmonics in groups.values()
            if 1 in harmonics and 2 in harmonics
        ]

    @staticmethod
    def _gamma(pair: tuple[PlotRecord, PlotRecord]) -> float | None:
        first, second = pair
        first_v = first.voltage_v
        second_v = second.voltage_v
        if (
            first_v in (None, 0.0)
            or second_v is None
            or first.drive_current_a in (None, 0.0)
            or abs(first.field_t) < 1e-3
        ):
            return None
        return 2.0 * second_v / (
            first_v * abs(first.field_t) * abs(first.drive_current_a)
        )

    def _plot_gamma(self, plt: object) -> None:
        pairs = [(pair, self._gamma(pair)) for pair in self._paired_harmonics("xx")]
        pairs = [(pair, gamma) for pair, gamma in pairs if gamma is not None]
        if len(pairs) < 2 or not _has_span(
            (pair[0].temperature_k for pair, _ in pairs), 0.01
        ):
            self.skipped(
                "magnetochiral_gamma",
                "Magnetochiral coefficient gamma versus temperature at several fields.",
                "Paired xx 1w/2w records at nonzero current and field over multiple temperatures are required.",
                ("temperature_k", "field_t", "drive_current_a", "xx/1w", "xx/2w"),
            )
            return
        fig, ax = plt.subplots(figsize=(7.5, 5.2))
        grouped: dict[
            tuple[str, float, float | None, float | None],
            list[tuple[PlotRecord, float]],
        ] = defaultdict(list)
        for pair, gamma in pairs:
            first = pair[0]
            grouped[
                (
                    first.source,
                    round(abs(first.field_t), 2),
                    _rounded(first.drive_current_a, 9),
                    _rounded(first.frequency_hz, 3),
                )
            ].append((first, float(gamma)))
        plotted = 0
        eligible = sorted(
            grouped.items(),
            key=lambda item: len({round(value[0].temperature_k, 2) for value in item[1]}),
            reverse=True,
        )
        for (source, field, current, frequency), values in eligible[:10]:
            bins: dict[float, list[float]] = defaultdict(list)
            for record, gamma in values:
                bins[round(record.temperature_k, 3)].append(gamma)
            items = sorted((temperature, float(np.median(gammas))) for temperature, gammas in bins.items())
            if len(items) < 2 or items[-1][0] - items[0][0] < 0.01:
                continue
            ax.plot(
                [item[0] for item in items],
                [item[1] for item in items],
                "o-",
                ms=4,
                label=(
                    f"{Path(source).stem}, |B|={field:g} T, "
                    f"I={float(current or 0.0) * 1e3:g} mA, "
                    f"f={float(frequency or 0.0):g} Hz"
                ),
            )
            plotted += 1
        if not plotted:
            plt.close(fig)
            self.skipped(
                "magnetochiral_gamma",
                "Magnetochiral coefficient gamma versus temperature at several fields.",
                "No field group contains multiple temperatures.",
                ("temperature_k", "field_t", "drive_current_a", "xx/1w", "xx/2w"),
            )
            return
        ax.set(xlabel="Temperature (K)", ylabel="gamma (T^-1 A^-1)")
        ax.legend()
        fig.tight_layout()
        self.generated(
            "magnetochiral_gamma",
            fig,
            "Paper Fig. 3(f), gamma = 2 V2w / (V1w |B| |I|).",
            ("temperature_k", "field_t", "drive_current_a", "xx/1w", "xx/2w"),
        )

    def _plot_temperature_field_map(self, plt: object) -> None:
        records = [
            record
            for record in self.dataset.records
            if record.signal == "xx"
            and record.harmonic == 2
            and record.voltage_v is not None
            and abs(record.field_t) >= 1e-3
        ]
        by_source: dict[str, list[PlotRecord]] = defaultdict(list)
        for record in records:
            by_source[record.source].append(record)
        eligible_sources = [
            (source, values)
            for source, values in by_source.items()
            if _has_span((record.temperature_k for record in values), 0.01)
            and _has_range((record.field_t for record in values))
            and len(
                {
                    (round(record.temperature_k, 2), round(record.field_t, 4))
                    for record in values
                }
            )
            >= 4
        ]
        if not eligible_sources:
            self.skipped(
                "temperature_field_v2_over_b_map",
                "Temperature-field contour of V2w/B.",
                "A two-dimensional temperature-field set with nonzero field is required.",
                ("temperature_k", "field_t", "xx/2w"),
            )
            return
        source, records = max(eligible_sources, key=lambda item: len(item[1]))
        bins: dict[tuple[float, float], list[float]] = defaultdict(list)
        for record in records:
            bins[(round(record.temperature_k, 2), round(record.field_t, 4))].append(
                (record.voltage_v or 0.0) / record.field_t * 1e6
            )
        points = [
            (temperature, field, float(np.median(values)))
            for (temperature, field), values in bins.items()
        ]
        if len(points) < 4:
            self.skipped(
                "temperature_field_v2_over_b_map",
                "Temperature-field contour of V2w/B.",
                "At least four distinct temperature-field points are required.",
                ("temperature_k", "field_t", "xx/2w"),
            )
            return
        fig, ax = plt.subplots(figsize=(8, 5.5))
        temperatures = np.asarray([point[0] for point in points])
        fields = np.asarray([point[1] for point in points])
        values = np.asarray([point[2] for point in points])
        try:
            contour = ax.tricontourf(temperatures, fields, values, levels=24, cmap="coolwarm")
        except (RuntimeError, ValueError):
            plt.close(fig)
            self.skipped(
                "temperature_field_v2_over_b_map",
                "Temperature-field contour of V2w/B.",
                "Temperature-field coordinates are collinear or insufficient for triangulation.",
                ("temperature_k", "field_t", "xx/2w"),
            )
            return
        fig.colorbar(contour, ax=ax, label="V2w / B (uV/T)")
        ax.set(xlabel="Temperature (K)", ylabel="Magnetic field (T)")
        ax.set_title(Path(source).stem)
        fig.tight_layout()
        self.generated(
            "temperature_field_v2_over_b_map",
            fig,
            "Paper Fig. 4(b) transport contour; zero-field points are excluded.",
            ("temperature_k", "field_t", "xx/2w"),
        )

    def _plot_transport_phase_overview(self, plt: object) -> None:
        second = [
            record
            for record in self.dataset.records
            if record.signal == "xx" and record.harmonic == 2 and record.voltage_v is not None
        ]
        field_pairs: dict[
            tuple[str, float, float, float | None, float | None],
            dict[str, list[float]],
        ] = defaultdict(
            lambda: {"positive": [], "negative": []}
        )
        for record in second:
            if abs(record.field_t) < 1e-3:
                continue
            key = (
                record.source,
                round(record.temperature_k, 2),
                round(abs(record.field_t), 2),
                _rounded(record.drive_current_a, 9),
                _rounded(record.frequency_hz, 3),
            )
            branch = "positive" if record.field_t > 0 else "negative"
            field_pairs[key][branch].append(record.voltage_v or 0.0)
        deltas_by_source: dict[str, dict[float, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for (source, temperature, _field, _current, _frequency), branches in field_pairs.items():
            if branches["positive"] and branches["negative"]:
                deltas_by_source[source][temperature].append(
                    float(np.median(branches["positive"]))
                    - float(np.median(branches["negative"]))
                )
        gamma_pairs = [(pair, self._gamma(pair)) for pair in self._paired_harmonics("xx")]
        gamma_by_source: dict[str, dict[float, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for pair, gamma in gamma_pairs:
            if gamma is not None:
                gamma_by_source[pair[0].source][round(pair[0].temperature_k, 2)].append(
                    float(gamma)
                )
        delta_source, deltas = max(
            deltas_by_source.items(),
            key=lambda item: len(item[1]),
            default=("", {}),
        )
        gamma_source, gamma_by_temperature = max(
            gamma_by_source.items(),
            key=lambda item: len(item[1]),
            default=("", {}),
        )
        delta_has_span = _has_span(deltas, 0.01)
        gamma_has_span = _has_span(gamma_by_temperature, 0.01)
        if not delta_has_span and not gamma_has_span:
            self.skipped(
                "transport_phase_overview",
                "Transport-only phase overview near a crossover temperature.",
                "Multiple temperatures with +/-B V2w pairs or gamma values are required.",
                ("temperature_k", "field_t", "xx/1w", "xx/2w"),
            )
            return
        fig, axes = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
        if delta_has_span:
            items = sorted((temperature, float(np.median(values)) * 1e6) for temperature, values in deltas.items())
            axes[0].plot([item[0] for item in items], [item[1] for item in items], "o-")
        axes[0].set(ylabel="Delta V2w(+B - -B) (uV)", title=Path(delta_source).stem)
        if gamma_has_span:
            items = sorted(
                (temperature, float(np.median(values)))
                for temperature, values in gamma_by_temperature.items()
            )
            axes[1].plot([item[0] for item in items], [item[1] for item in items], "o-")
        axes[1].set(
            xlabel="Temperature (K)",
            ylabel="gamma (T^-1 A^-1)",
            title=Path(gamma_source).stem,
        )
        fig.tight_layout()
        self.generated(
            "transport_phase_overview",
            fig,
            "Transport-only subset of Paper Fig. 4(a); auxiliary Nernst/scattering/Hall data are not invented.",
            ("temperature_k", "field_t", "xx/1w", "xx/2w"),
        )

    def _gate_records(self) -> list[PlotRecord]:
        return [
            record
            for record in self.dataset.records
            if record.signal == "xx"
            and record.harmonic == 1
            and record.gate_top_voltage_v is not None
            and record.gate_bottom_voltage_v is not None
            and record.resistance_ohm is not None
        ]

    def _plot_gate_resistance_map(self, plt: object) -> None:
        records = self._gate_records()
        grid = _grid(
            (
                float(record.gate_bottom_voltage_v),
                float(record.gate_top_voltage_v),
                float(record.resistance_ohm),
            )
            for record in records
        )
        if grid is None:
            self.skipped(
                "gate_resistance_map",
                "Dual-gate Rxx(Vbottom,Vtop) map.",
                "A two-dimensional gate grid with xx/1w resistance-like values is required.",
                ("gate_bottom_voltage_v", "gate_top_voltage_v", "xx/1w", "drive_current_a"),
            )
            return
        xs, ys, values = grid
        fig, ax = plt.subplots(figsize=(7.5, 5.8))
        mesh = ax.pcolormesh(xs, ys, values, shading="auto", cmap="viridis")
        fig.colorbar(mesh, ax=ax, label="X1w / drive current (ohm)")
        ax.set(xlabel="Bottom gate (V)", ylabel="Top gate (V)")
        fig.tight_layout()
        self.generated(
            "gate_resistance_map",
            fig,
            "Dual-gate resistance-like map used to identify calibrated trajectories.",
            ("gate_bottom_voltage_v", "gate_top_voltage_v", "xx/1w", "drive_current_a"),
        )

    def _plot_gate_leakage(self, plt: object) -> None:
        if not self.dataset.leakage:
            self.skipped(
                "gate_leakage",
                "Top- and bottom-gate leakage map or trace.",
                "Raw instrument samples with gate-current readback are unavailable.",
                ("gate_top_measured_current_a", "gate_bottom_measured_current_a"),
            )
            return
        top_grid = _grid(
            (
                row.gate_bottom_voltage_v,
                row.gate_top_voltage_v,
                abs(row.top_current_a),
            )
            for row in self.dataset.leakage
            if row.top_current_a is not None
        )
        bottom_grid = _grid(
            (
                row.gate_bottom_voltage_v,
                row.gate_top_voltage_v,
                abs(row.bottom_current_a),
            )
            for row in self.dataset.leakage
            if row.bottom_current_a is not None
        )
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), squeeze=False)
        if top_grid is not None and bottom_grid is not None:
            for column, (title, grid) in enumerate((('Top gate', top_grid), ('Bottom gate', bottom_grid))):
                xs, ys, values = grid
                mesh = axes[0, column].pcolormesh(xs, ys, values, shading="auto", cmap="magma")
                fig.colorbar(mesh, ax=axes[0, column], label="|Leakage| (A)")
                axes[0, column].set(xlabel="Bottom gate (V)", ylabel="Top gate (V)", title=title)
        else:
            ordered = sorted(
                self.dataset.leakage,
                key=lambda row: (row.sequence_index, row.sample_index),
            )
            x = np.arange(len(ordered))
            for column, (title, field) in enumerate((('Top gate', 'top_current_a'), ('Bottom gate', 'bottom_current_a'))):
                y = [abs(getattr(row, field)) if getattr(row, field) is not None else np.nan for row in ordered]
                axes[0, column].semilogy(x, y, "-", lw=1)
                axes[0, column].set(xlabel="Raw sample order", ylabel="|Leakage| (A)", title=title)
        fig.tight_layout()
        self.generated(
            "gate_leakage",
            fig,
            "Gate-leakage safety diagnostic; this is not sample transport current.",
            ("gate_top_measured_current_a", "gate_bottom_measured_current_a"),
        )

    def _plot_paired_gate_linecut(self, plt: object) -> None:
        records = sorted(self._gate_records(), key=lambda record: record.observation_index)
        pairs = {
            (round(float(record.gate_top_voltage_v), 9), round(float(record.gate_bottom_voltage_v), 9))
            for record in records
        }
        top_values = {pair[0] for pair in pairs}
        bottom_values = {pair[1] for pair in pairs}
        is_line = len(pairs) >= 2 and len(pairs) < len(top_values) * len(bottom_values)
        if not is_line:
            self.skipped(
                "paired_gate_linecut",
                "Rxx and both gate voltages along a paired trajectory.",
                "The gate points form a full grid or do not contain a multi-point trajectory.",
                ("observation_index", "gate_top_voltage_v", "gate_bottom_voltage_v", "xx/1w"),
            )
            return
        fig, axes = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
        order = np.arange(len(records))
        axes[0].plot(order, [record.resistance_ohm for record in records], "o-")
        axes[0].set(ylabel="X1w / drive current (ohm)")
        axes[1].plot(order, [record.gate_top_voltage_v for record in records], "o-", label="Top gate")
        axes[1].plot(order, [record.gate_bottom_voltage_v for record in records], "s--", label="Bottom gate")
        axes[1].set(xlabel="Paired point index", ylabel="Gate voltage (V)")
        axes[1].legend()
        fig.tight_layout()
        self.generated(
            "paired_gate_linecut",
            fig,
            "Dual-gate line cut after an independently justified trajectory is selected.",
            ("observation_index", "gate_top_voltage_v", "gate_bottom_voltage_v", "xx/1w"),
        )

    def _plot_n_d_map(self, plt: object) -> None:
        if self.calibration is None:
            self.skipped(
                "n_d_resistance_map",
                "Resistance-like map transformed from gate voltages to carrier density n and displacement D.",
                "No explicit gate-capacitance/offset calibration was supplied.",
                ("gate voltages", "Ct", "Cb", "voltage offsets", "xx/1w"),
            )
            return
        records = self._gate_records()
        points: list[tuple[float, float, float]] = []
        for record in records:
            density, displacement = self.calibration.convert(
                float(record.gate_top_voltage_v),
                float(record.gate_bottom_voltage_v),
            )
            points.append((density / 1e16, displacement, float(record.resistance_ohm)))
        if len(points) < 4:
            self.skipped(
                "n_d_resistance_map",
                "Resistance-like map transformed from gate voltages to carrier density n and displacement D.",
                "At least four calibrated gate-grid points are required.",
                ("gate voltages", "Ct", "Cb", "voltage offsets", "xx/1w"),
            )
            return
        fig, ax = plt.subplots(figsize=(8, 5.8))
        x = np.asarray([point[0] for point in points])
        y = np.asarray([point[1] for point in points])
        z = np.asarray([point[2] for point in points])
        try:
            contour = ax.tricontourf(x, y, z, levels=24, cmap="viridis")
        except (RuntimeError, ValueError):
            plt.close(fig)
            self.skipped(
                "n_d_resistance_map",
                "Resistance-like map transformed from gate voltages to carrier density n and displacement D.",
                "Calibrated n-D coordinates are collinear or insufficient for triangulation.",
                ("gate voltages", "Ct", "Cb", "voltage offsets", "xx/1w"),
            )
            return
        fig.colorbar(contour, ax=ax, label="X1w / drive current (ohm)")
        ax.set(xlabel="Carrier density n (10^16 m^-2)", ylabel="Displacement D (C m^-2)")
        fig.tight_layout()
        self.generated(
            "n_d_resistance_map",
            fig,
            "Calibrated dual-gate n-D transport map; sign convention follows the supplied calibration.",
            ("gate voltages", "Ct", "Cb", "voltage offsets", "xx/1w"),
        )

    def _record_unavailable_paper_panels(self) -> None:
        self.skipped(
            "nernst_temperature_field_map",
            "Paper Fig. 4(c) Nernst-coefficient temperature-field contour.",
            "The transport schema contains no calibrated thermal gradient or Nernst coefficient.",
            ("temperature_k", "field_t", "Nernst coefficient"),
        )
        self.skipped(
            "scattering_rate_temperature",
            "Paper Fig. 4(a) scattering-rate series.",
            "Scattering rate and effective-mass calibration are not measured by this setup.",
            ("temperature_k", "scattering rate"),
        )
        self.skipped(
            "hall_coefficient_temperature",
            "Paper Fig. 4(a) Hall-coefficient series.",
            "A Hall coefficient requires sample geometry and an explicit Hall extraction protocol; Vxy alone is insufficient.",
            ("temperature_k", "field_t", "xy/1w", "sample geometry"),
        )
        self.skipped(
            "electrode_direction_comparison",
            "Paper Fig. 2(f,g) explicit forward/reversed electrode comparison.",
            "Run metadata does not yet encode electrode geometry or signed current-direction labels.",
            ("electrode geometry", "current direction", "field_t", "harmonic voltage"),
        )


def generate_publication_plots(
    dataset: PlotDataset,
    output_dir: str | Path,
    *,
    calibration: GateCalibration | None = None,
    formats: Sequence[str] = ("png", "pdf"),
) -> dict[str, object]:
    allowed_formats = {"png", "pdf"}
    normalized_formats = tuple(dict.fromkeys(str(value).lower() for value in formats))
    if not normalized_formats or set(normalized_formats).difference(allowed_formats):
        raise PlotDataError("Plot formats must be one or both of: png, pdf.")
    if not dataset.records:
        raise PlotDataError("Plot dataset contains no transport records.")
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    suite = _PlotSuite(dataset, destination, normalized_formats, calibration)
    return suite.run()
