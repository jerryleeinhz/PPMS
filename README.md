# PPMS QCoDeS Control

This is a new, simulation-first implementation. The incomplete
`offline_kit_revised` directory is treated as a requirements reference and is
not imported or modified.

See [docs/DESIGN_GOALS.md](docs/DESIGN_GOALS.md) for the directory map,
architecture boundaries, design goals, and real-hardware entry criteria.
For the SR lock-in resistance definition, dual-gate `grid`/`paired` workflow,
manual TOML parameters, data columns, and a file-by-file testing map, see
[docs/SR_DUAL_GATE_TEST_GUIDE.md](docs/SR_DUAL_GATE_TEST_GUIDE.md).
For SQLite/ETO plotting, the CrSBr Notebook and 2M-WS2 paper figure map,
dual-gate `R(Vg1,Vg2)`/`R(n,D)` analysis, and interpretation limits, see
[docs/DATA_ANALYSIS.md](docs/DATA_ANALYSIS.md).
For the current two-backend design, verified ETO format, compliance findings,
implementation status, and next-chat handoff, read
[docs/PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md) first.

Current milestone:

- strict TOML configuration;
- QCoDeS-backed simulated SR830, SR865A, two Keithley 2400 SMUs, and DynaCool;
- a fail-closed `SafeStation` facade;
- voltage, frequency, magnetic-field, temperature-field-grid, and dual-gate-grid protocols;
- sequential `1w -> 2w -> 3w` acquisition from both SR830 (`xx`) and SR865A (`xy`);
- averaged six-signal readings with per-harmonic quality flags and retries;
- per-sample SR830, SR865A, SMU, and PPMS readbacks in SQLite;
- PPMS rotator position/status readback when that option is available;
- per-sample SR830 and SR865A readings in the shared `transport_readings` table;
- append-only attempts, physical-state samples, and events in SQLite;
- simulation measurement CLI and CSV export;
- strict real-hardware endpoint configuration;
- read-only VISA and MultiPyVu hardware diagnostics;
- authorized real-hardware commands for all five SR protocols;
- a strict parser and inspection command for MultiVu ETO 1.2 `.dat` files;
- restart-safe incremental ETO file ingestion with atomic SQLite checkpoints;
- manifest-driven PNG/PDF analysis figures from a SQLite run or ETO file/directory,
  including current, frequency, temperature, field, angle, harmonic, gamma, and
  dual-gate products.

The SR830 front-panel `SINE OUT` is treated as a voltage source. Requested and
read-back amplitudes are stored in volts RMS. The configured series resistance
is used only to store an explicitly labelled estimated current; it is not a
current measurement. Cleanup returns the SR830 to the configured safe-idle
amplitude (normally 4 mV RMS), which is not physical zero. See
[docs/HARDWARE_DIAGNOSTICS.md](docs/HARDWARE_DIAGNOSTICS.md) before using it
on the PPMS computer, and record staged evidence with
[docs/HARDWARE_VALIDATION_CHECKLIST.md](docs/HARDWARE_VALIDATION_CHECKLIST.md).

## Run with the existing AI environment

From this directory in PowerShell:

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install --no-deps -e .
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m unittest discover -s tests -v
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-frequency config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-field config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-temperature-field config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate-gate config\simulation.toml
```

The editable-install step is required once for this `src/`-layout project. It
also installs the `ppms-control` command into the AI environment. Re-run it
after moving the repository to a different directory. Unless the `AI` Conda
environment is activated, prefer the complete Python path shown above; the
environment's `Scripts` directory may not be on the current PowerShell `PATH`.
Each command is standalone and can be pasted separately.

Install the optional plotting dependency when analysis output is needed:

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install -e '.[analysis]'
```

## Inspect existing MultiVu ETO data

The installed ETO 1.2 format stores the fundamental voltage as amplitude and
in-phase/quadrature components, while the second and third harmonics are dB
ratios relative to the fundamental. The parser preserves those dB values and
derives only unsigned harmonic amplitudes; it does not invent a harmonic sign
or phase. Channel 1 and Channel 2 records may occur on separate rows.

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control inspect-eto-data `
  'C:\path\to\measurement.dat'
```

The command reports record counts, active-channel counts, temperature, field,
and sample-position ranges without modifying the source file.

To ingest an existing file once, while explicitly preserving the current
sample's channel mapping:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control follow-eto-data `
  'C:\path\to\measurement.dat' 'C:\PPMS_Data\ppms_control.sqlite' `
  --sample-name 'SAMPLE_NAME' --channel-1-role xy --channel-2-role xx --once
```

Omit `--once` to follow a file that MultiVu is still writing. The command
stores every completed increment and its byte/line checkpoint in one SQLite
transaction. It defers a trailing partial line, rejects truncation or rewriting
of consumed data, and can resume an interrupted run with `--resume-run`.
Channel roles are mandatory because ETO channel numbers do not universally
mean `xx` or `xy`.

## Generate analysis figures

Generate figures from one SQLite run:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control plot-data `
  'C:\PPMS_Data\ppms_control.sqlite' 'C:\PPMS_Data\figures\RUN_ID' `
  --run-id '<RUN_ID>'
```

Or read one ETO `.dat` file or a directory recursively, with explicit channel
roles:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control plot-data `
  'C:\path\to\ETO_directory' 'C:\PPMS_Data\figures\ETO_run' `
  --channel-1-role xy --channel-2-role xx
```

