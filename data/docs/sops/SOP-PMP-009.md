---
doc_id: SOP-PMP-009
source_type: sop
title: "CP-75 Pump Bearing and Mechanical Seal Replacement"
machine_types: [pump]
revision: "B"
effective_date: 2024-03-01
---
# SOP-PMP-009 CP-75 Pump Bearing and Mechanical Seal Replacement

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-09.

## 1. Scope

Replacement of both motor/pump bearings when bearing wear is confirmed (MAN-PMP-001 §5.2): `VIB-HIGH`
on `bearing_vibration` with a rising trend over days, bearing noise, late temperature rise. The
mechanical seal is **always** replaced when the pump is opened: a used seal rarely seals again after
its faces have been separated.

Duration: about 4 h. Staff: 2 technicians. M-09 is out of service for the whole time: plan cooling
water for Line 2 (temporary pump or a planned Line 2 stop).

## 2. Safety

- LOTO per SOP-GEN-001, plus hydraulic isolation: close suction and discharge valves, drain the
  casing.
- The motor/pump back-pull-out unit weighs about 70 kg: use the lifting eye and a hoist.
- Bearings are mounted hot: use the induction heater only, maximum 110 °C. **Never** heat a bearing
  with a flame.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Deep-groove ball bearing, drive end, 6309-2Z/C3 | BRG-6309-C3 |
| Deep-groove ball bearing, non-drive end, 6307-2Z/C3 | BRG-6307-C3 |
| Mechanical seal kit CP-75 | MS-CP75-K |
| Casing gasket | GK-CP75 |
| Induction bearing heater, bearing puller, dial indicator | — |

## 4. Procedure

1. Record the `bearing_vibration` and `bearing_temp` trends for the last 7 days.
2. Arrange the cooling water alternative for Line 2.
3. Apply LOTO (SOP-GEN-001) and the hydraulic isolation. ⚠ STOP: zero energy and casing drained?
4. Disconnect the motor; remove the casing bolts; pull out the back-pull-out unit with the hoist.
5. Remove the impeller (note the shim position) and the old mechanical seal.
6. Pull both bearings with the puller. Inspect the shaft seats: scoring or a loose fit → shaft to the
   workshop, stop here.
7. Heat the new bearings to 100–110 °C on the induction heater and slide them onto the shaft. Let
   them cool in place.
8. Shaft run-out at the impeller seat: ≤ 0.05 mm.
9. Fit the new mechanical seal (clean hands, no grease on the seal faces), the impeller and shims.
10. Refit the unit with a new casing gasket; tighten the casing bolts crosswise.
11. Open the suction valve, vent the casing (priming), open the discharge valve.
12. Remove LOTO; check the direction of rotation; start.

## 5. Acceptance criteria

- `bearing_vibration` ≤ 1.8 mm/s after 1 h at the duty point.
- `bearing_temp` stable below 60 °C after 2 h (new bearings run warmer during the first hours).
- No visible seal leakage after 2 h.
- `discharge_pressure` 2.5–4.0 bar.

## 6. Record

Vibration and temperature before and after, bearing condition (photo of the races), shaft run-out,
seal kit batch, technician names. Keep the removed bearings for the incident report.
