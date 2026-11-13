---
doc_id: MAN-CNC-001
source_type: manual
title: "Vertical Machining Center VMC-500 — Operating & Maintenance Manual"
machine_types: [cnc_mill]
revision: "D"
effective_date: 2024-05-13
---
# Vertical Machining Center VMC-500 — Operating & Maintenance Manual

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-01, M-02, M-03 (Line 1, Machining).

## 1. Safety

- Never open the work area or the spindle housing while the spindle turns. The door interlock is
  not a maintenance isolation: before any work inside the machine, apply lockout/tagout (SOP-GEN-001).
- The spindle bearing housing can exceed 60 °C after a long cycle. Let it cool for 30 minutes or wear
  heat-resistant gloves.
- Flood coolant is an emulsion: wear nitrile gloves and eye protection; slipping hazard around the
  chip conveyor.

## 2. Technical data

| Item | Value |
|---|---|
| Spindle | 22 kW, max 12 000 rpm, HSK-A63, grease-lubricated angular contact bearings (front pair + rear) |
| Spindle cooling | Oil-jacket chiller, set point 25 °C |
| Flood coolant | Internal coolant pump 1.1 kW, 3–6 bar at the nozzle manifold, 400 l tank |
| Coolant flow interlock | Spindle stops (TRIP) when coolant pressure stays below 1.5 bar for 10 s while cutting |
| Spindle drive | Inverter-fed, overload relay set to 115 % of rated current for 60 s |

## 3. Monitored signals and alarm limits

The controller samples every 10 s. A HIGH or CRIT alarm needs 3 consecutive samples (30 s) beyond the
limit; a LOW alarm needs 6 samples (60 s) and is only evaluated in steady running (more than 1 h after
start-up and 2 min after any stop), because pressure and speed are *supposed* to be low during
warm-up and stops. Alarms clear with 5 % hysteresis inside the limit.

| Signal | Unit | Normal min | Normal max | Critical above | Low alarm below |
|---|---|---|---|---|---|
| `spindle_bearing_temp` | °C | 25 | 55 | 62.5 | — |
| `spindle_vibration` | mm/s | 0.3 | 2.8 | 3.425 | — |
| `spindle_motor_current` | A | 4 | 38 | 46.5 | — |
| `spindle_speed` | rpm | 1000 | 12000 | 14750 | 1000 |
| `coolant_pressure` | bar | 3 | 6 | 6.75 | 3 |

Vibration is velocity RMS measured at the front bearing housing. ISO 10816-3 zones for this machine
class: below 2.8 mm/s acceptable, 2.8–4.5 mm/s alert (plan maintenance), above 4.5 mm/s risk of damage.

## 4. Alarm codes

| Code | Meaning | First check |
|---|---|---|
| `TEMP-HIGH` | `spindle_bearing_temp` above 55 °C | Coolant pressure and spindle chiller running? Then vibration trend (§5.1, §5.2) |
| `TEMP-CRIT` | `spindle_bearing_temp` above 62.5 °C | Stop the spindle. Do not restart before the cause is known |
| `VIB-HIGH` | `spindle_vibration` above 2.8 mm/s | Tool holder seated and clean? Then trend over days (§5.1) |
| `VIB-CRIT` | `spindle_vibration` above 3.425 mm/s | Stop machining; inspect the spindle (§5.1) |
| `CURR-HIGH` / `CURR-CRIT` | Spindle current above 38 / 46.5 A | Dull tool, wrong feed, chip packing; §5.4 |
| `RPM-LOW` | Spindle below 1000 rpm while cutting | Overload: the drive can't hold speed (§5.4) |
| `PRESS-LOW` | `coolant_pressure` below 3 bar | Coolant pump, tank level, suction filter (§5.3) |
| `PRESS-HIGH` | `coolant_pressure` above 6 bar | Blocked nozzles or closed manifold valve |
| `TRIP` | Spindle stopped by protection | Read the alarm list *before* the trip: coolant interlock or overload relay |

