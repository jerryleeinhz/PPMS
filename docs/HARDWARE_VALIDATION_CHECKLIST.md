# Laboratory hardware validation checklist

This checklist records evidence; it is not a software test report. Mark every
item `PASS`, `FAIL`, or `NOT RUN`, and stop at the first failed stage.

## Record identity

| Item | Value |
| --- | --- |
| Date and local time | |
| Operator | |
| PPMS computer | |
| Sample/device | |
| Git commit | |
| Configuration file and SHA-256 | |
| Diagnostic `run_id` | |
| DynaCool/MultiVu version | |
| MultiPyVu version | |
| NI-VISA/NI-488.2 version | |

## Stage 0 — software-only verification

- [ ] `python -m unittest discover -s tests -v` passes.
- [ ] `python -m ppms_control validate-config <hardware-config>` passes.
- [ ] The active SQLite path is on a local, non-synchronised disk.
- [ ] The hardware configuration contains no `CHANGE_ME` values.
- [ ] Limits have been reviewed for this specific sample and wiring.

Do not continue if any software check fails.

## Stage 1 — physical safe state

- [ ] PPMS emergency and laboratory shutdown procedures are known.
- [ ] SR830 excitation path and series resistance are traced physically.
- [ ] The approved SR830 minimum safe-idle amplitude is documented; note
  explicitly whether a physical disconnect exists.
- [ ] Both Keithley outputs display OFF and their source levels are zero.
- [ ] Gate compliance limits are set independently on the front panels.
- [ ] Magnet, chamber, sample puck, wiring, and thermal anchoring are checked.
- [ ] Instrument model/serial numbers match the configuration record.

Do not rely on software cleanup as the only protection.

## Stage 2 — read-only communication

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m ppms_control diagnose-hardware config\hardware.local.toml
```

- [ ] All four VISA identities match their assigned physical roles.
- [ ] Both Keithley output-state queries report OFF.
- [ ] Lock-in reference frequency and source state are plausible.
- [ ] MultiPyVu reports temperature, field, chamber, and stability.
- [ ] The JSON result reports `success: true`.
- [ ] The SQLite diagnostic run and five component events exist.

## Stage 3 — PPMS controlled setpoints

Use the authorized real-control command only after Stages 0 through 2 pass.

- [ ] Test at zero field without a sensitive sample.
- [ ] Request a small field using a conservative ramp rate.
- [ ] Compare requested and front-panel field values and signs.
- [ ] Verify a station-owned field returns to zero after normal completion.
- [ ] Interrupt the test and verify the zero-field cleanup request is issued.
- [ ] Repeat with a small temperature change and verify the ramp rate.

## Stage 4 — Keithley gate control

- [ ] Use a dummy load or disconnected device first.
- [ ] Confirm voltage-source/current-sense mode and compliance.
- [ ] Enable one gate at a very small voltage.
- [ ] Compare front-panel voltage and measured leakage with software values.
- [ ] Verify over-leakage causes excitation and both gates to retreat.
- [ ] Inject/trigger a software exception and verify both outputs turn OFF.
- [ ] Repeat independently for the second gate.

## Stage 5 — excitation control

- [ ] Confirm the excitation source is the SR830 front-panel `SINE OUT`.
- [ ] Verify the configured RMS voltage against a calibrated high-impedance load.
- [ ] Verify the series resistance and voltage-to-current estimate independently.
- [ ] Confirm the configured safe-idle amplitude is acceptable for the sample;
  do not label the SR830 minimum amplitude as physical zero.
- [ ] Verify voltage and estimated-current limits at the driver, `SafeStation`,
  and physical hardware.
- [ ] Interrupt the program and verify the SR830 reaches the approved safe idle.

## Stage 6 — lock-in acquisition

- [ ] Confirm SR830 is the longitudinal `Vxx` role at `1ω/2ω/3ω`.
- [ ] Confirm SR865A is the transverse `Vxy` role at `1ω/2ω/3ω`.
- [ ] Verify both lock-ins accept harmonic changes and settle before readout.
- [ ] Verify reference-lock and overload status handling on both instruments.
- [ ] Compare X/Y values and signs with the front panels.
- [ ] Verify a deliberately bad reference causes bounded retries and rejection.

## Stage 7 — combined measurement

- [ ] Start with conservative SR830 voltage, zero gates, zero field, and a stable
  temperature.
- [ ] Verify every accepted and rejected attempt is present in SQLite.
- [ ] Verify `instrument_samples` contains every raw SR830, SR865A, SMU, and PPMS
  state/readback row expected from the configured averages.
- [ ] Interrupt and resume the run; accepted conditions are not repeated.
- [ ] Validate voltage, frequency, field, and temperature-field protocols
  separately before using their full configured ranges.
- [ ] Confirm normal completion and forced failure both execute cleanup.
- [ ] Export CSV and compare it with the accepted rows in SQLite.
- [ ] Archive the closed database, configuration, checklist, and Git commit.

## Final authorization

| Role | Name | Date | Signature/record |
| --- | --- | --- | --- |
| Operator | | | |
| Independent reviewer | | | |
| Laboratory owner/authorized person | | | |

Real hardware control is not authorized by this document alone. Authorization
requires completed evidence for every applicable stage and compliance with the
laboratory's local procedures.
