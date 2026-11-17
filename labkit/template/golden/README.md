<!--
GUIDE: Rules for the golden set in this folder. The golden files live next to this README; the ones labkit
loads are listed under `golden.files` in labkit.yaml (default golden.jsonl), and nothing else here is read.
Update the "This project" section when the set is built.
Keep guide comment in place after editing.
-->

# Golden set

The golden set is the yardstick for every decision. Mistakes in it look like system failures, so build it carefully and keep a review log (`review_log.md`).

**Never add the golden set to the search index.**

## Format (one JSON object per line)

```json
{
  "qid": "Q001",
  "set": "base",
  "split": "dev",
  "question": "…",
  "answerable": true,
  "expected_behavior": "answer_with_citation",
  "target_answer": "…",
  "evidence": ["verbatim quote copied from the extracted page text", "…"],
  "targets": [{"file": "report.pdf", "page": 12}],
  "domain": "policy",
  "context_type": "paragraph",
  "question_type": "fact",
  "note": ""
}
```

- `qid`: required and unique across all listed files (labkit names the two files if it repeats). A string or an integer, read as a string with surrounding spaces removed (`12`, `"12"` and `" 12 "` are one question) and in Unicode NFC form (a composed and a decomposed spelling of the same Korean text are one qid). Refused: two qids that differ only by case (`D1`, `d1`), and a qid with an invisible or control character (a zero-width space, a bidi mark, a Hangul filler, a variation selector) or a space that is not a plain space.
- `split`: exactly `dev` or `test`. `set`: a group name for per-set results. Both are used by `labkit run --split/--sets`. labkit refuses to load a golden file with any other `split` (missing, `Test`, ` test`), because such a question would silently belong to no run, and refuses a `--sets` name that no question of the split has.
- Field types are checked when the file is loaded, with the line and qid in the message: `question` is non-empty text, `set` (if given) is non-empty text, `targets` is a list of `{"file", "page"}` (`null` means none), `evidence` is a list of text.
- `evidence`: verbatim quotes, copied from the extracted text of the target page, not paraphrased. `labkit.eval.metrics` matches them line by line after normalization; this is what makes evidence coverage computable without an LLM.
- `targets`: every page that contains the answer. A question needing two documents gets one target per document.
- `answerable: false` questions have no targets or evidence; put the reason in `note`. They measure correct refusal.
- `expected_behavior`: what the right response is (answer, abstain, ask back, correct a false premise, refuse). The names and the system `status` values that count as right are defined under `behaviors` in `labkit.yaml`.
- `context_type` (text / paragraph / table / image) and `question_type` let results be broken down later. Add fields you will want to slice by now; adding them later means re-reviewing.
- Optional `scope`: a text label passed to the system to say which part of the corpus to search (or map sets to scopes with `corpus.set_scope` in the config). If the corpus has more than one scope, say which scope the unanswerable questions assume: a question with no answer in the small scope may have one in the large scope.

## Review checklist

- [ ] Every evidence quote auto-matches the target page's extracted text (normalize with NFKC and strip whitespace). A failing quote means a wrong page, a typo, or an extraction problem, all worth knowing before step 2. `labkit golden-check` does this for every item: put the extracted text in a JSONL (`{"file": "report.pdf", "page": 12, "text": "…"}` per line), point `golden.pages` in `labkit.yaml` at it (or pass `--pages`), and it names each item and quote that does not match. It also flags missing targets or evidence, unanswerable items that list any, and quotes with no line long enough to be scored. It reads no system result, so it may check the test split too.
- [ ] Same content elsewhere (a press release and its attachment, a ruling and its appendix): list all those pages as targets, or the page metrics will count a correct retrieval as a miss.
- [ ] No question depends on unstated context ("the plaintiff…", "this case", "the table"). Rewrite it to stand alone or drop it.
- [ ] Reference answers checked against the source, not just copied from a provided set. Record every fix (question, answer, page) in the review log.
- [ ] Some questions phrased like real users: short, vague, using words the document does not use. Questions written while looking at the page are easy to retrieve and overstate quality.
- [ ] A set of unanswerable questions, including near-misses (the document covers the topic but not the asked fact).
- [ ] File names normalized (Unicode NFC) on both sides of every comparison.
- [ ] A fully correct system would not score 100% on a sample: a set the baseline already aces cannot show improvement.

## Splits

- Split before building the system, stratified by domain, context type, and difficulty.
- All tuning and decisions use dev. Test is run once, at the end. This includes LLM-free retrieval metrics.
- Size dev so the differences you care about exceed the noise measured in step 3. That noise is measured after the golden set is frozen, so size dev with a pilot: run a draft baseline twice on the draft dev set (`labkit run … --name pilot_a`, `--name pilot_b`) and compare the two with `python -m labkit.eval.paired`. Do not `labkit evaluate` the pilot runs: an evaluated experiment freezes its input files.
- A set you keep for the end, such as questions the team wrote itself, goes in `split: test`. labkit then keeps it locked until `--final`; nobody tunes on it or reads its results before step 8.
- Frozen once an evaluated run has used it: a fix means a new file and a note in `report/PROCESS.md`.
  - List the new file in `golden.files` **instead of** the old one: labkit rejects the same `qid` in two listed files, so never list both. Keep the old file in `golden/` unlisted (or in `golden/archive/`) and say so in `golden/review_log.md`; results made with it keep the hash of what they used in `summary.json`.
  - Results on the old and the new file are not compared: `labkit compare` warns when two summaries list different `golden_files`, and `labkit report` shows no difference between them.
  - The test lock follows each item's `split` as written in the listed files, so never relabel a test question as dev in a new version: labkit does not compare versions.

## This project

_Files, counts, the split seed, which sets are dev/test, and where each set came from._

| File | Contents | Split |
|---|---|---|
| | | |
