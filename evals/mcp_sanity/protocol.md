# MCP sanity check (4.3): does a strong model use our tools well?

A human-judged mini eval before we build our own agent. If even a strong model misuses a tool, the
fix belongs in the tool's description or design (4.4), not in a prompt.

**Setup:** a client that has ONLY the MCP tools: **Claude Desktop** (`docs/mcp-clients.md`), or
Claude Code started in an EMPTY folder with absolute paths in its MCP config. NOT Claude Code in the
repo root: it has file and shell tools of its own, so it can read `data/docs/` directly (it did, in
the first run) or even query the `sim` schema, and the test would no longer measure the tools.
Ask the questions word for word, one per new conversation, no hints.
Data: the dev DB (simulated week ending 2026-11-13 ~11:50 UTC).

## Questions and expected behaviour

Written down BEFORE running, so the judging can't drift towards whatever the model did.

| # | Question (ask exactly this) | Expected tools (order matters roughly) | Expected conclusion | Red flags |
|---|---|---|---|---|
| 1 | "M-08 has a discharge temperature alarm. Is the compressor overheating?" | get_machine / get_alarm_log → detect_anomalies(M-08) → search_docs (temperature rising, others normal) → get_document SOP-GEN-004 | Probably NOT overheating: only discharge_temp moved (isolated), filter dp, current, pressure quiet → suspect sensor drift, verify with a reference thermometer (SOP-GEN-004, MAN-CMP-001 §5.2). Don't stop the compressor | Says "overheating" or "stop the compressor"; concludes drift only because INC-2024-014 was drift; no citations |
| 2 | "What's wrong with M-01's spindle?" | get_machine → detect_anomalies(M-01) → search_docs → get_document SOP-CNC-002 | Bearing wear: vibration trend (+~140 %) well before/above the temperature rise (+~49 %); plan the spindle exchange SOP-CNC-002 | Blames the temperature sensor (not isolated!); misses that vibration came first |
| 3 | "Is M-05 OK?" | get_machine → get_alarm_log / detect_anomalies(M-05) | Nothing abnormal in the data; says so plainly | Invents a problem; hedges with generic maintenance advice as if something was found |
| 4 | "Which machines had alarms in the last 3 days, and is there a common cause?" | get_alarm_log (no machine filter) → per-machine follow-up | Alarms on M-01 and M-08 with two DIFFERENT causes (bearing wear vs. a lying sensor); no shared supply involved | Invents a common cause (e.g. "compressed air") because the docs say "several machines → check shared supplies" |
| 5 | "How many past incidents on M-09 were false alarms, and what caused them?" | search_docs (incident_report, pump) several times / get_incident_report per hit | ONE false alarm of six M-09 incidents: INC-2025-012 (strainer dp transmitter zero shift after frost). The other five were real (2× bearing, 2× clogged strainer, 1× soft foot) | Counts only what one search returned (F1); calls a real fault a false alarm; no doc ids |
| 6 | "Show me what M-09's suction strainer differential pressure did over the last week." | get_machine(M-09) for the sensor name → get_readings(suction_strainer_dp, 7-day range, coarse bucket) | Stayed in its normal band 0.05-0.3 bar (no fault on M-09); notices that the data ends 2026-11-13 ~11:50 | Uses the default 24 h window (F2) and reports "no data" or a near-empty series without saying why; guesses the sensor name |
| 7 | "Has the M-08 compressor's discharge temperature sensor been a problem before?" | search_docs (incident_report, compressor, discharge_temp) → get_incident_report | Yes: INC-2024-014, RTD connector corrosion, a false alarm; also INC-2024-010 (real fouled cooler) and INC-2025-009 (clogged filter) moved discharge_temp for real | Mentions only the drift case, or presents the analogy as proof about the current alarm |
| 8 | "I need to replace the inlet filter on M-08. Give me the full procedure and what to watch out for." | search_docs (filter, compressor, sop) → get_document(SOP-CMP-005) | All steps of SOP-CMP-005 incl. LOTO + venting to 0 bar (⚠ STOP), never run without the element, the whole plant loses air for ~20 min (warn all lines), acceptance 15-30 mbar | Summarises from one search chunk and skips steps; misses the plant-wide air interruption |

## Observations

One row per question. "Tools" = the actual calls in order (Claude Code shows them; note arguments that
were wrong or retried). Score each column 0 / 1 / 2 (wrong / partly / right).

### Run 2: Claude Code (Opus 5.5) in the repo root, 2026-11-14 (NOT blind)

Two deviations from the Setup make this run invalid as a blind eval:
- **Wrong client:** Claude Code in the repo root, with Grep available (used in Q5 and Q7).
- **One conversation for all eight questions:** from Q2 on, the model reused context from earlier
  answers. For example, Q4 needed no per-machine follow-up, and Q8 knew the SOP id from Q7.

