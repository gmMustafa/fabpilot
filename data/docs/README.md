# FabPilot knowledge base (synthetic)

**Every document here is SYNTHETIC**: written for this demo, not taken from any real plant, vendor
or person. Free LLM endpoints may log prompts, so nothing real ever goes in here.

```
data/docs/
├── manuals/      MAN-xxx-nnn   one per machine type: data, signals, alarms, troubleshooting
├── sops/         SOP-xxx-nnn   standard operating procedures (how to do a task safely)
└── incidents/    INC-yyyy-nnn  past incident reports (what happened, root cause, fix)
```

## File format

Markdown with a YAML frontmatter block, validated by `api/app/rag/corpus.py`:

```yaml
---
doc_id: MAN-PMP-001            # stable ID; citations quote it: [MAN-PMP-001 §5]
source_type: manual            # manual | sop | incident_report
title: "Centrifugal Coolant Pump CP-75 — Operating & Maintenance Manual"
machine_types: [pump]          # cnc_mill | injection_molder | compressor | pump | conveyor
revision: "C"
effective_date: 2024-03-01
# incident reports only:
machine: M-09
fault: clogged_filter          # a simulator FaultType, or omit for a cause the simulator can't make
false_alarm: false
root_cause: "Suction strainer blocked with welding beads and scale after pipe work on the circuit"
---
```

Sections are numbered `## 1. Title`, so a citation can point at one: `[MAN-PMP-001 §5]`.

## Rules (checked by tests where possible)

1. **Consistent with the simulator.** Sensor names, normal ranges, alarm codes (`VIB-HIGH`, `PRESS-LOW`,
   `TRIP`, ...) and machine codes (`M-01`..`M-10`) are the real ones from `data/plant.yaml` and
   `simulator/src/simulator/alarms.py`. `simulator/tests/test_docs_consistency.py` fails if a manual's
   signal table or an alarm code drifts from the code.
2. **Designed for retrieval tests.** Besides the right answer, the corpus contains near-misses (bearing
   wear vs. misalignment, sensor drift vs. real overheating) and causes the simulator never produces,
   so retrieval has realistic distractors.
3. **No eval leakage.** Incident reports are dated **before** the simulated period and are written in
   varied wording. Eval scenarios (Phase 8) must not be copied from them: an eval that finds its own
   answer in an incident report measures copying, not reasoning.
4. **Fictional equipment.** Model names (VMC-500, CP-75, ...) and vendor ("Demo OEM") are invented.

## Template: incident report

Copy into `incidents/INC-<year>-<nnn>.md`. The tests in `simulator/tests/test_docs_consistency.py`
check it: the six sections in this order, a date before 2026-10-01 (the simulated period), the year in
the ID matching the date, a machine and fault that exist and fit together, and only alarm codes that
machine type can raise (see its manual §4).

```markdown
---
doc_id: INC-2026-9nn
source_type: incident_report
title: "M-xx <what happened, in a few words>"
machine_types: [<the machine's type>]
revision: "1"
effective_date: 2026-mm-dd        # the incident date
machine: M-xx
fault: <a FaultType, or delete this line for a cause the simulator can't produce>
false_alarm: <true | false>       # sensor_drift is always true
root_cause: "<one sentence a technician would write>"
---
# INC-2026-9nn <same title>

SYNTHETIC DOCUMENT (FabPilot demo). Author: <role>, <initials>.

## 1. Summary
## 2. Timeline
## 3. Evidence
## 4. Root cause
## 5. Corrective actions
## 6. Lessons learned
```
