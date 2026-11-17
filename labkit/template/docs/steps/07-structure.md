<!--
GUIDE: Step 7 of docs/WORKFLOW.md. Read WORKFLOW.md first, then this file and steps/06-experiments.md.
Record the outcome in report/PROCESS.md §7. Check the exit criteria with `labkit check`; claim manual ones with `labkit tick`.
Keep guide comment in place after editing.
-->

# Step 7: Add structure only when triage demands it

Query rewriting, question decomposition, agents, and self-reflection loops add a stage that changes the control flow or makes extra model calls on every question, and there is no LLM-free upper bound for them. Add one only when step 5 points at a failure it addresses (typically "gold not in the pool" failures traced to the question's wording), and treat it as a step-6 experiment with a card and a cost cap. Rerankers are step 6.

## Do

1. **Triage again on the tuned chain.** Structure designed against the baseline's failures can aim at problems that tuning already solved. In the previous project an agent was built around "evidence not found" failures (25 questions); chunking and reranking then fixed almost all of them, and the agent had nothing left to fix. Redo step 5 on the best chain before designing anything.
2. **Name the reader of every added step.** For each node or stage, write who consumes its output. A classifier whose result was computed and then never passed to the answer step was a wasted node in the previous project.
3. **Decide the instrumentation before building**, so wins can be attributed:
   - An agent with re-search: record the first search and the accumulated context separately (`steps["first_context"]`, scored by the `retrieval` measure as `first_*`), and whether the answer cites chunks only the re-search found. In the previous project only 5 of an agent's 9 wins came from re-search.
   - The first search of such an agent equals the baseline search for the same question, so compare them directly.
   - Query rewriting or decomposition: record every rewritten query and sub-question in `searches`, and the search with the original question as `steps["first_context"]`. The `first_*` metrics against the final context show whether rewriting helped or hurt; decomposition also needs a per-sub-question look at which one found the gold page.
   - Self-reflection loops: record each critique and the revised output in `steps`, and score the answer before and after revision. A checker that does not see the reference answer misses most of what is wrong: in the previous project the failures left on the tuned chain were answers lacking one detail the reference required, and the checker flagged only 3 of 90 answers. Measure the checker against the judge's failures before counting on it.
   - These systems call a model inside retrieval, so `--retrieval-only` is not LLM-free for them: count those calls in the card's budget.
4. **Run it as a step-6 card**, with the cost cap. Report the cost ratio next to the score: a gain inside the noise at 1.4× cost is a finding, not an adoption.
5. If the system has more than one stage, document them (nodes, flow, stop conditions) in `report/SYSTEM_DESIGN.md` and keep it in sync with the code.

## Exit criteria

- [ ] Same as step 6, plus the attribution numbers in the card
- [ ] Triage on the tuned chain recorded in `report/PROCESS.md` §7, with the reason this structure is justified