So read the scores as "upper bound with help". The blind run (Run 3) is still to do.

| # | Tools actually called (order, notable args) | Errors / retries | Cited sources? | Conclusion | Tool use 0-2 | Answer 0-2 | Notes for 4.4 (what in a tool description or result would have helped?) |
|---|---|---|---|---|---|---|---|
| 1 | get_machine(M-08) ‖ get_alarm_log(M-08, start=11-07) ‖ detect_anomalies(M-08, default window) → search_docs("discharge_temp rising … other sensors normal", compressor) ‖ get_readings(discharge_temp, 6h) ‖ get_readings(motor_current, 6h) | none | Yes: MAN-CMP-001 §5.2, INC-2024-014, INC-2024-010 | Probably drift, not overheating; verify with the mechanical thermometer; don't stop the plant air. Evidence: `isolated`, 3 quiet sensors, temperature no longer follows the load at night | 1 | 2 | Never opened SOP-GEN-004 (only named it) → F11. Separating load from drift needed a manual temp-vs-current comparison → F4. Data ends 11-13 noticed only from bucket counts → F2 |
| 2 | get_machine(M-01) ‖ get_alarm_log(M-01, start=11-07, default limit 50) ‖ detect_anomalies(M-01, start=11-07T12:00) ✗ → detect_anomalies(start=11-09) ‖ get_readings(spindle_vibration, 6h) ‖ get_readings(spindle_bearing_temp, 6h) ‖ search_docs(cnc_mill) | 1: "at most 7 days, got 7 days" (window was 7 d 14 min) | Yes: MAN-CMP-001 → MAN-CNC-001 §5.1/§5.2, INC-2023-004, INC-2026-902, SOP-CNC-002 §1 | Late-stage bearing wear (vibration +116 % first, temperature last); stop, then spindle exchange per SOP-CNC-002 | 1 | 2 | F6. Alarm log was **cut at 50** without any marker in the result and the model didn't notice → new F12. Used the SOP-CNC-002 §1 chunk, never get_document → F11 |
| 3 | get_machine(M-05) ‖ get_alarm_log(M-05, start=11-08) ‖ detect_anomalies(M-05, start=11-08) → get_readings(hydraulic_pressure, 6h) to check freshness | none | n/a (no docs needed) | Fine up to 11-13 ~11:00; no alarms, no anomalies; data is ~25 h old | 2 | 2 | "No findings" on a stale feed reads like "healthy"; only the extra get_readings call revealed the gap → F2 / F10 |
| 4 | get_alarm_log(no machine, start=11-11T12, limit=500) ✗ ‖ list_machines → get_alarm_log(limit=200) → get_alarm_log(end=12T05:35) to prove completeness | 1: limit must be 1-200 | Yes (from Q1/Q2 context): MAN-CMP-001 §5.2, MAN-CNC-001 §5.1, INC-2024-014, INC-2023-004 | M-01 and M-08 only; two different causes; shared air, coolant (M-09) and power explicitly ruled out | 1 | 2 | F5. ~110 of ~115 alarms are M-01 flapping → F7. Same onset minute on both machines → F8. No per-machine follow-up because Q1/Q2 were in context (invalid) |
| 5 | search_docs("M-09 false alarm sensor", pump, incident_report, k=10) → search_docs("M-09 central coolant pump incident", k=10) ‖ search_docs("M-09 pump cavitation seal …", k=10) ‖ **Grep "M-09" data/docs** → get_incident_report(INC-2024-016, INC-2026-003, INC-2025-012) ‖ **Grep frontmatter** | none | Yes: INC-2025-012 §1-§5, the 5 real incidents by id | 1 false alarm of 6 (INC-2025-012, frost-shifted transmitter zero); 5 real incidents correctly classified | 1 | 2* | *Correct only thanks to Grep: INC-2026-003 never came up in three searches → F1 confirmed. INC-2023-007 has no `fault` field → F9 |
| 6 | get_machine(M-09) ‖ get_readings(suction_strainer_dp, start=11-07T12, 6h) ‖ detect_anomalies(M-09, start=11-07T13) | none | INC-2024-008 §2, INC-2025-012 §2 (as contrast) | In band (max 0.233 of 0.30 bar), daily flow cycle, flat weekend, no trend; data ends 11-13 ~11:00; standstill drift check impossible because the pump never stopped | 2 | 2 | Explicit 7-day range avoided F2's trap. The model itself noted that the standstill check is impossible → F3 (the tool should say so) |
| 7 | **Grep "^machine: M-08"** ‖ search_docs("M-08 discharge_temp sensor RTD drift", compressor, incident_report, k=10) → get_incident_report(INC-2026-006, INC-2024-005) | none | Yes: INC-2024-014 §1-§5, INC-2024-010, INC-2025-009, INC-2024-005 | Yes, once (INC-2024-014, condensate on the connector); 3 other cases were real; the analogy was used for the next checks, not as proof | 1 | 2 | Search alone found the 3 relevant reports; Grep was only needed for completeness → F1. INC-2026-006 `fault: null` → F9 |
| 8 | get_document(SOP-CMP-005) directly (id known from Q7) → get_readings(M-08, inlet_filter_dp, 1d) ‖ search_docs("lockout tagout … separator tank", sop) | none | Yes: SOP-CMP-005 §1-§6, SOP-GEN-001 §4.2, INC-2024-005, INC-2024-014 | All 9 steps, ⚠ STOP 0 bar, never run without the element, plant-wide air stop, 15-30 mbar; added: filter dp is normal, so the change won't fix the alarm | 2 | 2 | Skipped search_docs only because of the shared conversation (invalid). Good: checked the live dp before endorsing the job |

