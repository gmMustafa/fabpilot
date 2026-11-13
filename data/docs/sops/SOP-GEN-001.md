---
doc_id: SOP-GEN-001
source_type: sop
title: "Lockout/Tagout (LOTO) — Isolation of Hazardous Energy"
machine_types: [cnc_mill, injection_molder, compressor, pump, conveyor]
revision: "F"
effective_date: 2024-01-08
---
# SOP-GEN-001 Lockout/Tagout (LOTO) — Isolation of Hazardous Energy

SYNTHETIC DOCUMENT (FabPilot demo). Applies to every machine M-01 to M-10.

## 1. Scope

Mandatory before any maintenance, cleaning, adjustment or repair where a person could be hurt by an
unexpected start-up or a release of stored energy. An emergency stop, a door interlock or a stopped
program is **not** an isolation. Every other SOP starts with this one.

Duration: 10–20 min. Performed by: the qualified person who will do the work (each person applies
their own lock).

## 2. Safety

- Each person working on the machine applies a **personal lock and tag**. No shared locks.
- Only the person who applied a lock may remove it. A lock left behind at shift end is handed over
  by the procedure in §4.4, never cut off without the supervisor.
- Stored energy counts: hydraulic accumulators (molders), pressurized air and hot oil (compressor),
  water under pressure (pump), springs and tensioned belts (conveyor), DC-link capacitors in
  inverters (wait at least 5 min after switching off).

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Personal safety padlock (red, keyed individually) | LOTO-PL-01 |
| Danger tag "Do not operate" with name, date, reason | LOTO-TG-01 |
| Multi-lock hasp (when several people work) | LOTO-HS-06 |
| Voltage tester (two-pole, CAT III) | — |

## 4. Procedure

### 4.1 Prepare

1. Identify every energy source of the machine from its manual (electrical, pneumatic, hydraulic,
   thermal, mechanical, stored).
2. Inform the shift leader and the operators of the affected line.
3. Stop the machine with its normal stop sequence (end of cycle), not with the emergency stop.

### 4.2 Isolate and lock

4. Switch off the main disconnector and lock it in the OFF position with your personal lock and tag.
5. Close and lock the pneumatic supply valve; vent the machine's air circuit.
6. Release stored energy: relieve hydraulic accumulators (gauge to 0 bar), vent pressure vessels,
   close suction and discharge valves and drain pumps, secure suspended or tensioned parts.
7. Wait at least 5 min for inverter capacitors to discharge.

### 4.3 Verify zero energy — ⚠ STOP

8. Try to start the machine from the operator panel: it must **not** start. Return the control to OFF.
9. Measure the absence of voltage at the incoming terminals with the voltage tester (test the tester
   on a known live source before and after).
10. Read all pressure gauges: 0 bar.

**Do not start work until steps 8–10 are confirmed.**

### 4.4 Shift handover

11. The outgoing person explains the state of work to the incoming person; the incoming person
    applies their own lock **before** the outgoing person removes theirs.

### 4.5 Remove the isolation

12. Remove tools, refit guards, check that nobody is in the danger zone.
13. Each person removes only their own lock and tag.
14. Restore energy in reverse order; inform the operators before the first start.

## 5. Acceptance criteria

- The machine did not start on the test in step 8.
- No voltage at the incoming terminals; all pressures at 0 bar.
- After the work: all guards refitted, all locks removed by their owners, the machine starts and runs
  normally.

## 6. Record

Lock-out log: machine, date, time on/off, names of everyone who applied a lock, energy sources
isolated, result of the zero-energy test.
