<!--
GUIDE: Rules for prompt files. One file per version; a version used in a run is never edited.
Keep guide comment in place after editing.
-->

# prompts

- One file per prompt per version: `answer_v1.md`, `answer_v2.md`, `judge_v1.md`. A new idea is a new file, referenced from a variant config (`answer.prompt: prompts/answer_v2.md`).
- **Frozen once used.** A prompt that any run used is never edited, so old results still reproduce. The judge prompt is frozen at step 1; changing it invalidates earlier grades. What `labkit` checks about the judge prompt (the file `judge.prompt` names):
  - It must exist when the run starts: `labkit run` refuses an answer run without it (a search-only run does not need it), and `labkit grade` refuses while it is missing, so its hash is always on record.
  - `labkit grade` (a regrade too) refuses an experiment whose judge prompt was edited or deleted after it ran, and every grade row records a hash of the prompt that graded it.
  - Step 1's criterion "Judge prompt fixed" is checked by code: every judge prompt a config names exists, is tracked by git and has no uncommitted edits.
  - A judge prompt the config does not name is not checked; keeping it unedited is yours to do.
- Start each file with a header comment: what it is for, which step or card introduced it, what changed from the previous version, and which placeholders it expects (`{question}`, `{context}`, …). A placeholder that is never filled goes to the model verbatim, and the run looks fine. Smoke-test one question and read the prompt as sent.
- Use the same answer prompt in the baseline and in a structure variant when you want to measure structure alone.
