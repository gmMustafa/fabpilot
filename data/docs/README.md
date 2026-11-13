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
root_cause: "Suction strainer blocked with swarf after tank cleaning was skipped"
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
