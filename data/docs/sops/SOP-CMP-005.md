---
doc_id: SOP-CMP-005
source_type: sop
title: "SC-55 Compressor Inlet Filter Element Replacement"
machine_types: [compressor]
revision: "C"
effective_date: 2023-10-02
---
# SOP-CMP-005 SC-55 Compressor Inlet Filter Element Replacement

SYNTHETIC DOCUMENT (FabPilot demo). Applies to: M-08.

## 1. Scope

Replacement of the air inlet filter element at `PRESS-HIGH` on `inlet_filter_dp` (above 50 mbar),
at the latest at 60–70 mbar, or every 2 000 operating hours (MAN-CMP-001 §5.1).

**The whole plant runs on this compressor.** Plan the work for a break or the weekend, or arrange
temporary air. Expect about 20 min without compressed air.

Duration: 30 min. Staff: 1 technician.

## 2. Safety

- LOTO per SOP-GEN-001 **and** vent the compressor: the separator tank must read 0 bar.
- Never run the compressor with the filter element removed: dust and debris go straight into the
  airend.
- Do not blow the filter housing clean with compressed air into the intake.

## 3. Tools and parts

| Item | Part no. |
|---|---|
| Inlet filter element SC-55 | AF-SC55-E |
| Housing gasket | AF-SC55-G |
| Lint-free cloths, vacuum cleaner (housing) | — |

## 4. Procedure

1. Note the current `inlet_filter_dp` and the date of the last change (record).
2. Inform the shift leaders of all three lines about the air interruption.
3. Stop the compressor with the normal stop button; wait for the run-on cycle to end.
4. Apply LOTO (SOP-GEN-001), vent the system. ⚠ STOP: separator tank at 0 bar?
5. Open the filter housing cover; remove the element carefully, keeping the dirty side down so dust
   doesn't fall into the clean side.
6. Look at the element: a uniform grey coating is normal wear. Oil on the element points at oil
   carry-back from the airend (report it). Holes or a torn pleat explain a `PRESS-LOW` on
   `inlet_filter_dp`.
7. Clean the housing **from the clean side outwards** with a cloth and vacuum. Nothing may fall into
   the intake.
8. Fit the new element and gasket; close the cover.
9. Remove LOTO; start the compressor; let it load.

## 5. Acceptance criteria

- `inlet_filter_dp` 15–30 mbar at full load within 10 min (a new element).
- `discharge_temp` back in its normal band (75–95 °C) within 1 h.
- Plant air pressure back above 6.8 bar.

## 6. Record

dp before and after, element condition (photo), date and operating hours, technician name.
If the element was clogged well before 2 000 h, record the probable dust source (construction work,
open doors, cardboard handling near the intake).
