<!--
GUIDE: Step 5 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file.
Record the outcome in report/PROCESS.md §5. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 5: Triage dev failures

Pick what to improve from the failures, not from what is fashionable. Everything here works from the run log; no rerun is needed if the baseline recorded `steps["pool"]` (step 2).

## Do

Classify every dev answer that is not fully correct:

| Bucket | How to tell |
|---|---|
| Retrieval miss, gold in the pool | The gold page is among the candidates (`pool_page_hit`) but not in the context passed to the answer step. A reranker or a larger `k` can fix it (step 6) |
| Retrieval miss, gold not in the pool | The gold page is not among the candidates. Needs better chunking or retrieval (step 6); if the question's wording is the cause, query rewriting or decomposition (step 7) |
| Evidence present, answer incomplete | Gold page in context, answer misses or misstates part |
| Over-abstention | Gold page in context, system abstained |
| Answered an unanswerable question | The golden item is `answerable: false` (a refusal or near-miss question) and the system answered (`status_ok` is 0). A prompt or abstention-rule problem, not retrieval |
| Data problem | Extraction lost the answer (image-only, broken table), or duplicate content elsewhere |
| Evaluation error | Reference answer wrong, or the judge mis-scored (read the judge's reason) |
| Ambiguous question | Depends on unstated context ("the plaintiff…", "this case", "the table") |

Read a question's logged run with `labkit show <experiment> --qid <qid>` (answer, context, citations, grade and the judge's reason); in code use the run log (`RunLog.latest`) with `labkit.eval.metrics.context_metrics` (is the gold page in the context?). List question IDs per bucket. Fix evaluation errors in a new golden version, not in place.

The two retrieval buckets need the candidates before reranking or cutting to `k`: have the system record them as `steps["pool"]` and `labkit` adds `pool_*` metrics next to the context metrics (`labkit.measures`). A system with no reranking or cutting stage has pool = context; use one "Retrieval miss" bucket. Read the questions behind the "gold not in the pool" failures: are they vague, colloquial, or do they need several documents at once? That decides whether step 7 applies.

If you are the one who built the system, have someone else (or a separate session) check a sample of the classification: the person who wrote the prompt tends to see "answer incomplete" where the question was ambiguous.

## Exit criteria

- [ ] Counts per bucket, with question IDs, in `report/PROCESS.md` §5
- [ ] The largest bucket named as the bottleneck for step 6
