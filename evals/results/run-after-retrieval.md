# Eval report: run-after-retrieval

Eval clock 2026-11-10 12:00 UTC; prompt cache off; 7 of 51 scenarios run.

## Headline

| completed | correct | acceptable | wrong | errored | accuracy | completion |
|---|---|---|---|---|---|---|
| 7 | 7 | 0 | 0 | 0 | 7/7 (100 %) | 7/7 (100 %) |

Accuracy = correct / completed. An allowed 'unknown' is acceptable, never correct; a run that didn't complete is errored, never wrong. Read accuracy WITH completion: hard scenarios error more often, so a low completion rate flatters accuracy.

## By group

| group | n | correct | acceptable | wrong | errored |
|---|---|---|---|---|---|
| fault: bearing_wear | 1 | 1 | 0 | 0 | 0 |
| fault: belt_slippage | 1 | 1 | 0 | 0 | 0 |
| fault: clogged_filter | 1 | 1 | 0 | 0 | 0 |
| fault: coolant_pump_failure | 1 | 1 | 0 | 0 | 0 |
| fault: motor_overload | 1 | 1 | 0 | 0 | 0 |
| fault: sensor_drift | 1 | 1 | 0 | 0 | 0 |
| typical | 5 | 5 | 0 | 0 | 0 |
| drift | 1 | 1 | 0 | 0 | 0 |
| control | 1 | 1 | 0 | 0 | 0 |
| smoke | 7 | 7 | 0 | 0 | 0 |

## Repeats (every completed run, oldest first)

| scenario | verdicts (model) | correct |
|---|---|---|
| gen-coolant-pump-failure-cnc-mill-typical | wrong (nemotron-3.5-super-vl:free), correct (groq:gpt-oss-120b), correct (groq:gpt-oss-120b) | 2/3 (67 %) |
| sensor-drift-m08 | wrong (nemotron-3.5-super-vl:free), correct (groq:gpt-oss-120b), correct (groq:gpt-oss-120b) | 2/3 (67 %) |

! Some scenarios were answered by DIFFERENT models (fallback): their spread mixes model differences with run-to-run variance. Compare models with one route each.

## False alarms

- controls (nothing injected): invented a fault in 0/1 (0 %)
- sensor drift: blamed the MACHINE in 0/1 (0 %)

## Abstention by difficulty

| difficulty | said unknown |
|---|---|
| typical | 0/5 (0 %) |

## Citations, work orders, tools

- citation recall (must_cite found): 100 % over 6 scenarios
- citations all valid (retrieved): 7/7 (100 %)
- work-order rule met: 7/7 (100 %)
- expected tools called: 7/7 (100 %)

## Cost

- provider requests: 15 total, 2.1 per scenario
- tokens: 36,618
- wall time per scenario: median 17 s, p95 54 s

| model | attempts ok | attempts failed |
|---|---|---|
| groq:openai/gpt-oss-120b | 4 | 2 |
| nvidia/nemotron-3.5-super-vl:free | 9 | 0 |
