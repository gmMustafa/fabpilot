---
doc_id: SOP-GEN-004
source_type: sop
title: "Sensor Verification and Calibration (Suspected False Alarm)"
machine_types: [cnc_mill, injection_molder, compressor, pump, conveyor]
revision: "D"
effective_date: 2024-09-02
---
# SOP-GEN-004 Sensor Verification and Calibration (Suspected False Alarm)

SYNTHETIC DOCUMENT (FabPilot demo). Applies to every monitored sensor on M-01 to M-10.

## 1. Scope

Use when a single reading looks wrong, **before** any machine work is ordered: one signal drifts or
alarms while the physically related signals stay normal, or a value changes while the machine is
stopped. Also the yearly verification of all sensors.

Typical false-alarm signature: a temperature that climbs slowly over days, with no change in
vibration, current or pressure, and that keeps climbing overnight with the machine OFF.

Why it matters: stopping a line and opening a machine for a sensor fault costs hours of production
and creates real risk (unnecessary dismantling). Ignoring a real fault because "it's probably the
sensor" costs more. This procedure decides it with a reference measurement, not a guess.

Duration: 30–60 min per sensor. Staff: 1 technician (instrumentation).

## 2. Safety

- Readings with the reference instrument are taken from outside guards where possible. If a guard
  must be opened, apply LOTO (SOP-GEN-001) and take the reading at standstill.
- Hot surfaces: contact thermometers on bearing housings above 60 °C need heat-resistant gloves.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Calibrated contact thermometer (±0.5 °C) | REF-TC-01 |
| Calibrated test pressure gauge (0.25 % class) with adapters | REF-PG-01 |
| Handheld vibration meter (velocity RMS, mm/s) | REF-VM-01 |
| Clamp ammeter (true RMS) | REF-CA-01 |
| Spare sensors: PT100 RTD probe, pressure transmitter, accelerometer | SNS-PT100, SNS-PT-xx, SNS-ACC |

## 4. Procedure

### 4.1 Desk check (before going to the machine)

1. Plot the suspect signal together with its physical neighbours for the last 3–7 days:
   temperature ↔ vibration, current, coolant/oil pressure; pressure ↔ current, flow-related signals.
2. Check the readings during standstill (nights, weekend). A stopped machine settles to ambient;
   a sensor that keeps changing at standstill is suspect.
3. If the neighbours show the same story (e.g. vibration rose before the temperature), **stop here**:
   it is a real machine problem; follow the machine manual.

### 4.2 Reference comparison

4. Measure the same quantity with the reference instrument as close to the sensor as possible, at two
   operating points if the machine can run (idle and load).
5. Compare. Tolerances: temperature ±2 °C; pressure ±2 % of the sensor range; vibration ±10 %;
   current ±3 %.
6. Within tolerance → the sensor is fine. The reading is real: go back to the machine manual.

### 4.3 Sensor fault found

7. Check the connector and cable first (loose, corroded, water ingress, damaged screen). Re-seat and
   re-check.
8. RTD (PT100) check: disconnect and measure the resistance. 100.0 Ω at 0 °C, about 119.4 Ω at 50 °C;
   a value off by more than 1 Ω means the probe or its wiring is faulty.
9. Replace the sensor if steps 7–8 don't fix it. Re-check against the reference.
10. ⚠ STOP: inform the shift leader that the alarm was a **false alarm**, so that no machine work is
    started and any work order for the machine is cancelled.

## 5. Acceptance criteria

- Sensor reading within the tolerance of §4.2 step 5 at two operating points.
- At standstill, the reading settles to the expected value (ambient, 0 bar, 0 A).
- The data from the drift period is marked as suspect in the incident record.

## 6. Record

Sensor tag, as-found and as-left readings, reference instrument and its calibration date, cause
(drift / connector / cable / probe), whether the alarm was a false alarm, technician name.
