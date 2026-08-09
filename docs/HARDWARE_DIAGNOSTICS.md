# Read-only hardware diagnostics

## Scope

`diagnose-hardware` is the first laboratory-facing milestone. It checks that
the software can reach the expected SR830, SR865A, two Keithley 2400 SMUs, and
the MultiPyVu Server. It does not start a measurement and does not send
setpoint or output commands.

The PPMS client default is the conventional same-computer endpoint:

```text
127.0.0.1:5000
```

Change it in the local TOML file if the laboratory server uses another host or
port. Do not confuse the MultiPyVu port with a different Quantum Design API.

## Prerequisites on the PPMS computer

- Python 3.12 or 3.13 in an isolated environment;
- NI-VISA and, for NI GPIB interfaces, NI-488.2;
- the project dependencies plus `MultiPyVu==3.6.1`;
- DynaCool MultiVu and the laboratory's normal MultiPyVu Server running;
- confirmed VISA addresses for all four external instruments;
- a local non-synchronised directory for future measurement data.

## Prepare a local configuration

From the project directory in PowerShell:

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m pip install -e '.[real-ppms]'
Copy-Item 'config\hardware.example.toml' 'config\hardware.local.toml'
```

Edit `config\hardware.local.toml` and replace every `CHANGE_ME` value. Local
configuration files matching `config/*.local.toml` are ignored by Git.

The example defaults to:

```toml
ppms_host = "127.0.0.1"
ppms_port = 5000
visa_backend = "default"
```

`visa_backend = "default"` asks PyVISA to use the installed system VISA
implementation, normally NI-VISA on the PPMS computer.

## Validate before connecting

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control validate-config config\hardware.local.toml
```

Validation rejects placeholder or duplicate VISA addresses and invalid TCP
ports without contacting any device.

## Run the read-only diagnostic

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control diagnose-hardware config\hardware.local.toml
```

The VISA diagnostic sends only these queries:

- every instrument: `*IDN?`;
- SR830/SR865A: `FREQ?`, `HARM?`, `SLVL?`, and the reference-source query;
- Keithley 2400: output state, source mode, source voltage level, and current
  compliance queries.

The PPMS diagnostic opens a MultiPyVu Client session and reads server version,
temperature, field, chamber status, and temperature/field stability. Magnetic
field is reported in both Oe and tesla. The client session is then closed; the
server remains running.

Before contacting hardware, the command creates a
`read_only_hardware_diagnostic` run in the configured SQLite database. Each
component result is stored as an event, including failures, and the JSON output
contains the audit `run_id`.

The diagnostic intentionally uses raw PyVISA queries rather than constructing
the QCoDeS Keithley 2400 driver, because that driver's constructor sends format
configuration commands.

Exit codes:

- `0`: all five components passed;
- `2`: the configuration or command mode is invalid;
- `3`: one or more hardware components failed diagnostics.

## Safety boundary

A passing diagnostic proves only communication, expected model identity, and
readable state. It does not validate wiring, voltage-to-current conversion, output
polarity, leakage accuracy, magnetic-field direction, temperature accuracy, or
emergency interlocks.

Real control treats the SR830 `SINE OUT` as a voltage source. Its safe-idle
amplitude, voltage range, series resistance, estimated-current limit, and sweep
must be reviewed in the local configuration. The default safe idle is 4 mV RMS,
not physical zero or a disconnected circuit.

The software authorization boundary additionally requires the exact phrase
`I CONFIRM REAL HARDWARE CONTROL` and the `run_id` of a successful diagnostic
created from the identical configuration. Changing any configuration field
invalidates that diagnostic for control authorization.

After completing the validation checklist, the authorized command is:

```powershell
& 'C:\Users\liy56\.conda\envs\AI\python.exe' -m ppms_control run-hardware config\hardware.local.toml `
  --diagnostic-run-id '<DIAGNOSTIC_RUN_ID>' `
  --confirm 'I CONFIRM REAL HARDWARE CONTROL'
```

Use `run-hardware-frequency`, `run-hardware-field`, or
`run-hardware-temperature-field` in place of `run-hardware` for the other
configured protocols. Each command requires a diagnostic created from the
identical complete configuration.

Every raw measurement sample is committed to the SQLite `instrument_samples`
table with lock-in measurements and SR830, SMU, and PPMS state readbacks.
