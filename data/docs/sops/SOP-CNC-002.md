---
doc_id: SOP-CNC-002
source_type: sop
title: "VMC-500 Spindle Unit Exchange (Bearing Wear)"
machine_types: [cnc_mill]
revision: "C"
effective_date: 2024-06-17
---
# SOP-CNC-002 VMC-500 Spindle Unit Exchange (Bearing Wear)

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-01, M-02, M-03.

## 1. Scope

Exchange of the complete spindle unit when bearing wear is confirmed (MAN-CNC-001 §5.1): rising
`VIB-HIGH` trend over days, rumbling at low speed, or late-stage temperature rise. The spindle
bearings are **not** replaced on site: precision spindle bearings need a clean room and preload
measurement. The worn unit goes to the rebuild partner; a reconditioned unit is fitted.

Duration: about 6 h including run-in. Staff: 2 maintenance technicians (mechanical), crane licence for
one of them.

## 2. Safety

- LOTO per SOP-GEN-001 before step 3 (electrical, pneumatic, coolant).
- The spindle unit weighs about 85 kg: use the lifting fixture and the hall crane, never lift by hand.
- The spindle housing may still be hot: wait 30 min after the last cycle or wear heat-resistant gloves.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Reconditioned spindle unit VMC-500 (exchange part) | SPU-VMC500-R |
| O-ring set, spindle flange and chiller connections | SPU-ORS-500 |
| Spindle lifting fixture | TL-SPL-500 |
| Torque wrench 20–200 Nm, dial indicator 1 µm, test arbor HSK-A63 | — |
| Chiller oil (top-up) | OIL-CH-32 |

## 4. Procedure

1. Record the current `spindle_vibration` and `spindle_bearing_temp` values and the 7-day trend
   (they go into the record and the rebuild partner's report).
2. Remove the tool from the spindle; move the Z axis to the change position.
3. Apply LOTO (SOP-GEN-001). ⚠ STOP: zero-energy verification done?
4. Drain the spindle chiller circuit at the spindle; cap the hoses.
5. Disconnect the spindle motor power and encoder cables, the drawbar air and the vibration and
   temperature sensor connectors. Label every connector.
6. Fit the lifting fixture, take the weight with the crane, remove the eight flange screws.
7. Lower the old unit out, place it in its transport box, cap all openings.
8. Clean the spindle seat in the head; inspect for fretting marks. ⚠ STOP: fretting or damage in the
   seat must be assessed by the supervisor before continuing.
9. Fit new O-rings; lift the new unit in; tighten the flange screws crosswise to 85 Nm.
10. Reconnect cables, air and chiller hoses; refill and bleed the chiller.
11. Measure run-out at the taper with the test arbor: at the nose and at 300 mm.
12. Remove LOTO (SOP-GEN-001 §4.5).
13. **Run-in** (grease distribution, mandatory): 15 min each at 2 000, 4 000, 6 000, 8 000 rpm;
    then 10 min at 10 000 and 12 000 rpm. Watch `spindle_bearing_temp`: if it rises above 50 °C,
    hold the current step until it falls back below 45 °C.
14. Run a test part program with a known good result.

## 5. Acceptance criteria

- Run-out at the taper ≤ 2 µm at the nose, ≤ 5 µm at 300 mm.
- After run-in: `spindle_vibration` ≤ 1.5 mm/s at 8 000 rpm (a new unit typically reads 0.6–1.0).
- `spindle_bearing_temp` stable below 45 °C at 8 000 rpm after 30 min.
- Test part within tolerance.

## 6. Record

Work order number; serial numbers of the removed and fitted units; vibration and temperature before
and after; run-out values; run-in log; technician names. Send the removed unit with the vibration
trend to the rebuild partner.
