# Eval report: run-groq-full

Eval clock 2026-11-10 12:00 UTC; prompt cache off; 51 of 51 scenarios run.

## Headline

| completed | correct | acceptable | wrong | errored | accuracy | completion |
|---|---|---|---|---|---|---|
| 51 | 44 | 1 | 6 | 0 | 44/51 (86 %) | 51/51 (100 %) |

Accuracy = correct / completed. An allowed 'unknown' is acceptable, never correct; a run that didn't complete is errored, never wrong. Read accuracy WITH completion: hard scenarios error more often, so a low completion rate flatters accuracy.

## By group

| group | n | correct | acceptable | wrong | errored |
|---|---|---|---|---|---|
| fault: bearing_wear | 8 | 8 | 0 | 0 | 0 |
| fault: belt_slippage | 4 | 3 | 0 | 1 | 0 |
| fault: clogged_filter | 7 | 6 | 0 | 1 | 0 |
| fault: coolant_pump_failure | 4 | 4 | 0 | 0 | 0 |
| fault: motor_overload | 8 | 6 | 1 | 1 | 0 |
| fault: sensor_drift | 11 | 8 | 0 | 3 | 0 |
| typical | 8 | 8 | 0 | 0 | 0 |
| mild | 8 | 5 | 1 | 2 | 0 |
| advanced | 8 | 7 | 0 | 1 | 0 |
| drift | 11 | 8 | 0 | 3 | 0 |
| control | 9 | 9 | 0 | 0 | 0 |
| trap | 10 | 10 | 0 | 0 | 0 |
| smoke | 7 | 7 | 0 | 0 | 0 |

## Manifest

- answered by: groq:openai/gpt-oss-120b (51 rows)
- routes groq:openai/gpt-oss-120b@low; temperature 0.2; agent 0650892b5f9a; data not recorded; commits 6ffea189, 708e4cd9
- 48 row(s) recorded before manifests (answering model only)
- one model, one configuration: comparable

## Repeats (every completed run, oldest first)

| scenario | verdicts (model) | correct |
|---|---|---|
| gen-bearing-wear-cnc-mill-typical | correct (groq:gpt-oss-120b), correct (groq:gpt-oss-120b) | 2/2 (100 %) |
| trap-drift-question-strainer-m09 | correct (groq:gpt-oss-120b), correct (groq:gpt-oss-120b) | 2/2 (100 %) |
| trap-overload-question-belt-m04 | correct (groq:qwen3.8-27b), correct (groq:gpt-oss-120b) | 2/2 (100 %) |
| trap-strainer-question-bearing-m09 | correct (groq:gpt-oss-120b), correct (groq:gpt-oss-120b) | 2/2 (100 %) |

! Some scenarios were answered by DIFFERENT models (fallback): their spread mixes model differences with run-to-run variance. Compare models with one route each.

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

- citation recall (must_cite found): 87 % over 42 scenarios
  - gen-sensor-drift-m07-gearbox-vibration: cited 0 % of its must_cite
  - gen-sensor-drift-m08-inlet-filter-dp: cited 0 % of its must_cite
  - gen-sensor-drift-m09-suction-strainer-dp: cited 0 % of its must_cite
  - gen-belt-slippage-conveyor-advanced: cited 0 % of its must_cite
  - gen-clogged-filter-pump-mild: cited 50 % of its must_cite
  - gen-motor-overload-cnc-mill-mild: cited 50 % of its must_cite
  - gen-motor-overload-injection-molder-mild: cited 50 % of its must_cite
- citations all valid (retrieved): 51/51 (100 %)
- work-order rule met: 49/51 (96 %)
- expected tools called: 51/51 (100 %)

## Cost

- provider requests: 164 total, 3.2 per scenario
- tokens: 261,221
- wall time per scenario: median 28 s, p95 43 s

| model | attempts ok | attempts failed |
|---|---|---|
| groq:openai/gpt-oss-120b | 93 | 71 |

## Judge (LLM, root cause only)

Prompt version bfd383e0146c; judged only answers with the right fault category. correct 35.

- judge says correct: 35/35 (100 %)
- agrees with the keyword grade: 35/35 (100 %)
- 35 verdict(s) from dots-studio/dots-3-note-preview:free (calibrated)

## Wrong answers

| scenario | expected | said | root cause |
|---|---|---|---|
| gen-sensor-drift-m07-gearbox-vibration | sensor_fault | unknown | Insufficient evidence to determine if the rising gearbox vibration is a real mechanical issue or a sensor drift; additio |
| gen-sensor-drift-m08-inlet-filter-dp | sensor_fault | machine_fault | The inlet filter is clogged, causing a sustained high differential pressure. |
| gen-sensor-drift-m09-suction-strainer-dp | sensor_fault | machine_fault | The suction strainer is clogged, causing elevated differential pressure. |
| gen-belt-slippage-conveyor-advanced | machine_fault | sensor_fault | The drive_roller_speed sensor is likely faulty, giving a low RPM reading while all related signals remain normal. |
| gen-clogged-filter-pump-mild | machine_fault | no_fault | The motor is not failing; the abnormal suction strainer differential pressure is due to a clogged suction strainer, not  |
| gen-motor-overload-cnc-mill-mild | machine_fault | sensor_fault | Spindle motor current sensor is giving false high readings. |
