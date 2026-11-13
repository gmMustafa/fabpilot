---
doc_id: SOP-CNC-003
source_type: sop
title: "VMC-500 Flood Coolant Pump — Inspection and Replacement"
machine_types: [cnc_mill]
revision: "B"
effective_date: 2023-11-27
---
# SOP-CNC-003 VMC-500 Flood Coolant Pump — Inspection and Replacement

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-01, M-02, M-03.

## 1. Scope

Diagnosis and replacement of the machine's internal flood coolant pump (1.1 kW immersion pump in the
400 l tank) after `PRESS-LOW` on `coolant_pressure` or a coolant-interlock `TRIP`
(MAN-CNC-001 §5.3). Also the 6-monthly inspection.

Not covered: the central coolant supply pump M-09 (see MAN-PMP-001). The machining centers have their
own pumps; M-09 does not feed them.

Duration: inspection 45 min, replacement 2 h. Staff: 1 technician (electrical qualification for
step 6).

## 2. Safety

- LOTO per SOP-GEN-001 before reaching into the tank or opening the terminal box.
- Coolant emulsion: nitrile gloves, eye protection. Swarf in the tank is sharp: cut-resistant gloves
  for the filter basket.
- Do not restart the spindle after a coolant-pump failure before the spindle has cooled below 45 °C:
  a hot spindle hit by cold coolant can lose its bearing preload.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Immersion coolant pump 1.1 kW, 400 V (exchange) | CP-IMM-110 |
| Tank filter basket (spare) | FB-VMC-400 |
| Clamp ammeter, insulation tester 500 V | — |
| Refractometer (emulsion concentration) | — |

## 4. Procedure

1. Check the alarm history: did `coolant_pressure` collapse within about a minute (pump failure) or
   fall over hours (level, filter, foam)? This decides between steps 2–4 and 5–9.
2. Check tank level and emulsion concentration (target 6–8 %). Top up if needed.
3. Remove and clean the tank filter basket. A basket full of fine chips explains slowly falling
   pressure.
4. With the machine running coolant only (spindle off), check `coolant_pressure` returns to 3–6 bar.
   If yes: done, go to §5.
5. Apply LOTO (SOP-GEN-001). ⚠ STOP: zero-energy verification done?
6. Measure the motor winding resistance (three phases equal within 5 %) and insulation to earth
   (≥ 1 MΩ). Fail → replace the pump.
7. Turn the pump shaft by hand: it must turn freely. A blocked shaft is usually a chip in the impeller.
8. To replace: disconnect, lift the pump out of the tank, fit the exchange pump with a new gasket,
   reconnect (check the direction of rotation on the first start).
9. Remove LOTO; start coolant only; check the direction of rotation and the pressure.

## 5. Acceptance criteria

- `coolant_pressure` 3–6 bar at the nozzle manifold within 30 s of switching coolant on.
- Pump current within ±10 % of the nameplate value.
- No `PRESS-LOW` during a 15-minute test program with the spindle running.

## 6. Record

Cause found (level / basket / pump), measurements from step 6, serial number of a replaced pump,
technician name.
