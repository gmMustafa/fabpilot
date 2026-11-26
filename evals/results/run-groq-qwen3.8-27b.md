# Eval report: run-groq-qwen3.8-27b

Eval clock 2026-11-10 12:00 UTC; prompt cache off; 51 of 51 scenarios run.

## Headline

| completed | correct | acceptable | wrong | errored | accuracy | completion |
|---|---|---|---|---|---|---|
| 51 | 42 | 2 | 7 | 0 | 42/51 (82 %) | 51/51 (100 %) |

Accuracy = correct / completed. An allowed 'unknown' is acceptable, never correct; a run that didn't complete is errored, never wrong. Read accuracy WITH completion: hard scenarios error more often, so a low completion rate flatters accuracy.

## By group

| group | n | correct | acceptable | wrong | errored |
|---|---|---|---|---|---|
| fault: bearing_wear | 8 | 8 | 0 | 0 | 0 |
| fault: belt_slippage | 4 | 3 | 1 | 0 | 0 |
| fault: clogged_filter | 7 | 7 | 0 | 0 | 0 |
| fault: coolant_pump_failure | 4 | 4 | 0 | 0 | 0 |
| fault: motor_overload | 8 | 7 | 1 | 0 | 0 |
| fault: sensor_drift | 11 | 4 | 0 | 7 | 0 |
| typical | 8 | 8 | 0 | 0 | 0 |
| mild | 8 | 6 | 2 | 0 | 0 |
| advanced | 8 | 8 | 0 | 0 | 0 |
| drift | 11 | 4 | 0 | 7 | 0 |
| control | 9 | 9 | 0 | 0 | 0 |
| trap | 10 | 10 | 0 | 0 | 0 |
| smoke | 7 | 6 | 0 | 1 | 0 |

## Manifest

- answered by: groq:qwen/qwen3.8-27b (51 rows)
- routes groq:qwen/qwen3.8-27b@none; temperature 0.0; agent 0650892b5f9a; data e20e6b3397d0; commits 36e74fa0, e3becc17
- one model, one configuration: comparable

## False alarms

- controls (nothing injected): invented a fault in 0/9 (0 %)
- sensor drift: blamed the MACHINE in 5/11 (45 %)

## Abstention by difficulty

| difficulty | said unknown |
|---|---|
| typical | 0/8 (0 %) |
| mild | 2/8 (25 %) |
| advanced | 0/8 (0 %) |

## Citations, work orders, tools

- citation recall (must_cite found): 93 % over 42 scenarios
  - gen-belt-slippage-conveyor-mild: cited 50 % of its must_cite
  - gen-motor-overload-injection-molder-mild: cited 50 % of its must_cite
  - gen-sensor-drift-m08-inlet-filter-dp: cited 0 % of its must_cite
  - gen-sensor-drift-m09-suction-strainer-dp: cited 0 % of its must_cite
- citations all valid (retrieved): 51/51 (100 %)
- work-order rule met: 51/51 (100 %)
- expected tools called: 51/51 (100 %)

## Cost

- provider requests: 191 total, 3.7 per scenario
- tokens: 268,945
- wall time per scenario: median 50 s, p95 86 s

| model | attempts ok | attempts failed |
|---|---|---|
| groq:qwen/qwen3.8-27b | 94 | 97 |

## Judge (LLM, root cause only)

Prompt version bfd383e0146c; judged only answers with the right fault category. correct 33.

- judge says correct: 33/33 (100 %)
- agrees with the keyword grade: 33/33 (100 %)
- 33 verdict(s) from nvidia/nemotron-3-ultra-550b-a55b:free (calibrated)

## Wrong answers

| scenario | expected | said | root cause |
|---|---|---|---|
| gen-sensor-drift-m06-hydraulic-pressure | sensor_fault | machine_fault | A genuine hydraulic system fault is causing pressure to rise above normal limits during operation and continue to drift  |
| gen-sensor-drift-m07-gearbox-vibration | sensor_fault | machine_fault | Progressive gearbox wear (bearing or gear) is causing rising vibration, indicated by a slow upward trend and out-of-band |
| gen-sensor-drift-m08-inlet-filter-dp | sensor_fault | machine_fault | The inlet filter element is clogged, causing a sustained rise in differential pressure. |
| gen-sensor-drift-m08-motor-vibration | sensor_fault | machine_fault | Mechanical degradation or imbalance in the compressor airend or motor, indicated by a sustained 90% rise in vibration me |
| gen-sensor-drift-m09-bearing-temp | sensor_fault | unknown | The evidence does not decide between a real bearing/lubrication fault and a sensor fault because the physical neighbours |
| gen-sensor-drift-m09-suction-strainer-dp | sensor_fault | machine_fault | The suction strainer is clogged, causing a steady rise in differential pressure that has reached critical levels. |
| sensor-drift-m08 | sensor_fault | unknown | The discharge temperature is genuinely high and rising, but it is impossible to distinguish between a real compressor ov |