## 5. Troubleshooting

### 5.1 Rising spindle vibration over days

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Vibration rises slowly over days and accelerates; temperature normal at first, rising only in the last stage | **Spindle bearing wear** (race damage, lubricant breakdown) | Trend `spindle_vibration` over 7 days; listen for rumbling at low speed; envelope spectrum shows bearing defect frequencies | Plan a spindle bearing replacement (SOP-CNC-002) before temperature rises. Once `TEMP-HIGH` appears with high vibration, wear is advanced |
| Vibration jumps after a tool change, constant afterwards | Unbalanced or badly seated tool holder | Clean the taper, re-clamp, test with a balanced test arbor | Balance or replace the holder |
| Vibration high at one speed only | Resonance of the setup | Run a speed ramp | Change speed or fixture |
| Vibration and temperature rise together from the start, after a spindle repair | Bearing preload too high / misalignment after assembly | Repair record, run-in procedure followed? | Repeat the run-in; contact service |

### 5.2 High spindle bearing temperature

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Temperature rises within minutes, `coolant_pressure` collapses towards 0 bar at the same time, often followed by `TRIP` | **Coolant pump failure** (motor, coupling, seized impeller) | Coolant pressure trend; pump motor running? Tank level | Do not restart the spindle. Check the coolant pump (SOP-CNC-003) |
| Temperature rises slowly over days, vibration rose before it | Late-stage **bearing wear** | Vibration trend (§5.1) | Replace bearings (SOP-CNC-002) |
| Temperature reading climbs slowly, even overnight with the machine OFF, while vibration, current and coolant pressure are normal | **Temperature sensor drift** (aging RTD, loose connector), not a machine fault | Compare with a handheld IR thermometer or contact probe on the housing | Verify and recalibrate the sensor (SOP-GEN-004); do not stop production for the bearing |
| Temperature high after a long heavy-cutting cycle, falls when cutting stops | Normal thermal load near the limit | Load profile | Review cutting parameters |

### 5.3 Low coolant pressure

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Pressure drops to near 0 within about a minute | Coolant pump failed | Pump motor current, coupling, impeller | SOP-CNC-003 |
| Pressure falls slowly over a shift | Low tank level, clogged suction filter in the tank | Tank level, filter basket | Top up / clean the basket |
| Pressure fluctuates, foam in the tank | Air ingress, wrong emulsion concentration | Refractometer reading | Correct the concentration |

### 5.4 High spindle current / speed drops

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Current jumps in episodes while spindle speed sags at the same time (healthy: current follows speed) | **Motor overload**: excessive cutting load, dull tool, chip packing | Tool wear, feed rate, chip evacuation | Reduce feed / change tool. Repeated episodes trip the overload relay (`TRIP`); see SOP-GEN-007 before resetting |
| Current high at constant speed for the whole program | Program uses aggressive parameters | Compare with the program's last good run | Adjust the program |

## 6. Diagnostic rules of thumb

- **One sensor alone is not evidence.** A real mechanical problem shows in more than one signal:
  bearing wear raises vibration *and later* temperature; a coolant failure drops pressure *and* raises
  temperature. A single signal drifting while its physical neighbours stay normal points at the sensor.
- **Readings while the machine is OFF are a free sensor test.** A stopped spindle cools to ambient; a
  temperature that keeps climbing while OFF is the sensor, not the bearing.
- **Look at what happened before a TRIP.** The trip itself only says "protection acted"; the alarm
  sequence before it says why.

## 7. Maintenance intervals

| Interval | Task |
|---|---|
| Daily | Coolant level and concentration, chip conveyor |
| Weekly | Clean coolant tank filter basket; check `spindle_vibration` trend |
| Every 2 000 h | Spindle bearing condition check (vibration spectrum, run-out) |
| Every 6 months | Coolant pump inspection (SOP-CNC-003); verify temperature sensors (SOP-GEN-004) |
| ~15 000–20 000 h or by condition | Spindle bearing replacement (SOP-CNC-002) |
