<!--
GUIDE: Entry point for every agent session in this repo (loaded on every task, so keep it short).
Holds only: where things are, what to read first per task, and rules whose violation cannot be undone.
Procedure, values, and formats live in the documents listed below: add them there, not here.
Keep guide comment in place after editing.
-->

# _Project name_: RAG experiment repo

_One line: what the system answers, over which corpus._ Deadline: _date_.

## Start of every task

1. Run `labkit status` for the current step, then read the status table at the top of `report/PROCESS.md` for who is doing what. Do the current step only; `labkit check` lists what must hold. You may claim a criterion (`labkit tick`, with evidence); only the owner approves a step (`labkit approve`). Never approve a step yourself. (The owner starts a step with `/labkit-next`, which follows the same rules.)
2. Claim your task there before starting: add or update a row with your GitHub username and commit/push only that change to `main` so teammates see it (only when the user asks you to commit). Do not take a task someone else holds. Do experiment-card work (step 6) on a `<github-user>/<card-slug>` branch merged by PR (`docs/BRANCHES.md`); work for steps 1–5 may be committed on `main` (`docs/WORKFLOW.md`, "The eight steps").
3. Read `docs/RESUME.md`, the handoff from the last session (rewrite it when you stop), then the documents for your task type:

| Task | Read first |
|---|---|
| Any step | `docs/WORKFLOW.md`, then only the file for your step in `docs/steps/` |
| An experiment or batch run | the step file above, plus `experiments/_TEMPLATE.md` and `docs/BRANCHES.md` |
| Golden set, data conversion | `golden/README.md` ("Format" to write items, "Review checklist" to check them, "Splits"); log the review in `golden/review_log.md` |
| Exporting results | `results/README.md` ("Files per experiment", "Rules") |
| Reading or comparing results | `results/README.md` ("summary.json", "Comparison views") |
| Settings, model IDs | `configs/baseline.yaml`, `configs/README.md` ("Writing a variant", "Keys labkit reads"; values live only in configs) |
| Prices | `configs/pricing.yaml` |
| Plan, hypotheses, schedule | `report/PLAN.md` |
| Prompts | `prompts/README.md` and the header comment of each prompt file |
| Project-wide settings (golden files, behaviors, modules, measures) | `labkit.yaml` |
| Pipeline code | the module docstrings; the result contract is `labkit.systems` |

Where a section is named, read that section; the rest of the file is reference for other tasks.

When the task is done, update your status row and record the outcome in the matching section of `report/PROCESS.md`.

## Where things are

| Path | Contents |
|---|---|
| `report/` | PLAN, PROCESS, EVALUATION, RETROSPECTIVE (and SYSTEM_DESIGN if there is an agent) |
| `docs/` | `WORKFLOW.md` (facts, rules), `BRANCHES.md` (card branches, PRs, `main`), `steps/` (one file per step), `RESUME.md` (handoff) |
| `golden/` | Golden sets and `review_log.md` |
| `experiments/` | One card per experiment |
| `configs/`, `prompts/` | Baseline + variant configs, prices; prompt files (one file per version) |
| `results/` | Committed results per experiment + `SUMMARY.md`; `out/` (run log, grades, report.html) and `scratch/` stay local |
| `labkit.yaml` | Marks the project root; settings labkit's code reads |

## Rules that cannot be undone if broken

- **Test split once.** Do not compute or look at any test-split number (LLM-free included) before the final setting is fixed (workflow step 8), and never run it on a branch. `labkit` refuses test commands without `--final` (it does not check the branch); do not add `--final` on your own. A golden set kept for the end, such as questions the team wrote itself, goes in `split: test` so it is locked the same way; its results are not read before step 8 either.
- **Never index** the golden set or any file holding questions or reference answers.
- **No batch LLM run without a card** whose sections 1–4 were written before results and whose gates passed (`docs/steps/06-experiments.md`). Exceptions: any run of the unchanged baseline config (the baseline, the noise runs, the sizing pilot in `golden/README.md`). `labkit run` refuses a variant whose card sections 1–4 still have placeholders, and an answer run of a variant until section 5 says `Gates passed? yes` (or `n/a` for an answer-step change). It reads that line, not the numbers above it, so write it only from the results. A change to the answer step itself (model, prompt, agent) is the other exception: it has no LLM-free gate and is listed in `report/PLAN.md` §3.2.
- **Smoke test first**: `--limit 1`, then `labkit show <experiment>` to read the result before a batch.
- **Disable tracing before batch runs**: `labkit` calls `labkit.env.setup()`, which forces `LANGSMITH_TRACING=false`; any new entry point must call it first.
- **Frozen files stay frozen**: a prompt, golden file, or config used in a run is never edited: create a new version. A `results/<experiment>/` folder used in a decision is never overwritten: rerun under a new name. `labkit run` refuses when an input file differs from what an evaluated experiment used; the fix is a new version of the file, never a workaround.
- **The judge model differs from every answer model** and stays fixed.

Language: _which language the deliverables in `report/` are written in; agent-facing docs stay in English._
