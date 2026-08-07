# PPMS QCoDeS Control

This is a new, simulation-first implementation. The incomplete
`offline_kit_revised` directory is treated as a requirements reference and is
not imported or modified.

See [docs/DESIGN_GOALS.md](docs/DESIGN_GOALS.md) for the directory map,
architecture boundaries, design goals, and real-hardware entry criteria.
For the current two-backend design, verified ETO format, compliance findings,
implementation status, and next-chat handoff, read
[docs/PROJECT_HANDOFF.md](docs/PROJECT_HANDOFF.md) first.

Current milestone:

- strict TOML configuration;
- QCoDeS-backed simulated SR830, SR865A, two Keithley 2400 SMUs, and DynaCool;
- a fail-closed `SafeStation` facade;
- voltage, frequency, magnetic-field, and temperature-field-grid protocols;
- sequential `1w -> 2w -> 3w` acquisition from both SR830 (`xx`) and SR865A (`xy`);
- averaged six-signal readings with per-harmonic quality flags and retries;
- per-sample SR830, SR865A, SMU, and PPMS readbacks in SQLite;
- PPMS rotator position/status readback when that option is available;
- per-sample SR830 and SR865A readings in the shared `transport_readings` table;
- append-only attempts, physical-state samples, and events in SQLite;
- simulation measurement CLI and CSV export;
- strict real-hardware endpoint configuration;
- read-only VISA and MultiPyVu hardware diagnostics;
- authorized real-hardware commands for all four SR protocols;
- a strict parser and inspection command for MultiVu ETO 1.2 `.dat` files;
- restart-safe incremental ETO file ingestion with atomic SQLite checkpoints.

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
```

The editable-install step is required once for this `src/`-layout project. It
also installs the `ppms-control` command into the AI environment. Re-run it
after moving the repository to a different directory. Unless the `AI` Conda
environment is activated, prefer the complete Python path shown above; the
environment's `Scripts` directory may not be on the current PowerShell `PATH`.
Each command is standalone and can be pasted separately.

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
`run-hardware-temperature-field`; they use the same diagnostic-run and exact
confirmation arguments.

Every raw average sample is committed immediately to `instrument_samples`,
including SR830/SR865A X/Y, frequency, harmonic, lock and overload state; both
Keithley voltage, output, compliance, and measured current; and PPMS
temperature, field, chamber, status, and stability. Export these records with:

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
