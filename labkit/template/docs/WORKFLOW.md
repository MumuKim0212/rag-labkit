<!--
GUIDE: The operating manual for a RAG experiment project. Agents read this file, then only the file for the
current step (docs/steps/NN-*.md), not all eight.
Fill in "Project facts" before step 1. Do not edit the steps per project unless the owner asks; record
project-specific deviations under "Project deviations" below and in report/PROCESS.md §0.
Keep guide comment in place after filling in.
-->

# RAG experiment workflow

## Project facts

Fill in before step 1. Budget and cost cap are decided now, before any results exist.

| Item | Value |
|---|---|
| Task | _what the system answers, over what; what a good answer is, including when it should refuse or ask back_ |
| Corpus | _files, size, text layer or OCR needed, anything excluded_ |
| Models | _answer candidates; judge (a different model from every answer candidate, fixed for the whole project); embedding_. Stable, pinned model IDs only (no preview models, no alias that can start pointing at another model): a result must be repeatable. IDs and prices: `configs/pricing.yaml` |
| LLM call budget | _a number, or "minimize"_. Every call goes into the ledger in `report/PROCESS.md` |
| Cost cap for adopting a change | _e.g. ≤ 1.5× baseline USD per question and p90 latency ≤ N s; or "none: report cost beside quality"_ |
| Split | _dev / test ratio, how it is stratified; the seed is recorded in `golden/review_log.md`_ |
| Deadline | _date. The order to cut work if time runs short: `report/PLAN.md`_ |
| Outward side effects to disable | _e.g. tracing services. labkit forces `LANGSMITH_TRACING=false` and `LANGCHAIN_TRACING_V2=false`_ |

Project deviations from the steps below (also in `report/PROCESS.md` §0):
- _none_

## The eight steps

Do them in order. A step is done when its exit criteria hold and the owner has approved it. Record each step in the matching section of `report/PROCESS.md` (step N ↔ section N).

Before step 1: the folder is a git repository on `main` with a first commit (`labkit init --git` does the first part), and `git config user.name` is set (it is the default `--by` of `tick` and `approve`).

The state of the steps is kept by `labkit`, in `report/state.yaml`:

- `labkit status`: which step is current, who approved the earlier ones.
- `labkit check [N]`: lists the exit criteria. Some are checked by code (files exist, a config is committed, the judge differs from the answer model, which `labkit run` also enforces on every answer run, smoke tests included); the rest are claimed with `labkit tick N I --evidence "…"`, naming the file, section, or number that shows it. An agent claims a criterion; it does not approve a step.
- `labkit approve N --by NAME`: the owner approves a step whose criteria all hold. Only then does step N+1 open. Step 7 is optional: `labkit approve 7 --skip --note "why not needed"`.
- `labkit revoke N --reason "…"`: withdraw the latest approval when a problem turns up; the record keeps the history.
- Ticks, approvals, and revocations work on `main` only (`main_branch` in `labkit.yaml`; not checked where git cannot run) and change `report/state.yaml`: commit that change alone on `main`. Work for steps 1–5 can be committed on `main` directly; a step-6 criterion is ticked after the card's PR has merged. Editing a criterion's wording makes earlier ticks of it stale (`STALE`): claim it again.
- In Claude Code, `/labkit-next` (`.claude/skills/labkit-next/SKILL.md`) has the agent do the current step, claim what it verified, report, and stop for your approval. It is a loop over this file and the step files, not a second copy of them. It cannot stop an agent from running `labkit approve`, so the owner stays the check.

| Step | File | In one line |
|---|---|---|
| 1 | [steps/01-corpus-golden.md](steps/01-corpus-golden.md) | Check the corpus, build the golden set, fix the judge |
| 2 | [steps/02-baseline.md](steps/02-baseline.md) | Simplest system, end to end, on dev |
| 3 | [steps/03-noise.md](steps/03-noise.md) | Measure how much a rerun changes by itself |
| 4 | [steps/04-proxy.md](steps/04-proxy.md) | Pick the LLM-free metric that tracks answer quality |
| 5 | [steps/05-triage.md](steps/05-triage.md) | Sort every dev failure into a cause |
| 6 | [steps/06-experiments.md](steps/06-experiments.md) | Fix the biggest bottleneck, one card per change |
| 7 | [steps/07-structure.md](steps/07-structure.md) | Add query rewriting, decomposition, agents only if triage demands |
| 8 | [steps/08-final-test.md](steps/08-final-test.md) | Run test once, write up |

## Working rules

Apply to every step. The irreversible ones are repeated in `CLAUDE.md`.

