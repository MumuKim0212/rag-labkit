<!--
GUIDE: The experiment plan, written before experiments start: purpose, hypotheses, fixed conditions, metrics,
and the record format.
- Do not change the judging criteria in this document after results are seen. A changed decision goes into
  PROCESS.md §0 with the date and reason.
- Per-experiment criteria go in experiments/NNN-*.md cards; this document holds the overall plan only.
- The order of work and the rules are in docs/WORKFLOW.md.
Keep guide comment in place after filling in.
-->

# RAG experiment plan

Written: _YYYY-MM-DD_

## 1. Purpose and hypotheses

**Purpose**: _what system you are building, over what, and what "good" means (quality and cost together)._

| # | Hypothesis | Why you expect it |
|---|---|---|
| H1 | | |

Each hypothesis is tested by one or more cards.

## 2. Corpus, questions, split

- Corpus: _files, size, scopes if different questions need different parts_
- Question sets: _name, size, origin, dev / test, stratification_
- Self-written set (if any): _how it is written, when, and that it is written without looking at system results_

## 3. Experiments

### 3.1 Baseline
_The simplest configuration, and the fixed conditions every comparison shares._

### 3.2 Rules
- One change per card; compared against the adopted chain's last experiment.
- LLM-free comparison and headroom gate before an answer run; exceptions (changes that alter the answer step) listed here.
- Final test: which systems run, once each (docs/steps/08-final-test.md).

### 3.3 Planned cards

| Card | Change | Bucket / hypothesis | Needs an answer run? |
|---|---|---|---|
| | | | |

## 4. Models and prices

| Role | Model | Price source and date |
|---|---|---|
| Answer candidates | | |
| Judge (fixed, not an answer candidate) | | |
| Embedding | | |

Prices live in `configs/pricing.yaml`.

## 5. Metrics and how they are measured

- Answer quality: judge score `0..max_score`, share fully correct, status match, citation checks.
- Retrieval (LLM-free): the proxy chosen in step 4.
- Cost: USD, tokens, LLM calls, searches, p50 / p90 latency, per question.
- Noise and significance: paired comparison with a 95% CI; differences smaller than the measured noise are not results.

## 6. LLM call estimate

_Rough calls per planned card and in total; the actuals go in PROCESS.md §10._

## 7. Result format

`results/README.md` and `labkit report`.

## 8. Schedule and cut order

_Dates per step. If time runs short, cut in this order: …_
