---
doc_id: SOP-GEN-007
source_type: sop
title: "Motor Overload Trip — Investigation Before Reset"
machine_types: [cnc_mill, injection_molder, compressor, pump, conveyor]
revision: "C"
effective_date: 2024-02-12
---
# SOP-GEN-007 Motor Overload Trip — Investigation Before Reset

SYNTHETIC DOCUMENT (FabPilot demo). Applies to every motor-driven machine M-01 to M-10.

## 1. Scope

What to do after a `TRIP` caused by a motor overload relay (thermal or electronic). The relay
protects the motor winding; it trips because the motor drew too much current for too long.
**Resetting without finding the cause** is the most common way a recoverable problem turns into a
burnt motor or a broken gearbox.

Rule: **one reset after investigation. A second trip within the same shift → no further reset,
escalate.**

Duration: 20–60 min. Staff: 1 technician (electrical qualification for §4.3).

## 2. Safety

- LOTO per SOP-GEN-001 before §4.2 (mechanical check) and §4.3 (electrical checks).
- Never raise the overload relay setting to "make it hold". The setting matches the motor nameplate;
  changing it removes the protection. Only the electrical supervisor may change it, with a record.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Clamp ammeter (true RMS) | REF-CA-01 |
| Insulation resistance tester 500 V | — |
| Multimeter | — |

## 4. Procedure

### 4.1 Read the history first

1. Read the alarm list and the signal trends for the 2 h **before** the trip: which current alarms
   (`CURR-HIGH`, `CURR-CRIT`) came first? Did the speed drop (`RPM-LOW`) while current rose?
2. Typical patterns:
   - current up **while** speed sags, in repeated episodes → process overload (material, tool, jam);
   - current rising slowly over days → mechanical wear or lubrication;
   - a single spike with no build-up → supply problem (phase loss) or a jam.

### 4.2 Mechanical check

3. Apply LOTO (SOP-GEN-001). ⚠ STOP: zero-energy verification done?
4. Turn the drive by hand where possible (or check for a jam: wedged part, blocked screw, seized
   idlers). It must turn freely.
5. Correct the process cause found in step 2 (tool, feed rate, material, cleared jam).

### 4.3 Electrical check

6. Insulation resistance of the motor winding to earth at 500 V: ≥ 1 MΩ required; below that → do not
   restart, motor to be checked by the electrical workshop.
7. Winding resistance of the three phases: equal within 5 %.
8. Check the three supply phases are present (a lost phase makes a motor draw about 1.7× current).

### 4.4 Reset once

9. Wait at least 15 min after the trip: a thermal relay must cool down before it can be reset,
   and so must the winding.
10. Remove LOTO; reset the relay; restart.
11. Watch the motor current with the clamp ammeter for 30 min: it must stay below the nameplate value
    in normal operation.

## 5. Acceptance criteria

- Cause identified and corrected (or, for a single unexplained trip, the checks of §4.2–4.3 passed).
- Insulation ≥ 1 MΩ; phases balanced.
- 30 min of normal operation without current alarms.
- A second trip in the same shift: machine stays stopped; escalated to the maintenance supervisor.

## 6. Record

Time of trip, alarm sequence before it, cause found, measurements (§4.3), reset time, current during
the 30-min watch, technician name.