- **Gate answer runs.** Before an answer run, do the LLM-free comparison and the headroom check (`python -m labkit.eval.headroom`). If upside minus risk is not larger than the measured noise, do not run it; record that in the card. The exception: a change that alters the answer step itself (answer model, prompt, an agent) has no LLM-free proxy and always gets an answer run. List such cards in `report/PLAN.md` §3.2 and under Project deviations, and write `n/a: answer-step change, always gets an answer run` in the card's gate lines.
- **Reuse, don't rerun.** Compare against the existing baseline run instead of running it again.
- **Cards come first, and `labkit` checks.** `labkit run` on a variant config (one whose `changed` is not empty) needs its card with sections 1–4 written, and an answer run of it needs section 5 to say `Gates passed? yes` (`n/a` for an answer-step change); the baseline and noise reruns are exempt, and so is a smoke test of up to 3 questions. What else it checks and warns about is in step 6, item 1.
- **One index per set of index-affecting settings.** List those settings under `index_keys` in `labkit.yaml`. `labkit` records the resulting `index_key` with every run and shows it in the report. A system that keeps an index reports the key it used (`steps["index_key"]`; a mismatch fails the question) and implements `ready()` so a missing index stops the batch before it starts.
- **Smoke test first.** Before any batch, run `--limit 1` and look at the result. Two incidents in the previous project (an unfilled prompt placeholder, a missing index before the test run) would have shown up on one question. A run of up to 3 questions is a smoke test: its rows stay in `out/runs.db` (read them with `labkit show <experiment>`) but belong to no result. The real run repeats those questions, nothing is graded or evaluated from them, and fixing a prompt after reading one needs no new experiment name.
- **Settle the decision conditions before test.** Prices, the final model choice, and the cost cap are fixed before the first test run. Changing them after seeing test numbers means seeing test twice.
- **A judge that grades its own kind is lenient.** The judge model differs from every answer model and stays fixed; changing the judge invalidates earlier grades.
- **Every run is logged** by `labkit` (retrieved ids, the context passed to the answer step, citations, status, tokens, latency). Systems record every model call with `labkit.cost.record_call`.
- **Use the shared CLI.** Run, grade, evaluate, compare, and report only through `labkit` (`labkit --help`; each step file lists its commands). A new experiment is a new config plus a card, not a new script. One-off analysis goes to `scratch/` (git-ignored); promote it to the project only if the write-up depends on it.
- **Change behavior by config, not by editing what the baseline runs.** A card's code goes in as a new registered name (a system, chunker, retriever, ...) that a config key selects; the baseline's name keeps behaving as before, so rerunning the baseline config reproduces the baseline. `labkit` does not freeze code (every run records the git commit, a dirty flag and a hash of the uncommitted edits, and `labkit evaluate` warns when the scored runs of one experiment saw different versions; nothing stops the edit itself), so this rule is yours to keep.
- **Never index the golden set** or any file holding questions or reference answers.
- **Export results** to `results/<experiment>/` at the end of each experiment (`results/README.md`).
- **Claims need data.** Write a cause only after checking per-question data, and cite question IDs. Mark guesses as guesses.
- **Decisions get a date.** A changed decision goes into `report/PROCESS.md` §0 with the date and reason. Do not edit `report/PLAN.md` criteria after results are seen.
- **Frozen means frozen.** A prompt, golden file, or config used in a run is never edited: create a new version. A `results/<experiment>/` folder used in a decision is never overwritten: rerun under a new name. `labkit run` enforces it: it refuses to resume an experiment whose config or inputs changed, and refuses any run when an input differs from what an *evaluated* experiment used (runs nobody evaluated freeze nothing, so smoke tests stay free). If an evaluated experiment was invalid, remove its `results/<experiment>/` folder in a reviewed commit and it stops freezing. What is hashed, and what is not (folders, links, paths outside the project): `file_hashes` in `results/README.md`; the `labkit.yaml` settings fixed per run: `project_settings` there.

## Branches and main

Cards (steps 6 and 7) are worked on `<user>/<card-slug>` branches and merged by PR; ticks, approvals and the test split happen on `main` only. How to claim a card, merge an adopted or a rejected one, settle competing cards and run parallel sessions: [BRANCHES.md](BRANCHES.md). Read it before card work and before step 8.

## Rules of thumb

- A retrieval gain becomes an answer gain only on questions the current setup gets wrong. Count those first.
- A difference smaller than the measured noise is not a result. The same direction in two independent runs is stronger evidence than one run.
- With small chunks, how much of the gold page reaches the answer step matters more than whether the gold page appears at all.
- Report cost next to accuracy: +0.026 score for 2.3× latency is a trade-off, not a win.
- Tuning the parts that find and read the evidence (chunking, retrieval, reranking, answer model, prompt) moved scores more than adding structure in the previous project. Check the cheap levers first.
