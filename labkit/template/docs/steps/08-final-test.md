<!--
GUIDE: Step 8 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file and docs/BRANCHES.md (test runs from main).
Record the outcome in report/PROCESS.md §8 and write report/EVALUATION.md. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 8: Final test run and write-up

The test split is looked at once. Anything decided after seeing a test number is no longer a test result.

## Do

1. **Decide which systems run on test, and write down why.** A pattern that separates the effects:

   | Run | Config | Shows |
   |---|---|---|
   | T1 | `configs/baseline.yaml` | starting point |
   | T2 | last config of the adopted chain | tuning effect = T2 − T1 |
   | T3 | the structure variant on T2's retrieval, answer model, and prompt (if step 7 added one) | structure effect = T3 − T2 |

   If the structure variant's nodes use models other than T2's answer model, record that: the structure effect then includes the model change.
   If no change was adopted (T2 = T1), skip T2 and say so. Run the structure variant even if it lost on dev when the comparison is part of what you report; which system is "final" is decided on dev beforehand.
2. **Settle everything that could change the choice**: the final system, prices, the cost cap. If a price or a requirement forces a change afterwards, the test split has been seen twice; say so in the write-up.
3. **Commit the final configs before the first test run** and list them in `report/PROCESS.md` §8. Nothing changes after a test number is seen.
4. **Run from `main`** (labkit checks `--final`, not the branch), each system once:
   `labkit run --config … --split test --final` · `labkit grade --experiment test_… --final` · `labkit evaluate --experiment test_… --with-runs --final` (grade first, so the result is complete: `labkit` will not let a complete test result be overwritten)
5. **Compare**: `labkit compare <T1> <T2> --final` and `<T2> <T3> --final` (T3 vs T1 falls out as the total), then `labkit report --include-test --final`. `compare` refuses a test experiment without `--final`, like every other command.
6. **Test confirms totals.** Per-change contributions inside T2 − T1 come from the dev cards' paired results, not from extra test runs.
7. **Check the judge by hand**: read about 10 graded answers and say how many you disagree with.
8. **Write `report/EVALUATION.md`.** State as limitations: noise, dev size, judge reliability, and that the structure effect was measured on improved retrieval and could differ on the baseline's.

## Exit criteria

- [ ] The final configs were committed before the first test run; each system ran once; no change after seeing test results (or the exception is stated)
- [ ] `report/EVALUATION.md` complete: every causal claim cites question IDs, differences come with paired counts and a 95% CI, cost sits beside accuracy
- [ ] Limitations include noise, dev size, and judge reliability (10 graded answers checked by hand)
- [ ] `report/RETROSPECTIVE.md` written
