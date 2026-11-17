<!--
GUIDE: Step 3 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file.
Record the outcome in report/PROCESS.md §3. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 3: Measure noise

Models answer and grade differently on a rerun. The number of questions that flip with nothing changed is the smallest difference worth acting on.

## Do

Two runs, both on dev with the baseline config:

- **(a) Total noise (generation + judging):** run the identical config again under a new name, grade it. Name it `dev_000_baseline_r<N>`: `labkit check` finds a rerun only by that pattern.
  `labkit run --config configs/baseline.yaml --split dev --name dev_000_baseline_r2` · `labkit grade --experiment dev_000_baseline_r2` · `labkit evaluate --experiment dev_000_baseline_r2`
- **(b) Judge noise alone:** grade the baseline's existing answers again. The suffix must start with `__regrade`; `labkit grade` refuses any other.
  `labkit grade --experiment dev_000_baseline --suffix __regrade1`

Compare each pair with `python -m labkit.eval.paired <grades A> <grades B> --max-score <max_score>`. The grade files are in `out/grades/`; copy a regrade into `results/dev_000_baseline/` if a decision uses it (as `results/dev_000_baseline/grades__regrade1.jsonl`).

**The noise figure is (a)**: the number of questions whose score differs between the two runs (`python -m labkit.eval.paired` prints `<B> better N / <A> better M / same K`, with the two file names for A and B; the figure is N + M). Later comparisons pass it as `--noise` (`labkit compare`, the headroom check). (b) says how much of it is the judge; use it to decide whether a second judge run or a stricter rubric is worth the calls.

`labkit check` accepts (a) only as the baseline run again: same config (`config_hash` and config file), same input files (an edit that was put back before evaluating still shows as a different hash), the same `--sets` and so the same questions, nothing failed, evaluated, and not a `--retrieval-only` run; and (b) only if it graded every answer the baseline graded (a file left empty by a judge that raised does not count).

Changing anything between the two runs (a prompt, `k`, a model version) does not measure noise. In the previous project the "noise" figure came from two runs with different prompts, so it mixed the prompt effect into the noise.

Some models ignore `temperature`; if yours does, (a) is not optional.

## Exit criteria

- [ ] Both noise runs exist: a rerun of the baseline and a regrade of its answers <!-- auto: noise_runs_exist -->
- [ ] Noise recorded in `report/PROCESS.md` §3 as "(a) X of N changed, (b) Y of N changed"
- [ ] The noise figure (a) is what later comparisons pass as `--noise`
