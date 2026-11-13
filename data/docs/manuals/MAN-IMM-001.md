---
doc_id: MAN-IMM-001
source_type: manual
title: "Hydraulic Injection Molding Machine HM-200 — Operating & Maintenance Manual"
machine_types: [injection_molder]
revision: "B"
effective_date: 2025-01-20
---
# Hydraulic Injection Molding Machine HM-200 — Operating & Maintenance Manual

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-05, M-06 (Line 2, Molding).

## 1. Safety

- The mold area is guarded by a door with a dual-channel interlock. Never bridge it. For work in the
  mold area or on the hydraulics: lockout/tagout (SOP-GEN-001) and **relieve the hydraulic
  accumulator** (pressure gauge must read 0 bar).
- Barrel and nozzle run at 200–250 °C. Molten plastic can spit from the nozzle when purging: face shield
  and heat-resistant gloves.
- Hydraulic oil leaks at 150+ bar can inject oil through the skin. Never feel for leaks by hand.

## 2. Technical data

| Item | Value |
|---|---|
| Clamping force | 200 t, toggle clamp |
| Hydraulics | Fixed-displacement pump driven by a 37 kW motor; working pressure up to 175 bar |
| Plasticizing | Screw Ø 45 mm, hydraulic screw motor, 40–200 rpm |
| Barrel heating | 5 zones with ceramic heater bands, type J thermocouples; zone 2 is monitored for PP/ABS at 200–240 °C |
| Oil cooling | Plate heat exchanger on the plant cooling water circuit (supplied from M-09); oil 35–55 °C |
| Protection | Pump motor overload relay; oil over-temperature switch at 65 °C |

## 3. Monitored signals and alarm limits

Sampling every 10 s. HIGH and CRIT alarms after 30 s beyond the limit; LOW alarms after 60 s and only
in steady running (1 h after start, 2 min after any stop). 5 % hysteresis. Note: temperatures have **no
LOW alarm**, so a failed heater band shows up as part defects first (§5.3), not as an alarm.

| Signal | Unit | Normal min | Normal max | Critical above | Low alarm below |
|---|---|---|---|---|---|
| `barrel_temp_zone2` | °C | 200 | 240 | 250 | — |
| `hydraulic_oil_temp` | °C | 35 | 55 | 60 | — |
| `hydraulic_pressure` | bar | 100 | 175 | 193.75 | 100 |
| `pump_motor_current` | A | 25 | 65 | 75 | — |
| `screw_speed` | rpm | 40 | 200 | 240 | 40 |

## 4. Alarm codes

| Code | Meaning | First check |
|---|---|---|
| `CURR-HIGH` / `CURR-CRIT` | `pump_motor_current` above 65 / 75 A | Pattern with `screw_speed` (§5.1) |
| `RPM-LOW` | `screw_speed` below 40 rpm while running | Plasticizing overload (§5.1), cold barrel |
| `RPM-HIGH` / `RPM-CRIT` | `screw_speed` above 200 / 240 rpm | Speed setting, proportional valve fault |
| `PRESS-LOW` | `hydraulic_pressure` below 100 bar while running | Pump wear, relief valve, leak (§5.2) |
| `PRESS-HIGH` / `PRESS-CRIT` | `hydraulic_pressure` above 175 / 193.75 bar | Relief valve setting, blocked return line |
| `TEMP-HIGH` / `TEMP-CRIT` on `hydraulic_oil_temp` | Oil above 55 / 60 °C | Cooling water flow (§5.4) |
| `TEMP-HIGH` / `TEMP-CRIT` on `barrel_temp_zone2` | Zone 2 above 240 / 250 °C | Shear heating, controller or thermocouple fault (§5.3) |
| `TRIP` | Stopped by protection | Pump motor overload relay or oil over-temperature switch: read the alarms before it |

## 5. Troubleshooting

### 5.1 Pump motor current high, screw slows down

In healthy operation `pump_motor_current` follows the demand: it rises with screw speed and injection
pressure. A current rise **while** the screw slows down breaks that pattern.

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Episodes of high current with `screw_speed` sagging at the same time, temperatures up during the episodes; repeated episodes end in `TRIP` | **Motor overload during plasticizing**: material too cold or too viscous (wrong grade, wet regrind, barrel profile set too low), screw or check ring damage | Material batch and drying log, barrel set points, back pressure setting | Correct the material / profile. After a trip follow SOP-GEN-007; do not reset repeatedly |
| Current high in every cycle, speed normal | Higher injection pressure demand: new mold, cold mold | Process sheet | Adjust the process |
| Current slowly higher over months | Pump internal wear (lower efficiency) | Pump flow test | Plan a pump overhaul |

### 5.2 Low hydraulic pressure

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Pressure low and slow movements, worse when the oil is hot | Pump wear or internal leakage | Flow / pressure test | Overhaul the pump |
| Sudden drop | Relief valve stuck open, burst hose | Visual inspection, oil level | Repair (relieve the accumulator first) |

### 5.3 Barrel temperature problems

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Zone reads normal but parts show unmelts / short shots | Heater band failed, controller compensates with the neighbouring zones | Clamp-on ammeter on each band | Replace the band |
| Zone above set point by a constant offset, rising slowly over days, all other signals normal | Thermocouple drift or a thermocouple pulled out of its well | Compare with a handheld probe in the zone's measuring hole | Re-seat or replace the thermocouple (SOP-GEN-004) |
| Zone overshoots during fast cycles | Shear heating from high screw speed | Screw speed and back pressure | Reduce speed / back pressure |

### 5.4 Hydraulic oil temperature high

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Oil temperature rises on both molders (M-05 and M-06) at the same time | Cooling water supply: M-09 pump, strainer, valve | Check M-09 first (MAN-PMP-001) | Restore cooling water |
| Rises on one machine only, over weeks | Heat exchanger fouled (scale on the water side) | Temperature difference across the exchanger | Descale / clean the exchanger |
| Rises during long high-pressure cycles only | Process at the limit | Cycle profile | Review the process |

## 6. Diagnostic rules of thumb

- **Current vs. speed is the overload signature.** Both rising together = more demand. Current up while
  speed falls = the drive is struggling.
- A problem on **both molders at once** almost always comes from something they share: material
  batch, cooling water, compressed air (MAN-CMP-001), power supply.
- A drifting thermocouple only moves its own zone; real overheating moves oil temperature and current
  too.

## 7. Maintenance intervals

| Interval | Task |
|---|---|
| Daily | Oil level, leaks, material dryer |
| Monthly | Check heater bands (ammeter), clean the oil cooler water strainer |
| Every 4 000 h | Oil analysis, filter change |
| Yearly | Verify thermocouples and pressure transmitters (SOP-GEN-004); check screw and check ring wear |
