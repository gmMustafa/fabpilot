# Retrieval eval

2026-11-14 00:13 UTC · 20 questions · k=5 · model `fastembed-0.9.1:Qdrant/bge-small-en-v1.5-onnx-Q`

One question = 5 points: small differences are noise.

| variant | hit@5 | doc-hit@5 | MRR |
|---|---|---|---|
| vector | 90% | 95% | 0.81 |
| vector-noprefix | 90% | 95% | 0.80 |
| text | 95% | 100% | 0.72 |
| hybrid | 95% | 95% | 0.82 |
| hybrid-cap2 | 95% | 95% | 0.82 |

hit@5 per category:

| category | n | vector | vector-noprefix | text | hybrid | hybrid-cap2 |
|---|---|---|---|---|---|---|
| code | 4 | 50% | 50% | 100% | 75% | 75% |
| symptom | 6 | 100% | 100% | 100% | 100% | 100% |
| procedure | 4 | 100% | 100% | 100% | 100% | 100% |
| false_alarm | 3 | 100% | 100% | 100% | 100% | 100% |
| cross_machine | 3 | 100% | 100% | 67% | 100% | 100% |

Rank of the first relevant section (- = not in the top k):

| question | category | vector | vector-noprefix | text | hybrid | hybrid-cap2 |
|---|---|---|---|---|---|---|
| Q01 | code | 1 | 1 | 2 | 1 | 1 |
| Q02 | symptom | 5 | 5 | 2 | 2 | 2 |
| Q03 | code | - | - | 1 | 3 | 3 |
| Q04 | code | - | - | 1 | - | - |
| Q05 | code | 1 | 1 | 1 | 1 | 1 |
| Q06 | symptom | 2 | 2 | 1 | 2 | 2 |
| Q07 | symptom | 1 | 1 | 1 | 1 | 1 |
| Q08 | symptom | 1 | 1 | 3 | 1 | 1 |
| Q09 | symptom | 1 | 1 | 1 | 1 | 1 |
| Q10 | symptom | 1 | 1 | 3 | 2 | 2 |
| Q11 | procedure | 1 | 1 | 3 | 1 | 1 |
| Q12 | procedure | 1 | 1 | 1 | 1 | 1 |
| Q13 | procedure | 1 | 1 | 2 | 1 | 1 |
| Q14 | procedure | 2 | 4 | 1 | 1 | 1 |
| Q15 | false_alarm | 1 | 1 | 1 | 1 | 1 |
| Q16 | false_alarm | 1 | 1 | 1 | 1 | 1 |
| Q17 | false_alarm | 1 | 1 | 2 | 1 | 1 |
| Q18 | cross_machine | 1 | 1 | - | 1 | 1 |
| Q19 | cross_machine | 1 | 1 | 1 | 1 | 1 |
| Q20 | cross_machine | 1 | 1 | 2 | 2 | 2 |
