<!--
GUIDE: Step 4 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file.
Record the outcome in report/PROCESS.md §4. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 4: Check which retrieval metric tracks answers (LLM-free)

Answer runs cost money. A retrieval metric that moves with answer quality lets you compare many variants without LLM calls, but only if you picked the right one.

## Do

1. Take the baseline's grades and its per-question retrieval metrics (`results/dev_000_baseline/`).
2. Compute `labkit.eval.metrics.proxy_check(scores, metrics)` over answerable questions: the Pearson correlation of each metric with the answer score. Pick the metric that moves with the score. Pass only retrieval metrics (`page_hit@5`, `gold@5`, `evid@5`, `mrr`, ...). `metrics.jsonl` also holds answer-side columns such as `status_ok` and `citation_on_target`; they track the score almost perfectly because they are about the answer, and they say nothing about retrieval.

   ```python
   from labkit.eval import load_jsonl, to_map
   from labkit.eval.metrics import proxy_check
   from labkit.golden import load_golden

   answerable = {i["qid"] for i in load_golden() if i.get("answerable")}
   scores = {q: s for q, s in to_map(load_jsonl("results/dev_000_baseline/grades.jsonl"), "correctness").items() if q in answerable}
   rows = load_jsonl("results/dev_000_baseline/metrics.jsonl")
   print(proxy_check(scores, {m: to_map(rows, m) for m in ("page_hit@5", "gold@5", "evid@5", "mrr")}))
   ```

   A metric that is the same on every question (say `page_hit@5` is 1 everywhere) has no correlation: its value is `None`. If every candidate is `None`, retrieval does not separate the baseline's good answers from its bad ones on dev; that points away from retrieval as the bottleneck, so go to step 5 and let the failures decide.
3. The check that the metric moves the same way as the answer score between two configs (`labkit.eval.metrics.direction_agreement`) needs a changed config with an answer run, which does not exist yet. Do it in step 6, on the first card that has answer runs for both the baseline and the variant, and record it in `report/PROCESS.md` §4:

   ```python
   from labkit.eval import load_jsonl, to_map
   from labkit.eval.metrics import direction_agreement

   base, variant, proxy = "dev_000_baseline", "dev_NNN_slug", "gold@5"
   metric = lambda e: to_map(load_jsonl(f"results/{e}/metrics.jsonl"), proxy)
   score = lambda e: to_map(load_jsonl(f"results/{e}/grades.jsonl"), "correctness")
   print(direction_agreement(metric(base), metric(variant), score(base), score(variant)))
   ```

   `same_direction` out of `score_changed` is the agreement; `None` means no question changed score.

Do not assume `page_hit` or MRR is the right proxy. With small chunks in the previous project, hybrid search had the best `page_hit` and MRR but lost on answers; the number of gold-page chunks in the top 5 (`gold@5`) tracked answers.

## Exit criteria

- [ ] Proxy metric chosen, with the numbers that justify it (or the reason none could be chosen), in `report/PROCESS.md` §4
