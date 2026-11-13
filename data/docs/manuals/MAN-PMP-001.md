---
doc_id: MAN-PMP-001
source_type: manual
title: "Centrifugal Coolant Pump CP-75 — Operating & Maintenance Manual"
machine_types: [pump]
revision: "C"
effective_date: 2024-03-01
---
# Centrifugal Coolant Pump CP-75 — Operating & Maintenance Manual

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-09 (Line 3, Central Coolant Pump).

## 1. Safety

- Isolate electrically (SOP-GEN-001) **and** hydraulically (close suction and discharge valves, relieve
  pressure at the drain) before opening the pump or the suction strainer.
- Never run the pump dry or against a closed discharge valve for more than 30 s: the water in the
  casing heats up quickly and the mechanical seal fails.

## 2. Technical data

| Item | Value |
|---|---|
| Type | End-suction centrifugal pump, single stage, close-coupled to a 7.5 kW motor, 2 900 rpm |
| Duty point | 60 m³/h at 3.2 bar discharge |
| Bearings | Two deep-groove ball bearings in the motor/bearing frame, grease-lubricated |
| Suction strainer | Basket strainer, 1.5 mm mesh, with differential pressure transmitter |
| Shaft seal | Mechanical seal; a few drops per hour are normal, a steady stream is not |

## 3. Monitored signals and alarm limits

Sampling every 10 s. HIGH and CRIT alarms after 30 s beyond the limit; LOW alarms after 60 s and only
in steady running (1 h after start, 2 min after any stop). 5 % hysteresis. Pressure alarm codes are
shared by both pressure signals: the alarm message names the sensor.

| Signal | Unit | Normal min | Normal max | Critical above | Low alarm below |
|---|---|---|---|---|---|
| `discharge_pressure` | bar | 2.5 | 4.0 | 4.375 | 2.5 |
| `bearing_temp` | °C | 30 | 65 | 73.75 | — |
| `bearing_vibration` | mm/s | 0.3 | 2.8 | 3.425 | — |
| `motor_current` | A | 8 | 15 | 16.75 | — |
| `suction_strainer_dp` | bar | 0.05 | 0.3 | 0.3625 | 0.05 |

This is a small machine (< 15 kW, ISO 10816 class I): vibration limits are stricter than for the
compressor. Above 2.8 mm/s plan maintenance; above 4.5 mm/s stop the pump.

## 4. Alarm codes

| Code | Meaning | First check |
|---|---|---|
| `PRESS-HIGH` on `suction_strainer_dp` | Strainer dp above 0.3 bar: basket loading up | Trend over days (§5.1); plan cleaning (SOP-PMP-006) |
| `PRESS-CRIT` on `suction_strainer_dp` | Above 0.3625 bar: suction starving, cavitation risk | Clean the strainer now (SOP-PMP-006) |
| `PRESS-LOW` on `suction_strainer_dp` | Below 0.05 bar while running | Torn strainer screen or missing basket: debris reaches the impeller |
| `PRESS-LOW` on `discharge_pressure` | Below 2.5 bar while running | Strainer (§5.1), cavitation (§5.3), worn impeller, open bypass |
| `PRESS-HIGH` on `discharge_pressure` | Above 4.0 bar | Consumers closed, discharge valve throttled |
| `VIB-HIGH` / `VIB-CRIT` | `bearing_vibration` above 2.8 / 3.425 mm/s | §5.2 |
| `TEMP-HIGH` / `TEMP-CRIT` | `bearing_temp` above 65 / 73.75 °C | §5.2 |
| `CURR-HIGH` | `motor_current` above 15 A | Running far right on the curve (open consumers, broken pipe), rubbing impeller |
| `TRIP` | Pump stopped by protection | Motor protection or dry-run protection; see the alarms before the trip |

## 5. Troubleshooting

### 5.1 Strainer differential pressure rising

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| `suction_strainer_dp` climbs steadily over one or two days, `discharge_pressure` slowly falls at the same time | **Clogged suction strainer** (scale, rust flakes, sludge after tank or pipe work) | dp trend; when was the basket last cleaned? Any recent work on the cooling circuit? | Clean the basket (SOP-PMP-006). Note what was in it: it tells you where the dirt comes from |
| dp jumps within minutes | A large object (rag, glove) on the screen | Inspect the basket | Remove; find where it came from |
| dp reads high while the pump is stopped | Transmitter zero drift or blocked impulse line | Compare the reading at standstill (should be ~0) | Verify the transmitter (SOP-GEN-004) |

### 5.2 Bearing vibration and temperature

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Vibration rises slowly over days and accelerates; `bearing_temp` normal at first, rising only late | **Bearing wear** | 7-day vibration trend; noise at the bearing frame; grease condition | Plan bearing replacement before temperature rises |
| Vibration high from the moment of a restart after maintenance | Misalignment or soft foot (only if the pump was dismantled), loose base bolts | Check base bolts; alignment record | Re-tighten / re-align |
| Vibration with crackling noise and fluctuating `discharge_pressure` | Cavitation (§5.3), not a bearing defect | Strainer dp and suction conditions | Fix suction first; the bearings are not the cause |
| `bearing_temp` climbs slowly while vibration, current and pressure stay normal, and it keeps climbing with the pump stopped | **Temperature sensor drift** | Contact thermometer on the bearing frame | Recalibrate / replace the sensor (SOP-GEN-004). The pump is fine |

### 5.3 Cavitation

Cavitation is vapor bubbles forming at the impeller eye because the pressure at the suction is below
the liquid's vapor pressure (available NPSH lower than the pump requires). The bubbles collapse on the
impeller and erode it.

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Gravel-like noise, fluctuating discharge pressure, high-frequency vibration | Starved suction: clogged strainer, low tank level, partly closed suction valve, hot water | Strainer dp, tank level, suction valve position, water temperature | Restore suction conditions (SOP-PMP-006). Long-term cavitation erodes the impeller: inspect at the next opening |

### 5.4 Motor current

Centrifugal pumps draw power roughly in proportion to flow. **Less flow means slightly less current**:
a clogged strainer or a throttled discharge lowers `motor_current`; a burst pipe or many open consumers
(more flow) raises it. High current at *normal* flow points at mechanical rubbing (impeller, seal) or
a motor problem.

## 6. Diagnostic rules of thumb

- Read the strainer dp first: most problems of this pump start at the suction side.
- Bearing wear changes **vibration first, temperature last**. Temperature up with normal vibration is
  either a lubrication problem (over-greasing heats a bearing within hours) or the sensor.
- A pressure or temperature value that changes while the pump is stopped is a sensor question.

## 7. Maintenance intervals

| Interval | Task |
|---|---|
| Weekly | Check strainer dp trend and seal leakage |
| Monthly or at dp > 0.3 bar | Clean the suction strainer (SOP-PMP-006) |
| Every 4 000 h | Re-grease bearings (quantity per the motor nameplate; do not over-grease) |
| Yearly | Vibration spectrum, alignment check, sensor verification (SOP-GEN-004) |
| ~25 000 h or by condition | Bearing replacement (SOP-PMP-009) |
