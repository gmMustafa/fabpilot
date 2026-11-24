# Eval report: run-groq-gpt-oss-20b

Eval clock 2026-11-10 12:00 UTC; prompt cache off; 51 of 51 scenarios run.

## Headline

| completed | correct | acceptable | wrong | errored | accuracy | completion |
|---|---|---|---|---|---|---|
| 51 | 40 | 3 | 8 | 0 | 40/51 (78 %) | 51/51 (100 %) |

Accuracy = correct / completed. An allowed 'unknown' is acceptable, never correct; a run that didn't complete is errored, never wrong. Read accuracy WITH completion: hard scenarios error more often, so a low completion rate flatters accuracy.

## By group

| group | n | correct | acceptable | wrong | errored |
|---|---|---|---|---|---|
| fault: bearing_wear | 8 | 7 | 0 | 1 | 0 |
| fault: belt_slippage | 4 | 2 | 0 | 2 | 0 |
| fault: clogged_filter | 7 | 6 | 0 | 1 | 0 |
| fault: coolant_pump_failure | 4 | 4 | 0 | 0 | 0 |
| fault: motor_overload | 8 | 5 | 1 | 2 | 0 |
| fault: sensor_drift | 11 | 9 | 0 | 2 | 0 |
| typical | 8 | 8 | 0 | 0 | 0 |
| mild | 8 | 6 | 1 | 1 | 0 |
| advanced | 8 | 6 | 0 | 2 | 0 |
| drift | 11 | 9 | 0 | 2 | 0 |
| control | 9 | 7 | 2 | 0 | 0 |
| trap | 10 | 5 | 2 | 3 | 0 |
| smoke | 7 | 7 | 0 | 0 | 0 |

## Manifest

- answered by: groq:openai/gpt-oss-20b (51 rows)
- routes groq:openai/gpt-oss-20b@low; temperature 0.0; agent 0650892b5f9a; data e20e6b3397d0; commits 05a7eb12, 36e74fa0, e3becc17
- one model, one configuration: comparable

## False alarms

- controls (nothing injected): invented a fault in 0/9 (0 %)
- sensor drift: blamed the MACHINE in 2/11 (18 %)

## Abstention by difficulty

| difficulty | said unknown |
|---|---|
| typical | 0/8 (0 %) |
| mild | 1/8 (12 %) |
| advanced | 0/8 (0 %) |

## Citations, work orders, tools

- citation recall (must_cite found): 85 % over 42 scenarios
  - gen-belt-slippage-conveyor-advanced: cited 0 % of its must_cite
  - gen-belt-slippage-conveyor-mild: cited 50 % of its must_cite
  - gen-motor-overload-cnc-mill-mild: cited 50 % of its must_cite
  - gen-motor-overload-injection-molder-advanced: cited 50 % of its must_cite
  - gen-motor-overload-injection-molder-mild: cited 50 % of its must_cite
  - gen-sensor-drift-m08-inlet-filter-dp: cited 0 % of its must_cite
  - gen-sensor-drift-m09-suction-strainer-dp: cited 0 % of its must_cite
  - trap-drift-question-strainer-m09: cited 50 % of its must_cite
  - trap-overload-question-belt-m04: cited 50 % of its must_cite
  - trap-strainer-question-bearing-m09: cited 50 % of its must_cite
- citations all valid (retrieved): 51/51 (100 %)
- work-order rule met: 48/51 (94 %)
- expected tools called: 51/51 (100 %)

## Cost

- provider requests: 125 total, 2.5 per scenario
- tokens: 236,979
- wall time per scenario: median 27 s, p95 47 s

| model | attempts ok | attempts failed |
|---|---|---|
| groq:openai/gpt-oss-20b | 91 | 34 |

## Judge (LLM, root cause only)

Prompt version bfd383e0146c; judged only answers with the right fault category. correct 33.

- judge says correct: 33/33 (100 %)
- agrees with the keyword grade: 33/33 (100 %)
- 33 verdict(s) from dots-studio/dots-3-note-preview:free (calibrated)

## Wrong answers

| scenario | expected | said | root cause |
|---|---|---|---|
| gen-belt-slippage-conveyor-advanced | machine_fault | sensor_fault | The drive_roller_speed sensor is likely faulty, causing the observed RPM-LOW alarm while other related signals remain no |
| gen-motor-overload-cnc-mill-mild | machine_fault | sensor_fault | The spindle_motor_current sensor is reporting an erroneous high current reading, causing the machine to slow down. |
| gen-motor-overload-injection-molder-advanced | machine_fault | sensor_fault | The pump_motor_current sensor is giving false high readings, causing isolated current spikes while other related signals |
| gen-sensor-drift-m08-inlet-filter-dp | sensor_fault | machine_fault | The inlet filter is clogged, causing a sustained rise in differential pressure and triggering PRESS-HIGH and PRESS-CRIT  |
| gen-sensor-drift-m09-suction-strainer-dp | sensor_fault | machine_fault | The suction strainer is clogged, causing differential pressure to rise above the alarm limits. |
| trap-drift-question-strainer-m09 | machine_fault | sensor_fault | The suction_strainer_dp transmitter is drifting, causing a sustained rise in differential pressure readings. |
| trap-overload-question-belt-m04 | machine_fault | unknown | Current data insufficient to distinguish overload from slippage; no motor current or vibration changes reported. |
| trap-strainer-question-bearing-m09 | machine_fault | unknown | No current pressure or differential pressure data to determine if the suction strainer is clogged; the vibration and tem |
