# PPMS QCoDeS Control

This is a new, simulation-first implementation. The incomplete
`offline_kit_revised` directory is treated as a requirements reference and is
not imported or modified.

Current milestone:

- strict TOML configuration;
- QCoDeS-backed simulated SR830, SR865A, two Keithley 2400 SMUs, and DynaCool;
- a fail-closed `SafeStation` facade;
- a fixed-temperature, fixed-field current sweep;
- averaged `xx/1w` and `xy/3w` readings with quality flags and retries;
- append-only attempts and events in SQLite;
- simulation-only CLI and CSV export.

Real hardware writes are not implemented in this milestone. In particular,
the QCoDeS DynaCool driver is not used. A later milestone will wrap the
manufacturer-supported MultiPyVu client.

## Run with the existing AI environment

From this directory in PowerShell:

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install --no-deps -e .
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m unittest discover -s tests -v
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config config\simulation.toml
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control simulate config\simulation.toml
```

The editable-install step is required once for this `src/`-layout project. It
also installs the `ppms-control` command into the AI environment. Re-run it
after moving the repository to a different directory. Unless the `AI` Conda
environment is activated, prefer the complete Python path shown above; the
environment's `Scripts` directory may not be on the current PowerShell `PATH`.
Each command is standalone and can be pasted separately.

The example database is written below `run_data/`. For real measurements, use
a local, non-synchronised data directory and archive the closed run afterwards;
do not write an active SQLite database into OneDrive.