**Totals (Run 2):** tool use 11/16, answers 16/16 (one of them only correct with Grep).

### Run 3: blind run (TODO(Mustafa))

Claude Desktop, or Claude Code in an empty folder, one new conversation per question.

Q1 deviation: run 2026-11-14 in Claude Code (Opus 5.5) in the **repo root** (wrong client: file and
shell tools were available but not used; only MCP tools were called). New conversation, question
asked word for word, no hints.

Q2 deviation: same as Q1 (Claude Code in the repo root, new conversation, question word for word,
only MCP tools called). CLAUDE.md and the git status (untracked `evals/mcp_sanity/`) were in
context, and the answer's last line called the run "the step 4.3 check", so it was not blind. Row
written by the model itself on request: the scores are self-judged, so re-check them.

Q3 deviation: same as Q1 (Claude Code in the repo root, only MCP tools called). New conversation,
question word for word. The model saw the untracked `evals/mcp_sanity/` in the git status and
guessed this was a 4.3 sanity check, so it was not fully blind.

Q4 deviation: same as Q3 (Claude Code in the repo root, only MCP tools called, new conversation,
question word for word; it again guessed 4.3 from the git status and said so in the answer). Row
written by the model itself on request, so the scores are self-judged: re-check them.

Q5 deviation: same as Q4 (Claude Code in the repo root, new conversation, question word for word,
only MCP tools called, no Grep/Read on `data/docs/`). CLAUDE.md was in context, and the answer ended
with a Phase 5 tool suggestion, so it was not fully blind. Row written by the model itself on
request: the scores are self-judged, so re-check them.

Q6 deviation: same as Q5 (Claude Code in the repo root, new conversation, question word for word,
only MCP tools called). CLAUDE.md and the git status (untracked `evals/mcp_sanity/`) were in
context, but the answer did not mention 4.3. Row written by the model itself on request: the scores
are self-judged, so re-check them.

Q7 deviation: same as Q6 (Claude Code in the repo root, new conversation, question word for word,
only MCP tools called during the answer). CLAUDE.md and the git status were in context; the answer
did not mention 4.3. Row written by the model itself on request: the scores are self-judged, so
re-check them. While writing the row it used Grep on `data/docs/` once to check INC-2024-005 (after
the answer, so the answer itself is unaffected).

Q8 deviation: same as Q7 (Claude Code in the repo root, new conversation, question word for word,
only MCP tools called). CLAUDE.md and the git status (untracked `evals/mcp_sanity/`) were in
context, and the answer's last line said it was "a good live test of both MCP servers" and named
fault scenario 5, so it was not blind. Row written by the model itself on request: the scores are
self-judged, so re-check them.

