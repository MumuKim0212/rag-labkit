<!--
GUIDE: What gets committed under results/. One folder per experiment name (results/<experiment>/).
out/runs.db stays local; results/ is how runs are shared and submitted. Files here are written by
`labkit evaluate` and `labkit report`: do not hand-edit them; keep this README in sync with
labkit.harness when the summary format changes.
Keep guide comment in place after editing.
-->

# results

One folder per experiment, `results/<experiment>/`, named `<split>_<config name>[_retrieval]` (e.g. `dev_000_baseline`, `dev_004_hybrid_retrieval`). Written by `labkit evaluate --experiment <name>`; the local `out/runs.db` is never committed.

## Files per experiment

| File | Every experiment | Baseline, adopted configs, final test (`--with-runs`) |
|---|---|---|
| `summary.json` | yes | yes |
| `metrics.jsonl` | yes | yes |
| `grades.jsonl` | if graded | yes |
| `runs.jsonl` | – | yes |

- `metrics.jsonl`: one row per question, no LLM: `qid`, `set`, `status`; the columns of every measure listed in `labkit.yaml` (by default `retrieval`: `page_hit`, `gold`, `evid`, `evid_full`, `units`, `chars`, `mrr`, `page_hit@k`, `gold@k`, `evid@k`, plus `first_*` when a system records `steps["first_context"]` and `pool_*` when it records `steps["pool"]`; `citation`: `citation_in_context`, `citation_on_target`; `status`: `status_ok`, `behavior_group`); per-question `usd`, tokens, calls, `latency_s`; `correctness` and any other numeric judge field copied from the grades.
- `grades.jsonl`: one row per graded run: `qid`, `run_id`, `correctness` (0..max_score), `reason`, any extra judge fields, `calls`, `judge_model`, `judge_prompt`, `judge_prompt_hash` (a short hash of the judge prompt file when it graded this answer; null if the file did not exist). A grade counts for the run whose `run_id` it names and for no other: after a rerun (for example the run log was lost and the experiment run again) the new answers are ungraded until `labkit grade` is run again, and only grades of the scored runs are exported here.
- `grades__<suffix>.jsonl`: a regrade of the same runs (`labkit grade --suffix`), copied from `out/grades/` when a decision uses it; `dev_000_baseline/grades__regrade1.jsonl` is judge noise (workflow step 3).
- `runs.jsonl`: every logged run of the experiment (`RunLog.load`, errors and reruns included), one per line.

## summary.json