The default output is PNG and PDF plus `analysis_records.csv` and
`analysis_manifest.json`; `fit_summary.csv` is added when a fit is available. The manifest states which
requested figure types were generated or skipped and why. It also records
compliance-related input warnings. Add `--gate-calibration
config\gate_calibration.local.toml` only after replacing the example values
with independently justified device capacitances and offsets. Full formulas,
paper-panel mappings, and data-quality boundaries are in
[docs/DATA_ANALYSIS.md](docs/DATA_ANALYSIS.md).

The example database is written below `run_data/`. For real measurements, use
a local, non-synchronised data directory and archive the closed run afterwards;
do not write an active SQLite database into OneDrive.

## Read-only hardware diagnostics

On the PPMS computer, install the optional MultiPyVu dependency, copy the
hardware template to an ignored local configuration, and replace every
`CHANGE_ME` value before validation:

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -e '.[real-ppms]'
Copy-Item 'config\hardware.example.toml' 'config\hardware.local.toml'
& '.\.venv\Scripts\python.exe' -m ppms_control validate-config config\hardware.local.toml
& '.\.venv\Scripts\python.exe' -m ppms_control diagnose-hardware config\hardware.local.toml
```

The conventional same-computer MultiPyVu endpoint is configured as
`127.0.0.1:5000`. The diagnostic command does not send setpoints.

To inspect whether an already-running DynaCool MultiVu instance exposes
sequence control through OLE, without invoking any OLE method:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control inspect-multivu-ole
```

This command uses `GetActiveObject`, so it refuses to proceed when MultiVu is
not already running and does not launch it. Preserve the JSON output for API
review before enabling any sequence write operation.

## Authorized hardware sweeps

Use the `run_id` printed by a successful diagnostic performed with the exact
same configuration:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control run-hardware config\hardware.local.toml `
  --diagnostic-run-id '<DIAGNOSTIC_RUN_ID>' `
  --confirm 'I CONFIRM REAL HARDWARE CONTROL'
```

The command configures the SR830 as the internal-reference voltage source,
keeps SR865A on the external reference, prepares the configured PPMS
temperature and field, performs the selected sweep, and attempts safe cleanup.
Changing any configuration value invalidates the previous diagnostic
authorization.

The corresponding commands for the other configured protocols are
`run-hardware-frequency`, `run-hardware-field`, and
`run-hardware-temperature-field`, and `run-hardware-gate`; they use the same
diagnostic-run and exact confirmation arguments.

### Dual-gate SR measurement

`[gate_sweep]` defines top- and bottom-gate endpoints at one fixed temperature,
field, SR830 excitation, and frequency. With `mode = "grid"`, the independent
point counts form a two-dimensional map; a snake path prevents the bottom gate
from jumping across its full range between adjacent top-gate rows. After a
zero-field trajectory has been selected from that map or from an independent
capacitance calibration, set `mode = "paired"`, use equal top/bottom point
counts, and make the two endpoint pairs describe that trajectory. The two gate
setpoints then change together point by point. The software does not infer the
physical zero-field criterion from resistance alone.

Every transition is subdivided by
`gate_ramp_step_v`; leakage is checked at every ramp step and again during every
raw SR sample. `gate_ramp_step_delay_s` controls inter-step delay and
`gate_settle_s` controls the wait before lock-in acquisition. Configuration is
rejected unless `gate_leakage_limit_a` is strictly below the Keithley
`gate_compliance_limit_a`, so software abort is requested before the hardware
compliance boundary.

Run the simulation before considering real control:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control simulate-gate config\simulation.toml
```

For real hardware, first replace the zero-bias placeholders in
`config/hardware.local.toml` with sample-approved limits, complete the staged
Keithley checks in the hardware validation checklist, and obtain a successful
diagnostic `run_id`. Then use:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control run-hardware-gate config\hardware.local.toml `
  --diagnostic-run-id '<DIAGNOSTIC_RUN_ID>' `
  --confirm 'I CONFIRM REAL HARDWARE CONTROL'
```

Normal completion ramps both gates back to zero. Any leakage, output-state,
readback, driver, acquisition, or interruption failure enters the common
fail-closed cleanup, which requests the SR830 safe-idle amplitude and zero/off
for both Keithleys. The configured limits are software interlocks, not a
substitute for front-panel compliance and sample-specific approval.

Every raw average sample is committed immediately to `instrument_samples`,
including SR830/SR865A X/Y, frequency, harmonic, lock and overload state; both
Keithley voltage, output, compliance, and measured current; and PPMS
temperature, field, chamber, status, and stability. Export these records with:

The Keithley current in these rows is gate leakage current. It is not the
sample transport current and `Vgate / Ileak` is not reported as sample
resistance. Sample resistance-like values are derived from lock-in voltage
divided by the configured SR drive-current estimate.

When a Keithley output is off, `gate_*_current_available` is `0`; the adjacent
current value must not be interpreted as a measurement.

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control export-samples `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' 'C:\PPMS_Data\instrument_samples.csv'
```

The backend-independent long table and a plot-ready per-observation summary
are exported separately:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control export-transport `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' 'C:\PPMS_Data\transport_long.csv'
& '.\.venv\Scripts\python.exe' -m ppms_control export-transport-summary `
  'C:\PPMS_Data\ppms_control.sqlite' '<RUN_ID>' 'C:\PPMS_Data\transport_summary.csv'
```

The summary averages repeated SR samples only within the same sequence
condition, signal, instrument channel, and harmonic. ETO source rows remain
separate. Phase uses circular statistics, and derived resistance-like columns
are explicitly named `*_over_drive_current_ohm` because SR current is estimated
whereas ETO current may be measured.

Gate setpoints are present in accepted-attempt and transport-long exports as
`gate_top_voltage_v` and `gate_bottom_voltage_v`; summary exports provide the
corresponding `*_mean` and `*_std` columns.
