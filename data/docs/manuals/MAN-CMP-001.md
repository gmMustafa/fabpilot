---
doc_id: MAN-CMP-001
source_type: manual
title: "Oil-Injected Rotary Screw Compressor SC-55 — Operating & Maintenance Manual"
machine_types: [compressor]
revision: "E"
effective_date: 2023-10-02
---
# Oil-Injected Rotary Screw Compressor SC-55 — Operating & Maintenance Manual

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-08 (Line 3, Screw Compressor).

The SC-55 supplies compressed air to the **whole plant**: pneumatic clamps on Line 1, mold ejectors and
grippers on Line 2, assembly tools on Line 3. Low air pressure can stop machines on any line, so when
several machines stop at once, check the compressor first.

## 1. Safety

- Before any work: stop the compressor, isolate electrically (SOP-GEN-001) **and** vent the system.
  The oil separator tank holds pressurized air and hot oil even after shutdown.
- Oil and discharge pipes run at 80–100 °C. Let the unit cool before touching the airend.
- Never remove the inlet filter element with the compressor running: debris goes straight into the
  airend.

## 2. Technical data

| Item | Value |
|---|---|
| Type | Single-stage oil-injected rotary screw, 55 kW, fixed speed, load/unload control |
| Working pressure | 7.5 bar nominal; loads at 6.8 bar, unloads at 7.6 bar |
| Free air delivery | ~9.5 m³/min at 7.5 bar |
| Cooling | Air-cooled oil cooler and aftercooler; thermostatic valve opens at ~70 °C |
| Protection | High discharge temperature shutdown at 110 °C (independent switch); motor overload relay |

## 3. Monitored signals and alarm limits

Sampling every 10 s. HIGH and CRIT alarms after 30 s beyond the limit; LOW alarms after 60 s and only
in steady running (1 h after start, 2 min after any stop). 5 % hysteresis. Pressure alarm codes are
shared by both pressure signals: the alarm message names the sensor.

| Signal | Unit | Normal min | Normal max | Critical above | Low alarm below |
|---|---|---|---|---|---|
| `discharge_pressure` | bar | 6.5 | 7.8 | 8.125 | 6.5 |
| `discharge_temp` | °C | 75 | 95 | 100 | — |
| `motor_current` | A | 55 | 100 | 111.25 | — |
| `motor_vibration` | mm/s | 0.5 | 4.5 | 5.5 | — |
| `inlet_filter_dp` | mbar | 10 | 50 | 60 | 10 |

A clean inlet filter reads about 15–30 mbar. Replace the element at 60–70 mbar at the latest.

## 4. Alarm codes

| Code | Meaning | First check |
|---|---|---|
| `PRESS-HIGH` / `PRESS-CRIT` on `inlet_filter_dp` | Inlet filter loading up / blocked | Trend over days (§5.1); replace the element (SOP-CMP-005) |
| `PRESS-LOW` on `inlet_filter_dp` | Below 10 mbar while running | Element torn, missing or not seated: unfiltered air into the airend |
| `PRESS-LOW` on `discharge_pressure` | Below 6.5 bar while running | Air demand above capacity (leak, new consumer), inlet valve not opening (§5.3) |
| `PRESS-HIGH` / `PRESS-CRIT` on `discharge_pressure` | Above 7.8 / 8.125 bar | Unload valve or pressure switch faulty; the safety valve opens at 9 bar |
| `TEMP-HIGH` / `TEMP-CRIT` | `discharge_temp` above 95 / 100 °C | §5.2. At 110 °C the independent switch shuts the compressor down |
| `CURR-HIGH` / `CURR-CRIT` | `motor_current` above 100 / 111.25 A | Running above working pressure, airend bearing damage |
| `VIB-HIGH` / `VIB-CRIT` | `motor_vibration` above 4.5 / 5.5 mm/s | Coupling, motor bearings, loose mounts (§5.4) |

## 5. Troubleshooting

### 5.1 Inlet filter differential pressure rising

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| `inlet_filter_dp` rises steadily over one to two days; `discharge_temp` creeps up and `motor_current` rises slightly | **Clogged inlet filter**: the airend sucks against the restriction, the compression ratio increases | dp trend; dust load in the compressor room (construction, open doors, cardboard handling) | Replace the element (SOP-CMP-005). Do not clean paper elements with compressed air: it tears the media |
| dp reading jumps suddenly | Plastic bag or debris on the intake grille | Look at the intake | Remove; fit a grille if it repeats |
| dp rises while the compressor is stopped or unloaded | Transmitter drift, condensate in the impulse line | Reading at standstill should be ~0 | Verify the transmitter (SOP-GEN-004) |

### 5.2 High discharge temperature

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Rises slowly together with `inlet_filter_dp` | Clogged inlet filter (§5.1) | Filter dp | Replace the element |
| Rises over weeks, filter dp normal, worse on hot days | Oil cooler fins fouled with dust or oil mist | Visual check of the cooler; temperature rise across the cooler | Clean the cooler from the clean side |
| Rises within hours after an oil change or service | Low oil level, wrong oil, thermostatic valve stuck closed | Sight glass; valve | Top up / replace the valve |
| `discharge_temp` climbs steadily for days while `inlet_filter_dp`, `motor_current` and `discharge_pressure` are all normal, and it does not fall back when the compressor is unloaded or stopped | **Temperature sensor drift** (aging RTD, corroded connector), not overheating | Compare with the compressor's independent mechanical thermometer or a contact probe on the discharge pipe | Verify and recalibrate the sensor (SOP-GEN-004). A real overheating airend never shows normal current, pressure and filter dp for days |

### 5.3 Low plant air pressure

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| `discharge_pressure` low, compressor loaded 100 % of the time, current at its normal maximum | Demand above capacity: leaks, a new consumer, a blow-off valve left open | Walk the network with an ultrasonic leak detector during a stop | Fix leaks; consider a second compressor |
| Pressure low, current low, compressor does not load | Inlet valve stuck, control fault | Load/unload state | Service the inlet valve |

### 5.4 Vibration

| Pattern | Likely cause | Check | Action |
|---|---|---|---|
| Slow rise over months | Motor or airend bearing wear | Spectrum analysis | Plan an airend overhaul |
| Sudden step | Loose mount, damaged coupling element | Bolts, coupling | Re-tighten / replace the element |

## 6. Diagnostic rules of thumb

- **Several lines stop at once → check the air first.** A compressor problem shows on every pneumatic
  consumer, so "why did Line 1, 2 and 3 all report faults at 03:10?" often has one answer: air pressure.
- **A filter problem moves three signals in one direction**: filter dp up, discharge temperature up,
  current slightly up. A temperature rising alone does not have that signature.
- The independent 110 °C switch and the mechanical thermometer are the reference when the RTD is in doubt.

## 7. Maintenance intervals

| Interval | Task |
|---|---|
| Daily | Oil level, condensate drain, `inlet_filter_dp` |
| Every 2 000 h or at dp > 60 mbar | Inlet filter element (SOP-CMP-005) |
| Every 4 000 h | Oil, oil filter, separator element |
| Yearly | Clean coolers, verify temperature and pressure sensors (SOP-GEN-004), check the safety valve |
