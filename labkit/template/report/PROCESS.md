<!--
GUIDE: Progress record. Follows docs/WORKFLOW.md: section N holds step N (§0 holds constraints and dated
decisions; §9 incidents; §10 the LLM call ledger).
- Fill a section when its step is done. Write the reasoning and the options you dropped, not only the result.
- Give every number its question count and split (dev/test).
- Attach question IDs and data to every explanation of a cause. Mark guesses as guesses.
- The experiment table, the adopted chain, and the ledger (§6, §10) are updated after every experiment.
- Final results go in EVALUATION.md; this document is the record of how you got there.
- Update the status table at the top when you start and when you finish a task (CLAUDE.md "Start of every task").
Keep guide comment in place after filling in.
-->

# Progress record

## Current state

Current step: _N. name_ (_YYYY-MM-DD_). Best known setup on `main`: _config, experiment_. Next: _..._

Add or update your row before you start, and commit and push it so teammates see it. Do not take work someone else holds. Mark finished work `done` and record the result in the section below.

| Task | Step | Owner (GitHub) | Status | Branch / PR | Updated |
|---|---|---|---|---|---|
| | | | | | |

Status values: `waiting` / `in progress` / `in review` / `done` / `on hold`

## 0. Constraints and dated decisions

One bullet per decision, with its date and reason. A decision that changes later gets a new bullet; the old one stays.

- _YYYY-MM-DD: decision, and why_

## 1. Corpus and golden set

### 1.1 Extraction check
_What you found (pages, kinds of damage), what you did about each._

### 1.2 Golden set
_Sources, counts per set and split, the seed, review findings. Pointer to golden/review_log.md._

### 1.3 Judge
_Model, prompt version, why it differs from the answer models, how it was checked._

## 2. Baseline

_Config, experiment name, dev n, headline numbers, cost per question._

## 3. Noise

- (a) rerun, identical config: _X of N questions changed_
- (b) regrade, same answers: _Y of N questions changed_
- Noise figure used in later comparisons: _N_

## 4. Proxy metric

_Metric, the correlations that justify it, direction agreement once available._

## 5. Triage

| Bucket | n | Question IDs |
|---|---|---|
| Retrieval miss, gold in the pool | | |
| Retrieval miss, gold not in the pool | | |
| Evidence present, answer incomplete | | |
| Over-abstention | | |
| Answered an unanswerable question | | |
| Data problem | | |
| Evaluation error | | |
| Ambiguous question | | |

Bottleneck chosen for step 6: _bucket, and why_

## 6. Experiments

### 6.1 Experiment table

| Card | Experiment | Change | Proxy / score vs compare_to | Gates | Decision | Date |
|---|---|---|---|---|---|---|
| | | | | | | |

### 6.2 Adopted chain

The last row is what new cards build on (`base:` this config, `compare_to:` this experiment).

| Order | Card | Config | Experiment | Adopted by (name, date) |
|---|---|---|---|---|
| 0 | baseline | `configs/baseline.yaml` | `dev_000_baseline` | |

## 7. Added structure

_Triage redone on the tuned chain, the structure and who reads each of its outputs, attribution numbers._

## 8. Final test

### 8.1 Configs committed before the first test run

| Run | Config | Commit | Why this system |
|---|---|---|---|
| T1 | | | |

### 8.2 Results
_Pointer to EVALUATION.md. Whether test was seen more than once, and why._

## 9. Incidents

One entry per thing that broke: what happened, how it was found, how it was fixed, what would have caught it earlier.

- _YYYY-MM-DD: …_

## 10. LLM call ledger

Every batch of model calls, so total cost and call counts can be audited.

| Date | Experiment / task | Answer calls | Judge calls | Embedding calls | USD | Cumulative calls |
|---|---|---|---|---|---|---|
| | | | | | | |
