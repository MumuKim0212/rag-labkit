<!--
GUIDE: Step 2 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file.
Record the outcome in report/PROCESS.md §2. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 2: Simplest baseline, end to end

Every later change is measured against this run, so keep it plain and finish it before improving anything.

## Do

1. **Build the simplest system that can answer and abstain**: one chunk size, vector search, a small `k`, a short answer prompt that can say "not found", citations mapped from result numbers to file and page in code. No agent, no reranker.
2. **Register it** with `@register_system("name")` in a module listed under `modules:` in `labkit.yaml`.
   - Put your code in a package under `src/` (for example `src/myproject/systems.py`; `labkit.yaml` already has `pythonpath: [src]`) and list its dotted name under `modules:`. `modules: []` works until then.
   - The result contract is in `labkit.systems`: `status`, `answer`, `context` (exactly what the answer step received), `citations`, `steps` with every model call recorded by `labkit.cost.record_call`. labkit counts tokens, calls and latency itself from `steps["calls"]` and the clock.
   - A result is a failed question when its recorded calls cannot be costed (a call without a `model`, a token count that is not a number) or its context units lack a text `source` and a whole-number `page`.
   - It also needs `retrieve(question, scope, steps=None)`, the search alone: the LLM-free runs of step 6 (`--retrieval-only`) are refused for a system without it.
   Record the candidates before reranking or cutting to `k` as `steps["pool"]` from the start: step 5 needs it, and adding it after the baseline has been evaluated means running the baseline again (an evaluated `results/` folder is never overwritten).
   If the system keeps an index: list the settings that change it under `index_keys` in `labkit.yaml`, name the index folder by `labkit.index.index_key`, report the key it used as `steps["index_key"]`, and implement `ready()` so a missing index stops the batch (`labkit.index`).
3. **Write `configs/baseline.yaml`** with every setting of the baseline, including `system` and `judge`. Create `prompts/judge_v1.md` and `prompts/answer_v1.md`, the files `configs/baseline.yaml` names, before the first run; labkit refuses a run while `judge.prompt` names a missing file (`answer.prompt` is your system's key, which labkit does not read). Treat it as frozen from the first real run; labkit enforces it once an experiment that used it has been evaluated (index-affecting keys change every comparison).
4. **Smoke test one question**: `labkit run --config configs/baseline.yaml --split dev --limit 1`. Read the logged result (answer, context, citations, steps) with `labkit show dev_000_baseline` before running the rest. The smoke rows are kept in `out/runs.db` but belong to no result: the full run repeats that question, and a prompt fixed after reading the smoke output is fine.
5. **Run, grade, evaluate** dev, then build the comparison table:
   `labkit run … --split dev` · `labkit grade --experiment dev_000_baseline` · `labkit evaluate --experiment dev_000_baseline --with-runs` · `labkit report`
   `labkit evaluate` prints `complete`. It is false while a dev question never ran, failed on every attempt, or is not graded, and the scores then cover only part of dev (a failed question is not counted as 0). Run the same command again (it retries only the failed ones), grade, and `evaluate --overwrite`. The baseline check below passes only when `complete` is true and the experiment really is the baseline: the dev split, `configs/baseline.yaml` unchanged from itself (no changed keys), every set (no `--sets`), an answer run, with its input files and `labkit.yaml` scoring settings still as they were. A variant or a subset run under the name `dev_000_baseline` does not pass.
6. Record the baseline in `report/PROCESS.md` §2 and its calls in the ledger (§10); the experiment table in §6.1 is for cards.

## Exit criteria

- [ ] Baseline dev run, grades, and summary saved in `results/dev_000_baseline/` <!-- auto: baseline_evaluated -->
- [ ] Retrieval metrics for the baseline on dev saved per question <!-- auto: baseline_metrics_saved -->
- [ ] `configs/baseline.yaml` committed and treated as frozen <!-- auto: baseline_config_committed -->
- [ ] Experiment name and headline numbers recorded in `report/PROCESS.md` §2
