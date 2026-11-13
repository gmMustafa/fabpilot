---
doc_id: SOP-CNV-008
source_type: sop
title: "BC-22 Conveyor Belt Tension and Tracking Adjustment"
machine_types: [conveyor]
revision: "B"
effective_date: 2024-08-05
---
# SOP-CNV-008 BC-22 Conveyor Belt Tension and Tracking Adjustment

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-04, M-07, M-10.

## 1. Scope

Re-tensioning the belt after slippage (`drive_roller_speed` oscillating and dipping with lower
`drive_motor_current`, see MAN-CNV-001 §5.1), the scheduled re-tension ~100 h after a new belt, and
correcting belt tracking (MAN-CNV-001 §5.4).

Duration: 45–90 min. Staff: 1 technician; 2 for a tracking check (one at the panel, one at the belt).

## 2. Safety

- LOTO per SOP-GEN-001 for every tension adjustment and every cleaning step.
- The tracking check in §4.3 is the only step done with the belt moving: crawl speed (jog mode),
  guards closed, adjustment only at the take-up screws outside the guard, no loose clothing.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Tape measure, marker | — |
| Lagging repair kit (rubber lagging, adhesive) | LAG-BC22-K |
| Belt cleaning agent (non-solvent) | CL-BELT-01 |

## 4. Procedure

### 4.1 Clean and inspect

1. Apply LOTO (SOP-GEN-001). ⚠ STOP: zero-energy verification done?
2. Clean the belt underside and the drive roller: oil, coolant mist or water on the belt are a common
   cause of slippage, and re-tensioning a dirty belt only hides it.
3. Inspect the drive roller lagging: worn smooth or with missing pieces → repair with the lagging kit
   before tensioning.

### 4.2 Tension (elongation method)

4. With the belt slack, draw two marks exactly 1 000 mm apart on the belt edge.
5. Turn both take-up screws at the tail **by the same number of turns**, alternately, until the marks
   are 1 003 mm apart (0.3 % elongation, the target for this PVC belt).
6. Measure on both belt edges: a difference means the take-up is uneven, which causes mistracking.

### 4.3 Tracking check

7. Remove LOTO; run in jog mode.
8. Watch the belt at the tail roller for three full belt revolutions. If it runs to one side: on the
   side the belt runs **towards**, tighten that take-up screw by a quarter turn; wait three revolutions
   before the next correction.
9. Run at normal speed with load for 15 min.

## 5. Acceptance criteria

- Elongation 0.3 % (±0.05 %) on both edges.
- Belt runs centred within ±10 mm at the tail roller.
- With normal load: `drive_roller_speed` steady within 85–115 rpm, no oscillation; `drive_motor_current`
  back to its usual level (1.5–4.5 A).

## 6. Record

Cause found (contamination / stretched belt / worn lagging), elongation before and after, number of
take-up turns, technician name. A belt needing re-tension more than twice a year should be replaced.