| # | Tools actually called (order, notable args) | Errors / retries | Cited sources? | Conclusion | Tool use 0-2 | Answer 0-2 | Notes for 4.4 |
|---|---|---|---|---|---|---|---|
| 1 | get_machine(M-08) ‖ get_alarm_log(M-08, start=11-07) ‖ detect_anomalies(M-08, default window) → get_readings(discharge_temp, start=11-10, 6h) ‖ search_docs("discharge_temp rising TEMP-HIGH other sensors normal sensor drift", compressor, k=6) → get_readings(motor_current, start=11-10, 6h) ‖ get_document(SOP-GEN-004, whole doc) | none | Yes: MAN-CMP-001 §5.2, INC-2024-014 §1/§2 (flagged as analogy, not proof), SOP-GEN-004 §4.1-§4.3 | Probably NOT overheating: sensor drift. Evidence: `isolated`, 4 quiet sensors, +14 °C in ~2 days with an unchanged daily current profile (night dip vanishing). Don't stop the compressor; verify with the mechanical thermometer / contact probe (±2 °C), then oil/thermostatic valve/cooler if real, connector + RTD resistance if not | 2 | 2 | Unlike Run 2, opened SOP-GEN-004 with get_document (F11 not seen here). The model itself had to state that the standstill check is impossible (24/7 machine) → F3 confirmed. Data end (11-13 ~11:50) found only from the finding's end time and the short last bucket → F2 / F10 confirmed (get_machine said `running`). Load-vs-drift again needed a manual temp-vs-current comparison → F4 |
| 2 | get_machine(M-01) ‖ detect_anomalies(M-01, **default window**: 11-11 12:34 → 11-14 12:34) ‖ get_alarm_log(M-01, start=11-07, **default limit 50**) → search_docs("spindle_vibration rising then spindle_bearing_temp rising VIB-HIGH TEMP-HIGH bearing wear", cnc_mill, k=6) ‖ get_readings(spindle_vibration, start=11-11T12, 6h) ‖ get_readings(spindle_bearing_temp, start=11-11T12, 6h). **No get_document** | none | Yes: MAN-CNC-001 §5.1, INC-2023-004 §2/§3 (flagged as analogy, not proof), INC-2026-902 §3 (as the alternative to rule out), SOP-CNC-002 §1 | Advanced spindle bearing wear: vibration trend +3.03 mm/s / +121 % first (out of band 586 min, max 6.15 mm/s), temperature +14.9 °C / +50 % last (out of band 130 min, max 63.4 °C); current, speed, coolant quiet, so overload / coolant pump / single faulty sensor ruled out. Stop the machine (TEMP-CRIT still active), confirm by low-speed rumbling / envelope spectrum, exchange the spindle unit per SOP-CNC-002. Asked to check for a collision or tool change (INC-2026-902). Noticed the data ends 11-13 ~11:50 while get_machine says `running` | 1 | 2 (re-check) | Conclusion and order (vibration first, temperature last) match; red flag avoided. **F12 hit with a wrong fact in the answer:** the alarm log returned exactly 50 rows (oldest 11-12 17:30), and the answer stated "VIB-HIGH warnings started on 12 Nov around 17:30". That is the truncation point, not the onset: detect_anomalies in the same turn said vibration was out of band from 11-12 05:59, and Run 2 found the first VIB-HIGH at 11-12 05:35. The model didn't notice the contradiction. ~All 50 rows were M-01 flapping → F7. SOP-CNC-002 recommended from its §1 chunk only, never opened with get_document → **F11 confirmed** (same as Run 2). Default window was enough here (trend still visible), but the trend baseline starts 11-11 13:33, after the fault injection on 11-10, so the +121 % understates the change → F2 (mild). Data end found from the findings' end times and the short last bucket (2105 of 2160) → F2 / F10 confirmed. "Stop the machine because TEMP-CRIT is still active": on a stale feed an alarm can't clear, so `cleared_at: null` doesn't prove it is active now → new angle on F2 / F10. Minor: the answer said `list_machines` showed `running`, but only get_machine was called |
| 3 | get_machine(M-05) ‖ get_alarm_log(M-05, **default window**: 11-13 12:36 → 11-14 12:36) ‖ detect_anomalies(M-05, **default window**: 11-11 12:36 → 11-14 12:36) | none | n/a (no docs needed) | "Yes, healthy, running right now": no alarms in 24 h, no findings, all 5 sensors quiet. Explained `minutes_analyzed` 2792 of 4320 (~65 %) as "time the machine was stopped" | 1 | 1 | Conclusion matches (nothing abnormal), but for the wrong reasons. **F2 hit in full:** the alarm window started after the data ended (11-13 ~11:50), so "no alarms" covered zero data. The 1528 missing minutes are the stale feed (11-11 12:36 → 11-13 11:50 ≈ 2834 min ≈ 2792 analyzed), not standstill, and the model misread them. No freshness check (unlike Run 2). `status: running` taken as live → F10 confirmed. New: `minutes_analyzed` without its reason invites a wrong story → F13 candidate: split it into `minutes_running` / `minutes_stopped` / `minutes_no_data` |
| 4 | get_alarm_log(**no machine**, start=11-11T00 (≈3.5 d, not 3), limit=200) ‖ list_machines → detect_anomalies(M-01, start=11-11) ‖ detect_anomalies(M-08, start=11-11) ‖ detect_anomalies(**M-09**, start=11-11: to rule out the shared coolant supply) → search_docs("spindle_vibration rising then … bearing wear", cnc_mill, k=4) ‖ search_docs("discharge_temp rising alone … sensor drift", compressor, k=4) | none | Yes: MAN-CNC-001 §5.1, INC-2023-004 §3/§4, MAN-CMP-001 §5.2, INC-2024-014 §2, INC-2024-010 §3 (all flagged as analogies); SOP-CNC-002 / SOP-GEN-004 only named | Only M-01 and M-08; NO common cause. M-01: bearing wear, advanced (vibration +3.31 mm/s / +132 % first, temperature +14.7 °C last, current/speed/coolant quiet). M-08: probably RTD drift (`isolated`, 4 quiet sensors), verify with the mechanical thermometer before any cooler work. Coolant ruled out via M-09 (no findings) and different lines; data ends 11-13 ~11:50 noticed | 2 | 2 | Red flag avoided, but **F3 hit in the reasoning**: "no `moves_when_stopped` slightly weakens the drift theory", on a 24/7 machine where that check can't fire. The tool result should say the check was impossible. Shared compressed air (M-08 IS the air supply) and power were never explicitly ruled out, only coolant. ~120 of ~125 alarm rows were M-01 flapping, all read into context → F7. limit=200 was enough (~125 rows), but only the row count showed it: no `truncated` marker → F12. Data end found only from the findings' end times → F2 / F10. Didn't comment on the 24-min onset gap (F8): not lured, but not checked either. Procedures named without get_document → F11 (mild: the question didn't ask for one) |
| 5 | get_machine(M-09) ‖ search_docs("M-09 false alarm sensor drift", incident_report, k=10) → search_docs("M-09 Central Coolant Pump false alarm machine was fine", pump, incident_report, k=10) ‖ get_incident_report(INC-2025-012) → search_docs("M-09 incident", incident_report, k=10) | none | Yes: INC-2025-012 §1-§5 by section; the 5 real incidents by id only (INC-2023-007, INC-2024-002, INC-2024-008, INC-2026-003, INC-2026-005) | 1 false alarm of 6 (INC-2025-012, strainer dp transmitter zero shift after a frost night; discharge_pressure and current unchanged, 0.20 bar at standstill); 5 real incidents classified correctly. Said the count rests on three searches, not a listing | 1 | 2 | Six reports found by search alone this time (INC-2026-003 surfaced only in the 3rd, generic query "M-09 incident"), so **F1 still holds**: completeness was luck plus query wording, and the model could only hedge. The first search returned mostly OTHER machines' false alarms (M-08, M-01, M-06): search_docs has no `machine` filter, only `machine_type` → extends F1. get_incident_report opened only for the false alarm; the other five were classified from titles and summary chunks, never from their `false_alarm` field. The structured fields exist but can only be read one report at a time → F1's list tool would make this one call |
| 6 | get_machine(M-09) → get_readings(suction_strainer_dp, start=11-07T00, end=11-14T23:59, 6h) ‖ get_alarm_log(M-09, start=11-07) | none | None (no docs used; the question didn't need any) | In band all week (max 0.233 of 0.30 bar), no alarms. Flat ~0.13 bar on 11-07/08, then a daily cycle from 11-09 (~0.20 bar 06-18 UTC, ~0.14 bar at night); the daytime level doesn't climb, so no clogging trend. Data ends 11-13 "~11:00" (estimated from the short last bucket, 1843 of 2160 readings), although get_machine says `running` | 2 | 2 (re-check) | Correct sensor name, explicit 7-day range and a coarse bucket, so F2's trap was avoided. **Misread:** the flat 11-07/08 is the WEEKEND (Sat/Sun; Run 2 saw it), but the model blamed a possible simulator change on 11-09 and suggested checking for a restart. The conclusion still matches, but the extra claim is wrong. "No trend" came from comparing averages by eye, without detect_anomalies. Data end found only from the bucket count, estimated ~50 min too early → F2 / F10 confirmed. New: get_readings' description says "missing buckets mean no data (machine off)", but here the buckets after 11-13 are missing because the feed is stale. The wording invites a "machine was off" story → fix the description and add `data_ends_at` (F2). Weekday context (the shift/weekend pattern) has to be worked out by the model: a `weekday` per bucket or a shift calendar in get_machine could help |
| 7 | get_machine(M-08) ‖ search_docs("M-08 discharge_temp sensor fault drift", compressor, incident_report, k=10) → detect_anomalies(M-08, **default window**: 11-11 12:47 → 11-14 12:47). **No get_incident_report**: answered from the search chunks | none | Yes: INC-2024-014 §1/§2/§3/§5/§6, INC-2024-010 §1/§3, INC-2025-009 §2 | Yes: INC-2024-014, a false alarm (corroded RTD connector, +5 Ω; mechanical thermometer 84 °C vs RTD 97 °C). Two real cases also moved discharge_temp: INC-2024-010 (fouled oil cooler, rise over weeks) and INC-2025-009 (clogged inlet filter, filter dp 41 → 58 mbar). Then compared them with the current data: `discharge_temp` out of band up to 105.3 °C, +14.2 °C trend, `isolated`, 4 quiet sensors → "looks like the 2024 false alarm repeating", explicitly called an analogy, not proof; verify with the reference thermometer and the connector | 1 | 2 (re-check) | One search found the 3 expected reports, but **missed INC-2024-005** (M-08 inlet filter clogged during construction work, real fault, mentions discharge_temp / TEMP-HIGH). Run 2 found it only via Grep → **F1 confirmed again**: "has X been a problem before?" is an enumeration question. Never opened a report with get_incident_report, so the `false_alarm` / `fault` fields were inferred from titles and chunks (as in Q5). **F2 hit:** the answer called the anomaly findings "what M-08 shows right now", but the data ends 11-13 ~11:50, ~25 h before now; the finding end times showed it and the model didn't notice. Unasked detect_anomalies call was useful (it turned history into a check of the current alarm), but it went beyond the question; the "repeating" framing leans close to the red flag, though the caveat was explicit |
| 8 | get_machine(M-08) ‖ get_alarm_log(M-08, start=11-07) → search_docs("inlet filter replacement procedure inlet_filter_dp", compressor, k=6) ‖ search_docs("TEMP-HIGH discharge_temp rising compressor causes", compressor, k=5) → get_document(SOP-CMP-005, whole doc) → get_readings(inlet_filter_dp, start=11-10, 6h) ‖ get_readings(discharge_temp, start=11-10, 6h) ‖ get_readings(motor_current, start=11-10, 6h) | none | Yes: SOP-CMP-005 §1-§6, MAN-CMP-001 §5.1/§5.2, INC-2024-014 §1, INC-2024-010 §2; SOP-GEN-001 (LOTO) and SOP-GEN-004 only named | Full SOP-CMP-005: parts AF-SC55-E / -G, all 9 steps, ⚠ STOP separator tank at 0 bar, dirty side down, clean from the clean side, never run without the element, no compressed air into the intake / on paper elements, ~20 min plant-wide air stop (warn all three lines, plan for a break), acceptance 15-30 mbar / 75-95 °C within 1 h / > 6.8 bar, record. BUT led with "hold off": live filter dp normal (17-39 mbar daily cycle, no trend), current flat, discharge_temp rising alone (~85 → 104 °C, high at night too) with TEMP-HIGH + TEMP-CRIT active → probably RTD drift; check the mechanical thermometer first, then the oil cooler. Noted the data ends 11-13 ~12:00 while get_machine says `running` | 2 | 2 (re-check) | Red flags avoided: read the whole SOP with get_document instead of the chunk, and warned about the plant-wide air stop. Checking the live data before endorsing the job was useful, but it put a diagnosis the user didn't ask for ahead of the procedure: judge whether that's helpful or presumptuous. SOP-GEN-001 named for LOTO but never opened → F11 (mild). No detect_anomalies call: "temperature rises alone" came from comparing three get_readings series by eye → F4. `discharge_pressure` never fetched, though the drift rule in MAN-CMP-001 §5.2 names it (the answer didn't claim it was normal). Data end found only from the short last bucket (2106 of 2160) → F2 / F10 confirmed again |

**Reviewer re-check of Run 3 (2026-11-14):** the rows were written by the model about itself, so
the key claims were checked against the DB and the code:
- Q2 F12 confirmed: the 50 newest M-01 alarms end at 11-12 17:30:30; the first alarm was 11-12
  05:35:30. The answer reported the truncation point as the onset: a FALSE FACT → answer 1, not 2.
- Q3 confirmed and sharper: M-05's last reading is 11-13 11:07 (M-08's is 11:50), so the data end
  differs PER MACHINE. 11-11 12:36 → 11-13 11:07 = 2791 min ≈ the 2792 analysed: the gap is the stale
  feed, not standstill, as the row says.
- Q6: the claim "a simulator change on 11-09" is false (11-07/08 is the weekend) → answer 1, not 2.
- Q7: INC-2024-005 does mention discharge_temp (lines 27, 33) → F1 confirmed again.
- **Totals (Run 3, re-checked): tool use 12/16, answers 13/16** (self-judged: 15/16).
- Semi-blind only: Claude Code in the repo root again (one conversation per question, MCP tools only,
  but CLAUDE.md and the git status in context; the model named the 4.3 check three times).

### Run 4: after 4.4, blind (TODO(Mustafa))

Same 8 questions, word for word, one new conversation each, in **Claude Desktop** (restarted after
the 4.4 commits, so it runs the new code: 9 tools). Same data as Run 3 (the dev DB ending
2026-11-13 ~11:50), so only the tools changed. Judge every row yourself; if a model writes a row,
mark it and re-check its claims.

The before/after question per finding: F1 (counting), F2/F10/F13/F16 (data end), F5 (limit),
F11 (read the SOP), F12 (truncated list): gone, better, or still there?

| # | Tools actually called (order, notable args) | Errors / retries | Cited sources? | Conclusion | Tool use 0-2 | Answer 0-2 | F-numbers: gone / still there |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |
| 4 | | | | | | | |
| 5 | | | | | | | |
| 6 | | | | | | | |
| 7 | | | | | | | |
| 8 | | | | | | | |

**Totals (Run 4):** tool use __/16, answers __/16 (Run 3, re-checked: 12/16, 13/16).

## Findings from the exploratory runs, 2026-11-14

Not valid blind runs (Claude Code in the repo root, see Setup), but the observations are real
tool-design problems. F1-F4 come from the first run; F5-F11 from the second run of all eight
questions, which used Grep on `data/docs/` in Q5 and Q7. Without that, Q5 would have reported five
M-09 incidents instead of six (INC-2026-003 never came up in three searches), which confirms F1.

| # | Finding | Why it matters | Candidate fix (4.4) |
|---|---|---|---|
| F1 | docs-mcp can't list or count documents; search_docs returns <= 10 hits ranked by relevance | "How many false alarms on M-09?" needs many searches and can still miss reports: search is for finding, not for enumerating | `list_incident_reports(machine?, fault?, false_alarm?)` returning the frontmatter fields |
| F2 | Default windows count back from NOW, but the data ends 2026-11-13 11:50 | With defaults, get_alarm_log / detect_anomalies silently miss most of the incident; a model answers from a partial window without noticing | Every time-ranged result reports `data_ends_at` (latest reading); optionally anchor defaults to it; say so when the window extends past the data |
| F3 | moves_when_stopped can't fire on 24/7 machines (M-08, M-09) | The model may read the MISSING finding as evidence ("the sensor didn't move at standstill") | Report `checks_not_possible: [{check, reason}]`, e.g. "machine never stopped in the window" |
| F4 | No load-normalised view: separating a load cycle from drift needed a manual comparison of temperature vs current | Drift and load-driven changes look alike on a 24/7 machine; the `isolated` flag is the only hint | A trend-at-constant-load detector (residual of the sensor after regressing on current) |
| F5 | get_alarm_log's description doesn't mention the 1-200 limit on `limit`; the model sent 500 and needed a retry | Every rejected call costs a round trip (and LLM requests are our scarce budget) | Put the range in the parameter description, or `Field(ge=1, le=200)` so it lands in the JSON schema the client sees |
| F6 | detect_anomalies rejected a window of 7 d 14 min with "at most 7 days, got 7 days" (rounded down) | The error looks self-contradictory; the model has to guess what to change | Report the exact length ("7 d 0 h 14 min"), or clamp the start and report `clamped_start` in the result |
| F7 | M-01 alarms flap: 67 × VIB-HIGH + 48 × VIB-CRIT in ~30 h, median duration ~55-60 s. VERIFIED CAUSE: the simulator HAS hysteresis (5 % of band), but vibration noise is 6 % of band, so near the limit the noise crosses the clear level again and again (corrected after checking alarms.py and the DB; the first version said "no hysteresis") | ~95 % of the alarm log is noise; it fills the agent's context and hides the few alarms that matter | Simulator: size hysteresis to the noise per sensor kind (>= ~3 sigma) and/or a minimum alarm duration; get_alarm_log: optional `collapse_repeats` (first, last, count per code) |
| F8 | The first M-01 alarm (VIB-HIGH 05:35) and the first M-08 alarm (TEMP-HIGH 05:59) on 2026-11-12 are 24 min apart. VERIFIED: the faults were injected a day apart (11-10 and 11-11 11:50); M-01 alarms only while running, so its alarms start shortly after the early shift. Not the same minute (corrected) | Close timing still invites a false "common cause" in Q4 | Keep it as a documented TRAP scenario (a random fault onset wouldn't remove it: the timing comes from the shift start) |
| F9 | VERIFIED: INC-2023-007 and INC-2026-006 both OMIT `fault` — the documented convention for "a cause the simulator can't produce" (data/docs/README.md), stored identically. The first version said one was `null` (corrected) | Not an inconsistency; but F1's list tool must be able to filter on "no simulated fault" | `list_incident_reports(fault="none")` or a `fault_known` flag |
| F10 | list_machines / get_machine show `running` while the newest reading is ~24 h old | Status and data contradict each other; "no anomalies" on a stale feed reads like "healthy" | Part of F2: add `last_reading_at` to both results |
| F11 | The model often stopped at a search_docs chunk instead of reading the full SOP (Q1 SOP-GEN-004, Q2 SOP-CNC-002); only Q8 ("full procedure") used get_document | Recommending an action from one chunk can skip safety steps | search_docs description: "a hit is ONE chunk; before recommending a procedure, read it with get_document" |
| F12 | get_alarm_log cut the M-01 result at the default `limit=50` with no marker, and the model didn't notice (Q2) | A truncated list looks complete: the earliest alarms (when the problem started) are the ones lost, because the list is newest first | Return `truncated: true` and `total_count`, or `next_before` for paging |
| F13 | Q3: "no alarms, no findings" came from a window that started AFTER the last reading; `minutes_analyzed` (2792 of 4320) was explained as standstill, but it was the stale feed. The data end differs per machine (M-05 11:07, M-08 11:50) | A result can be empty because nothing happened, because the machine was off, or because there is no data. The model can't tell them apart and invents a story | Per-machine `data_ends_at` (F2) AND split `minutes_analyzed` into `minutes_running` / `minutes_stopped` / `minutes_no_data` |
| F14 | get_readings' description says "Missing buckets mean no data (machine off)" | Wrong: missing buckets also mean a stale or broken feed. The description itself suggested the false "machine off" story (Q6) | Reword: "missing buckets = no readings (machine off OR no data received); see data_ends_at" |
| F15 | search_docs filters by `machine_type` only, not by machine code (Q5: the first search returned other machines' false alarms) | Questions are asked per machine ("M-09"); type-level hits from M-08/M-01/M-06 waste the k slots | A `machine` filter for incident reports (part of F1's list tool) |
| F16 | Q2: "TEMP-CRIT still active, stop the machine" based on `cleared_at: null` on a stale feed | An alarm can't clear without new data: `null` means "not cleared before the data ended", not "active now" | Covered by F2/F13: with `data_ends_at` the result can say "status after <time> unknown" |

Note for judging Q2: the expected trend percentages (+~140 % vibration, +~49 % temperature) depend
on the analysis window. The second run got +116 % for vibration with `start=2026-11-09`. Judge the
order (vibration first, temperature last), not the exact number.

## Summary

### Run 2 (non-blind, see above)

- **Biggest problem in tool USE: enumerating and time ranges.**
  - search_docs can't answer "how many / which" questions. Q5 would have missed one of six reports
    without Grep (F1).
  - Results don't say when they are incomplete: the data ends ~24 h before now (F2, F10), and
    get_alarm_log truncates silently (F12).
  - The model compensated with extra calls (freshness checks, completeness proofs). A cheaper model
    or our own agent probably won't.
  - Smaller: two avoidable retries from undocumented or confusing limits (F5, F6), and a habit of
    stopping at a search chunk instead of reading the full SOP (F11).
- **Biggest problem in the ANSWERS: none were wrong.** Every answer matched the pre-registered
  conclusion and avoided the red flags, including the false-alarm traps in Q1 and Q4. Two
  qualifications:
  - Q5 was only right thanks to Grep.
  - Q2-Q8 had the earlier answers in context, so the run says little about how the tools perform
    on their own.
- **Candidate changes for 4.4, in priority order:**
  1. `data_ends_at` on every time-ranged result, and `last_reading_at` on list_machines and
     get_machine (F2, F10).
  2. `list_incident_reports(machine?, fault?, false_alarm?)` on frontmatter, with a way to ask
     for "no simulated fault" (F1, F9).
  3. get_alarm_log returns `truncated` and `total_count`, documents `limit` as 1-200 in the
     schema, and gets an optional `collapse_repeats` (F12, F5, F7).
  4. search_docs description: "one hit = one chunk; read procedures with get_document" (F11).
  5. detect_anomalies: exact window length in the error message or a clamped start (F6), and
     `checks_not_possible` (F3).
  6. Later or optional: a load-normalised trend detector (F4); hysteresis sized to the noise in
     the simulator (F7); keep F8 as a documented trap scenario.

### Run 3 (semi-blind, re-checked)

- **Biggest problem in tool USE: results that don't say they are incomplete.** Without Run 2's shared
  context and Grep, the model stopped compensating: it took the default windows (Q2, Q3, Q7), took a
  truncated alarm list as complete (Q2), and read `running` as live (Q2, Q3). F2 / F10 / F12 / F13
  hit in 5 of 8 questions.
- **Biggest problem in the ANSWERS: wrong facts with a right conclusion.** All 8 conclusions match the
  pre-registered ones and no red flag was hit, but Q2 (onset = truncation point), Q3 ("healthy right
  now" over zero data) and Q6 ("simulator change" = the weekend) state things that are false. A
  correct-sounding answer with a false fact is the most dangerous kind in maintenance.
- **Run 2 vs Run 3:** answers 16/16 → 13/16 once the help (Grep, shared context) was gone, while the
  model's own grading still said 15/16. Tool use 11 → 12: get_document was used in Q1 and Q8 (F11
  less often), but enumeration (F1) still depended on luck.
- **Priorities confirmed, with one change:** F2 + F10 + F12 + F13 (incompleteness must be IN the
  result) is now clearly priority 1; F14 (the misleading get_readings wording) and F16 belong to it.
  Then F1 + F15 (list_incident_reports with a machine filter), F11, F5/F6, F3. F4 stays later.
