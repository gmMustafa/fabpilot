# Model comparison (8.6): 3 runs, reference groq:openai/gpt-oss-120b

- comparable: same agent code, data and temperature; only the model differs
- note: data_hash not recorded (rows from before 8.6a) in: run-groq-full; data/ provably unchanged since (git log -- data/)
- note: temperature recorded as 0.2 in run-groq-full is a mislabel (8.6b): every agent call ran at 0.0

## Results

| metric | groq:openai/gpt-oss-120b | groq:openai/gpt-oss-20b | groq:qwen/qwen3.8-27b |
|---|---|---|---|
| root-cause accuracy | 44/51 (86%) | 40/51 (78%) | 42/51 (82%) |
| completed | 51/51 (100%) | 51/51 (100%) | 51/51 (100%) |
| fault invented on a healthy machine | 0/9 (0%) | 0/9 (0%) | 0/9 (0%) |
| sensor drift blamed on the machine | 2/11 (18%) | 2/11 (18%) | 5/11 (45%) |
| misleading-question traps correct | 10/10 (100%) | 5/10 (50%) | 10/10 (100%) |
| citations valid | 100% | 100% | 100% |
| citation recall | 87% | 85% | 93% |
| work-order rule | 96% | 94% | 100% |
| judge (root cause) | 35/35 agree | 33/33 agree | pending |
| tokens / requests | 261,221 / 164 | 236,979 / 125 | 268,945 / 191 |
| median seconds per scenario | 28 | 27 | 50 |

## Correct per fault (correct / completed)

| fault | groq:openai/gpt-oss-120b | groq:openai/gpt-oss-20b | groq:qwen/qwen3.8-27b |
|---|---|---|---|
| bearing_wear | 8/8 (100%) | 7/8 (88%) | 8/8 (100%) |
| belt_slippage | 3/4 (75%) | 2/4 (50%) | 3/4 (75%) |
| clogged_filter | 6/7 (86%) | 6/7 (86%) | 7/7 (100%) |
| coolant_pump_failure | 4/4 (100%) | 4/4 (100%) | 4/4 (100%) |
| motor_overload | 6/8 (75%) | 5/8 (62%) | 7/8 (88%) |
| none | 9/9 (100%) | 7/9 (78%) | 9/9 (100%) |
| sensor_drift | 8/11 (73%) | 9/11 (82%) | 4/11 (36%) |

## Paired against groq:openai/gpt-oss-120b (McNemar, exact, two-sided)

| model | scenarios both completed | both right | only reference | only this model | both wrong | p |
|---|---|---|---|---|---|---|
| groq:openai/gpt-oss-20b | 51 | 38 | 6 | 2 | 5 | 0.289 |
| groq:qwen/qwen3.8-27b | 51 | 39 | 5 | 3 | 4 | 0.727 |

p < 0.05 would mean: a split of the discordant pairs this lopsided is unlikely if the two models were equally good. A larger p does not prove them equal; it means 51 scenarios can't tell them apart.

### Discordant scenarios: groq:openai/gpt-oss-120b vs groq:openai/gpt-oss-20b

- only groq:openai/gpt-oss-120b right: gen-motor-overload-injection-molder-advanced, trap-drift-question-strainer-m09, trap-healthy-belt-question-m04, trap-healthy-strainer-question-m09, trap-overload-question-belt-m04, trap-strainer-question-bearing-m09
- only groq:openai/gpt-oss-20b right: gen-clogged-filter-pump-mild, gen-sensor-drift-m07-gearbox-vibration

### Discordant scenarios: groq:openai/gpt-oss-120b vs groq:qwen/qwen3.8-27b

- only groq:openai/gpt-oss-120b right: gen-belt-slippage-conveyor-mild, gen-sensor-drift-m06-hydraulic-pressure, gen-sensor-drift-m08-motor-vibration, gen-sensor-drift-m09-bearing-temp, sensor-drift-m08
- only groq:qwen/qwen3.8-27b right: gen-belt-slippage-conveyor-advanced, gen-clogged-filter-pump-mild, gen-motor-overload-cnc-mill-mild
