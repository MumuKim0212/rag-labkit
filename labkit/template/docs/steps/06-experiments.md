<!--
GUIDE: Step 6 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file, then experiments/_TEMPLATE.md and docs/BRANCHES.md.
Record each card's outcome in report/PROCESS.md §6 (experiment table, adopted chain) and its calls in §10 (call ledger).
Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 6: Improve the bottleneck, one experiment card per change

## Step 6 or step 7?

| | Step 6 (this file) | Step 7 |
|---|---|---|
| What changes | One variable inside an existing stage | A stage is added that changes the control flow or makes extra model calls per question |
| Upper bound without an answer run | Yes: the headroom check below | No |
| Examples | chunking, retrieval method, `k`, embedding, **reranking**, answer model, prompt | query rewriting, question decomposition, agents, self-reflection loops |

A reranker is a step-6 card. Its upper bound is in the data: the questions whose gold page is in the candidate pool (`pool_page_hit`) but not in the context (`page_hit`), see step 5. A reranker that calls a model still makes calls in `--retrieval-only` mode; they are recorded in `steps` and show in the cost columns.

## Do, per card

Start on `main`, in the order `labkit new-card` prints: ① `labkit new-card` (the card number is fixed here), ② claim the card in `report/PROCESS.md` and commit that change alone, ③ `git switch -c <user>/NNN-slug`, ④ write sections 1–4 of the card. The rest of the card's work happens on that branch (`docs/BRANCHES.md`).

1. Run `labkit new-card <slug> --title "…"`. It takes the next number and creates `experiments/NNN-slug.md` (a copy of `experiments/_TEMPLATE.md`) and the variant config `configs/NNN-slug.yaml`. After the claim and the branch, fill **sections 1–4 first**: hypothesis (one variable, the failure bucket it targets with question IDs), measurement, go/no-go criteria, LLM budget. Do not compute anything you will judge it by before this is written. What `labkit` checks about the card:
   - `labkit run` refuses a variant config whose card still has placeholders in sections 1–4 (a smoke test of up to 3 questions is exempt) or lies outside the project, and warns before any question runs when the card's `Config:` header line does not name this config. It cannot judge what the sections say.
   - `labkit evaluate` warns if sections 1–4 were edited since the card was first used by *any* experiment: the LLM-free run and the answer run of one card share one history. `labkit report` marks an experiment whose card sections 1–4 changed after it was evaluated.
2. Edit the variant config that `new-card` created: set `base:` to the last adopted config (`--base` does it at creation), add only the changed keys, and `compare_to:` the experiment of the chain it builds on. `card:` is already set. `labkit run` prints `changed vs baseline` and, for a config built on another variant, `changed vs its base`: check that the second one (`changed_from_base` in `summary.json`) lists only what the card says. `name`, `card`, `description` and `compare_to` are not inherited, so each config must carry its own.
3. **Smoke test** one question: `labkit run --config configs/NNN-slug.yaml --split dev --limit 1`. It needs no card and is part of no result, so the real run repeats the question.
4. **LLM-free comparison** on dev with the proxy metric (step 4), and the context length (`metrics.chars` in `summary.json`; to see it in the report, list it in `report.columns` in `labkit.yaml`; that list replaces the default columns, so name every column you want):
   `labkit run … --retrieval-only` · `labkit evaluate --experiment dev_NNN_slug_retrieval` · `labkit compare dev_000_baseline dev_NNN_slug_retrieval --metric <proxy>`
5. **Headroom check** against the baseline's grades: `python -m labkit.eval.headroom --scores results/dev_000_baseline/grades.jsonl --before results/dev_000_baseline/metrics.jsonl --after results/dev_NNN_slug_retrieval/metrics.jsonl --metric <proxy> --noise <N>` (the scale of a fully correct answer is `max_score` from `labkit.yaml`; a baseline score above it stops the check).
   - upside: proxy improved and baseline not fully correct
   - risk: proxy worsened and baseline scored above 0
   - If upside − risk ≤ noise, stop: an answer run cannot show a gain. Record that in the card.
6. **Only if the gates pass**, and section 5 of the card says so (`- Gates passed? yes`; `labkit run` refuses an answer run of the variant until it does, a smoke test excepted), run the dev answers, grade, evaluate, and compare against the existing baseline run: `labkit compare <baseline> <variant> --noise <N>`. On the first card that has answer runs for both the baseline and a variant, also run `labkit.eval.metrics.direction_agreement` (step 4, item 3): among the questions whose answer score changed, did the proxy move the same way? Record it in `report/PROCESS.md` §4 and keep or replace the proxy.
7. **Decide** (adopt / reject / inconclusive) against the go/no-go criteria in §3 of the card (written in item 1), including the cost cap. Fill sections 5–7. The owner may adopt a card that misses its criteria; write "below criteria, adopted by the owner" and why in §7.
8. Export results (`labkit evaluate` writes `results/<experiment>/`; for an adopted config add `--with-runs` so its answers are committed too), regenerate the table (`labkit report`), open the PR.

Upside is a ceiling, not a forecast: more context can also distract the answer step.

Changes that alter the answer step itself (answer model, prompt, an agent) have no LLM-free proxy. Decide at planning time which of them always get an answer run, and say so in `report/PLAN.md` §3.2 and under Project deviations in `docs/WORKFLOW.md`. Such a card writes `n/a: answer-step change, always gets an answer run` on its two gate lines (§3) and in §5; the LLM budget in §4 then counts the answer run from the start.

## Exit criteria (per card)

- [ ] Card sections 5–7 filled; sections 1–4 unedited since results were seen
- [ ] Decision recorded in `report/PROCESS.md` §6 (experiment table, adopted-chain table) and the calls in the ledger, `report/PROCESS.md` §10
- [ ] If adopted: the PR was approved by the owner and merged to `main`; if not: card and results merged, config and code not (`docs/BRANCHES.md`)
- [ ] Once, on the first card with answer runs: direction agreement recorded in `report/PROCESS.md` §4, proxy kept or replaced

The criteria are met per card but claimed once per step: name the card in the evidence (`labkit tick 6 1 --evidence "card 004: sections 5-7 filled"`). A later `labkit tick` of the same criterion replaces the evidence that `labkit check` shows; every tick stays in the `log` of `report/state.yaml`.
