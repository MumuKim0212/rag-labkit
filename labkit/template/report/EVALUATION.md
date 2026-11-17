<!--
GUIDE: Final evaluation. Written after step 8; a reader should get the conclusion and its evidence from this
document alone.
- The process and how decisions were reached are in PROCESS.md; link there.
- Every number states its question count and split. Numbers used to pick a setup are dev values; say so.
- Do not give means alone: give paired counts (better / worse / same) and a 95% CI. A difference smaller than
  the noise (PROCESS.md §3) is not a difference.
- Every sentence that says "why" carries the question IDs and data it rests on (cited chunks, retrieval metrics).
- Put cost (time, tokens, LLM calls) next to accuracy.
- Separate untested improvement ideas from tried ideas that did not help.
Keep guide comment in place after filling in.
-->

# Evaluation

Process and decisions: [PROCESS.md](PROCESS.md). All numbers are test (n = _N_) unless marked dev.

## 1. Result in one table

| System | Config | Score | Fully correct | Status match | USD/question | p90 s |
|---|---|---|---|---|---|---|
| T1 | | | | | | |

## 2. What the differences are

| Comparison | Better / worse / same | Mean diff | 95% CI | Noise |
|---|---|---|---|---|
| T2 vs T1 | | | | |

## 3. Which changes mattered

_From the dev cards' paired results (not extra test runs): change, effect, cost, card._

## 4. What did not work, and why

_Tried ideas with no effect, each with the question IDs that show why._

## 5. Remaining failures

_Counts per bucket on test, with question IDs; what would be needed to fix them._

## 6. Ideas not tested

_Clearly separated from section 4._

## 7. Limitations

- Noise: _…_
- Dev size: _…_
- Judge reliability: _10 graded answers read by hand, N disagreements_
- _Structure effect measured on improved retrieval only; test seen more than once?_