| Key | Contents |
|---|---|
| `experiment`, `card`, `split`, `system`, `retrieval_only` | identity (`split` is the one `labkit run` recorded, not read from the name) |
| `complete`, `incomplete` | `false` when the numbers cover only part of the experiment; `incomplete` lists why: expected questions that never ran, scored questions that are no longer in the split (the golden files were edited after the run), questions that failed on every attempt, answers not graded. A failed question is not scored as 0, so the flag is what says the score is partial. `labkit report` also marks a row `⚠ inputs changed since the run` when a file listed in `file_hashes` differs from what the run recorded (checked when the report is built, not when it was evaluated) |
| `sets` | `null` when the experiment covers the whole split (including a split run set by set), else the `--sets` it covers. `labkit report` shows no difference from the comparison baseline when the two cover different sets, and the baseline check requires `null` |
| `n_expected`, `n_questions`, `n_errors`, `n_graded`, `max_score` | counts: expected = the golden questions of the run's split (and `--sets`); `n_questions` = scored runs; `n_errors` = questions whose only runs failed |
| `compare_to` | experiment the report diffs against (config meta key; default `<split>_000_baseline`). The report shows no difference when the two ran on different splits, sets or `labkit.yaml` settings, and for a retrieval-only result against an answer run (or the reverse) only in the `metrics.*` columns |
| `changed`, `changed_from_base` | settings that differ from `configs/baseline.yaml`, and from the file named by `base:` (what this card itself changes; equal to `changed` for a config built on the baseline): `{"retrieval.method": {"from": "vector", "to": "hybrid"}}` |
| `run` | the `run:` block of the config (`concurrency`, and `timeout_s`, `max_retries` in the template); only `concurrency` is left out of `config_hash` |
| `description`, `config_file`, `config_hash`, `config` | the full merged settings that produced the runs |
| `index_key` | hash of the settings listed under `index_keys` in `labkit.yaml`; experiments with the same key shared an index |
| `file_hashes`, `file_check` | short hash of every input file at run time, under the path as the config writes it (files named anywhere in the config, including in lists; golden files; the config chain; `baseline.yaml`; line endings ignored; not folders or paths outside the project); `file_check.changed_since_run` lists those that differ or were deleted by evaluation. An evaluated experiment freezes these files (`labkit.freeze`). A name is judged by the file it reaches now, so a folder link repointed after the run, or a hard link, is not followed: create a new file instead |
| `project_settings`, `pricing_hash` | the `labkit.yaml` settings the result was scored under (`golden_files`, `max_score`, `answerable_default`, `behaviors`) and a short hash of `configs/pricing.yaml` when it was evaluated. `labkit compare` warns, and `labkit report` shows no difference, when two results differ in these; `compare` refuses two different `max_score` unless `--max-score` is given |
| `card_check` | the card named by the config, the hash of its sections 1–4 when any experiment first used it (`first_run_experiment` and `first_run_at` in UTC: the oldest record in the run log and the committed summaries, so a teammate who ran first counts first) and now, and `edited_after_run` (null for the baseline and noise reruns) |
| `environment`, `environment_check` | Python, git commit / branch / dirty flag / `changes` (a short hash of the uncommitted edits and new files in `src`, `scripts`, `configs`, `prompts`, `golden` and wherever your own code lives: the `pythonpath` folders, or with the default `pythonpath: [.]` the packages named under `modules:`; null when clean; `labkit.yaml` itself and paths outside the project are not watched), package versions, taken from the first scored run. `environment_check` lists the commits and `code_versions` (`<commit>` or `<commit>+<changes>`) of all scored runs; `mixed_commits` is true when there is more than one, i.e. a batch was resumed on other code (smoke tests never count) |
| `scores` | `correctness_mean` (score / max_score, 0–1), `fully_correct` (share at max_score) |
| `metrics` | mean of every numeric column in `metrics.jsonl` (excluding cost columns), over the questions that have it; booleans are rates |
| `by_set` | per golden set: `n`, `correctness_mean`, `fully_correct`, `metrics` |
| `cost` | `usd` and `usd_per_question` (the scored runs); `wasted_usd` (failed, superseded and smoke-test attempts: paid for, in no score); `total_usd` (= `usd` + `wasted_usd`, the report's cost column; it leaves out judging and index building, so add `judge_usd`, `judge_regrade_usd` and `index_build_usd` when you check the LLM budget); `usd_by_model`; `judge_usd` (every judge call in the experiment's grades file, replaced grades included); `judge_regrade_usd` (the `__regrade*` passes in `out/grades/`); `index_build_usd` (null unless `index_dir` is set in `labkit.yaml` and a `build.json` exists for every scope used: a missing scope makes the total unknown, not smaller); tokens (input / output / thinking, `tokens_per_question`). A system that raises loses its calls; return `{"status": "error", "error": …, "steps": steps}` to keep their cost |
| `calls` | `llm`, `llm_per_question`, `searches_per_question`, `judge` |
| `latency_s` | `p50`, `p90` |
| `created_at` | when `labkit evaluate` wrote this summary (local time, no offset) |

**What the freeze does not see** (`file_hashes`): a folder named in the config (name its files), a path outside the project, a file reached through a folder link that leads outside the project, and a path that does not exist when the run starts. A name is judged by the file it reaches now: pointing a folder link at another version after a run, or making a hard link to a frozen file, can hide an edit or raise a false alarm, so create a new file instead of retargeting a link. `labkit.yaml` is not hashed either, but its golden list and `max_score` are recorded with every run: an experiment is never resumed, graded or evaluated after either changed.

A metric an experiment did not measure is absent from `metrics`; report columns show it as "–". Metric names must not contain a dot.

## Comparison views

- `results/SUMMARY.md`: all dev experiments in one table with differences against `compare_to`; committed, readable on GitHub. Regenerate with `labkit report` after each evaluation. Columns: `report.columns` in `labkit.yaml`.
- `out/report.html`: the same table, sortable, colored by better / worse; local only, open in a browser.
- `labkit compare <a> <b>`: paired per-question comparison with a 95% CI, plus cost and latency ratios. It prints a `WARNING` line first when a result is incomplete, when the two were scored under different `labkit.yaml` settings or costed from different price tables, and (checked against the files as they are now, like `labkit report`) when an input file or the card of either result changed after it ran. A retrieval-only result has no grades: compare it with `--metric <column of metrics.jsonl>`; against an answer run it warns and leaves out the cost, call and latency lines.

## Rules

- Write the experiment card's sections 1–4 before the first file appears here.
- `test_*` folders appear only at workflow step 8 (`--final`); the report leaves them out unless `--include-test --final`.
- `labkit evaluate` refuses to overwrite an existing folder. A folder used in a decision is never overwritten; rerun under a new name. `--overwrite` is only for folders nobody has used yet, and for a test result `labkit` allows it only on one with `complete: false` (an evaluation made before grading was done, which you finish by evaluating again), never on a complete or unreadable one.
- `labkit evaluate` writes `summary.json` last, so a folder that has one is whole. A `summary.json` damaged by hand is reported by name (restore it from git, or remove the folder), and `labkit report` / `labkit run` stop on it rather than skip it. NaN or infinity in a judge's grade is refused when it is graded, so the exports never contain NaN. If a complete test result is invalid, remove its folder in a reviewed commit and say why in `report/PROCESS.md`.
