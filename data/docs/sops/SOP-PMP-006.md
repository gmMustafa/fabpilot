---
doc_id: SOP-PMP-006
source_type: sop
title: "CP-75 Suction Strainer Cleaning and Cavitation Check"
machine_types: [pump]
revision: "D"
effective_date: 2024-03-01
---
# SOP-PMP-006 CP-75 Suction Strainer Cleaning and Cavitation Check

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-09.

## 1. Scope

Cleaning the suction basket strainer at `PRESS-HIGH` on `suction_strainer_dp` (above 0.3 bar),
monthly, or when cavitation is suspected (MAN-PMP-001 §5.1, §5.3).

M-09 supplies cooling water to plant consumers, including the molders' oil coolers on Line 2: plan
the stop with the Line 2 shift leader (maximum 30 min without cooling water at normal load).

Duration: 30–45 min. Staff: 1 technician.

## 2. Safety

- LOTO per SOP-GEN-001, **plus hydraulic isolation**: close the suction and discharge valves and
  drain the strainer housing before opening it. A strainer lid opened under pressure can be thrown.
- Rust flakes and metal debris in the basket can be sharp: cut-resistant gloves.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Strainer basket 1.5 mm mesh (spare, for a torn basket) | SB-CP75-15 |
| Lid O-ring | SB-CP75-OR |
| Bucket, wire brush, water hose | — |

## 4. Procedure

1. Note `suction_strainer_dp`, `discharge_pressure` and `motor_current` before the stop.
2. Inform the Line 2 shift leader. Stop the pump.
3. Apply LOTO (SOP-GEN-001); close the suction valve, then the discharge valve; open the drain.
   ⚠ STOP: housing drained and pressure-free?
4. Open the lid; lift the basket out.
5. **Look at what is in the basket before cleaning it** and note it in the record: metal swarf points
   white scale points at water treatment (hardness); rust flakes at corrosion in the circuit;
   welding beads, PTFE tape or sealant at recent pipe work; an oil film on the basket at a leaking
   molder oil cooler on Line 2 (report it at once); rags or gloves at housekeeping during tank
   cleaning.
6. Clean the basket with water and a brush. Inspect the mesh: any tear → fit the spare basket
   (a torn screen lets debris reach the impeller).
7. Fit the basket and a new lid O-ring; close the lid; close the drain.
8. Open the **suction** valve fully first, vent the casing at the vent screw until water comes out
   without air (priming), then open the discharge valve.
9. Remove LOTO; start the pump.
10. Cavitation check: listen at the pump casing. A gravel-like crackling noise and a fluctuating
    `discharge_pressure` mean the suction is still starved: check the suction valve is fully open
    and the tank level is sufficient.

## 5. Acceptance criteria

- `suction_strainer_dp` 0.05–0.15 bar with a clean basket.
- `discharge_pressure` stable in 2.5–4.0 bar; no cavitation noise.
- `motor_current` back to its usual value (it is slightly lower while the strainer is clogged).

## 6. Record

dp before and after, basket contents (type and amount, photo), mesh condition, technician name.
The same debris type twice within a month must be reported to the maintenance planner: cleaning the
basket again only treats the symptom.
