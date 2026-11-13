---
doc_id: MAN-CNV-001
source_type: manual
title: "Belt Conveyor BC-22 — Operating & Maintenance Manual"
machine_types: [conveyor]
revision: "C"
effective_date: 2024-08-05
---
# Belt Conveyor BC-22 — Operating & Maintenance Manual

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-04 (Line 1 outfeed), M-07 (Line 2 parts),
M-10 (Line 3 assembly).

A conveyor rarely fails alone: when it stops, the machines feeding it fill their buffers and stop
too. A conveyor fault can therefore look like "the whole line stopped".

## 1. Safety

- Pull-cord emergency stops run along both sides. An emergency stop is **not** an isolation: use
  lockout/tagout (SOP-GEN-001) before touching the belt, rollers or the take-up.
- Never adjust tension or tracking with the belt running unless the procedure says so (SOP-CNV-008
  allows a tracking check at crawl speed with guards closed).
- In-running nips between belt and pulleys pull in hands and clothing within milliseconds.

## 2. Technical data

| Item | Value |
|---|---|
| Drive | 2.2 kW motor, helical-bevel gearbox, rubber-lagged drive roller at the head end |
| Belt | 600 mm PVC belt, screw take-up at the tail end |
| Roller speed | ~100 rpm nominal (85–115 rpm by load) |
| Protection | Motor overload relay, belt misalignment switches, pull-cords |

## 3. Monitored signals and alarm limits

Sampling every 10 s. HIGH and CRIT alarms after 30 s beyond the limit; LOW alarms after 60 s and only
in steady running (1 h after start, 2 min after any stop). 5 % hysteresis.

| Signal | Unit | Normal min | Normal max | Critical above | Low alarm below |
|---|---|---|---|---|---|
| `drive_motor_current` | A | 1.5 | 4.5 | 5.25 | — |
| `drive_motor_temp` | °C | 30 | 70 | 80 | — |
| `drive_roller_speed` | rpm | 85 | 115 | 122.5 | 85 |
| `gearbox_vibration` | mm/s | 0.3 | 2.8 | 3.425 | — |

`drive_roller_speed` is measured on the **driven roller**, not on the motor. When the belt slips,
the motor keeps turning at its speed but the measured roller speed falls.

## 4. Alarm codes

| Code | Meaning | First check |
|---|---|---|
| `RPM-LOW` | `drive_roller_speed` below 85 rpm while running | Slippage (§5.1), overload (§5.2) |
| `RPM-HIGH` / `RPM-CRIT` | Roller above 115 / 122.5 rpm | Inverter setting, speed sensor fault |
| `CURR-HIGH` / `CURR-CRIT` | `drive_motor_current` above 4.5 / 5.25 A | Jam, overload, seized idlers (§5.2) |
| `TEMP-HIGH` / `TEMP-CRIT` | `drive_motor_temp` above 70 / 80 °C | Sustained overload, blocked motor fan cover |
| `VIB-HIGH` / `VIB-CRIT` | `gearbox_vibration` above 2.8 / 3.425 mm/s | Gearbox bearings, gear wear, loose mounting (§5.3) |
| `TRIP` | Stopped by protection | Overload relay or misalignment switch |

## 5. Troubleshooting

### 5.1 Roller speed oscillating

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| `drive_roller_speed` oscillates with a period of a few minutes and dips below normal; `drive_motor_current` fluctuates with it and is **lower** than usual (less torque reaches the belt) | **Belt slippage on the drive roller**: tension too low (belt stretched), worn lagging, oil or water on the belt | Tension (belt sag, take-up position), lagging condition, contamination | Clean, re-tension (SOP-CNV-008); replace worn lagging |
| Speed dips only when heavy parts arrive | Slippage at the load limit | Load per meter | Increase tension within limits; check the load |
| Speed reading jumps between 0 and normal | Speed sensor gap or cable fault | Sensor gap (typ. 2 mm), cable | Adjust / replace the sensor (SOP-GEN-004) |

### 5.2 High drive current

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Current rises and speed falls, ends in `TRIP` | Jam (part wedged under a guide), seized return idlers | Walk the belt (after LOTO) | Clear the jam; free / replace idlers |
| Current high, speed normal, motor temperature rising over weeks | Gearbox oil low or wrong; worn bearings | Oil level, gearbox temperature | Service the gearbox |

Slippage and overload both lower the roller speed. The current tells them apart: **slippage lowers
the current, overload or a jam raises it**.

### 5.3 Gearbox vibration

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Slow rise over weeks | Gearbox bearing or gear wear | Oil sample (metal particles), spectrum | Plan a gearbox replacement |
| Step change after maintenance | Loose mounting bolts, misaligned coupling | Bolt torque | Re-tighten / re-align |

### 5.4 Belt mistracking

The belt runs to one side, the edge wears, the misalignment switch trips. Speed and current are
usually normal until the trip. Causes: material build-up on rollers, a misaligned idler, uneven
take-up. Correct by SOP-CNV-008 §4.

## 6. Diagnostic rules of thumb

- **Oscillating speed + falling current = slippage. Falling speed + rising current = overload/jam.**
- Check upstream and downstream: a conveyor stop stops the line, but a stopped upstream machine also
  leaves the conveyor running empty (low current, normal speed) — that is not a conveyor fault.
- Belts stretch most in the first weeks after installation: plan a re-tension after ~100 h.

## 7. Maintenance intervals

| Interval | Task |
|---|---|
| Daily | Visual: tracking, spillage, belt condition |
| Weekly | Clean drive and tail rollers; check belt tension |
| Monthly | Gearbox oil level, idlers turn freely |
| Yearly | Gearbox oil change, lagging inspection, sensor check (SOP-GEN-004) |
