<!--
GUIDE: Step 1 of docs/WORKFLOW.md. Read WORKFLOW.md first (project facts, working rules), then this file.
Record the outcome in report/PROCESS.md §1. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 1: Corpus check, golden set, evaluation tools

The golden set is the yardstick for every later decision, and mistakes in it look like system failures. Fixing extraction later means re-indexing everything. Both are cheap now and expensive later.

## Do

1. **Spot-check extraction against the originals** before indexing: tables, charts, scanned or garbled pages, information that exists only in images (a product color, a chart value). List what you find and what you did about each in `report/PROCESS.md` §1.
2. **Build the golden set** by `golden/README.md`. Split dev and test now, before building anything. Put the extracted page text in a JSONL and run `labkit golden-check --pages <pages.jsonl>` (item 6 sets `golden.pages`, after which the flag can be left out; see `golden/README.md`) until it reports no problems.
3. **Size dev for the decisions it must carry.** Almost every decision is made on dev, so dev should not be much smaller than test. In a previous project, 85 answerable dev questions could not show an effect that 195 test questions showed clearly.
4. **If you write questions yourself**, write them from the documents without looking at any system result. A set you keep for the end is in `split: test`: it stays unrun until step 8 (`labkit` refuses it without `--final`), so its difficulty cannot be checked by running the baseline now. Only questions in `split: dev` can be tried on the baseline. The first self-written set in the previous project scored 100% on the final system and had to be rewritten, so write them to be hard. Never choose or drop questions by what the system got wrong.
5. **Write the judge** and register it (`labkit.judges.register_judge`, contract in that module). It uses a different model from every answer candidate, sees the reference answer and the evidence quotes, and scores `0..max_score` with a written reason. Name it in `configs/baseline.yaml` under `judge.name`, and set `judge.model` and `answer.model` there to real model ids: the judge criterion below fails while the template's `example-…` names remain. Create `prompts/judge_v1.md` and `prompts/answer_v1.md`, the files `configs/baseline.yaml` names, before the first run; labkit refuses a run while `judge.prompt` names a missing file (`answer.prompt` is your system's key, which labkit does not read).
6. **Fill in `labkit.yaml`**: `golden.files`, `golden.pages` (for `labkit golden-check`), `max_score`, `behaviors` (what the right response is for each kind of question), `modules` (the dotted names of the modules that register your system and judge, e.g. `myproject.systems`).
7. **Fill in the Project facts** table in `docs/WORKFLOW.md` and the budget and cost cap in it, and write `report/PLAN.md` (purpose, hypotheses, fixed conditions, which cards need an answer run). The plan is written before experiments start and its criteria are not changed after results are seen.

## Exit criteria

- [ ] Extraction issues found are listed in `report/PROCESS.md` §1, with what was done about each
- [ ] Every answerable question has verbatim evidence quotes that auto-match the target page text (claim it with the `labkit golden-check` result as evidence)
- [ ] dev/test split fixed and stored with the golden set; no test run so far <!-- auto: split_and_test_untouched -->
- [ ] Judge model differs from the answer model in every config <!-- auto: judge_differs_from_answer -->
- [ ] Judge prompt fixed (changing it later invalidates earlier grades) <!-- auto: judge_prompt_committed -->
- [ ] `labkit.yaml`, the Project facts table, and `report/PLAN.md` are filled in
