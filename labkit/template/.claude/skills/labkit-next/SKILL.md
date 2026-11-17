---
name: labkit-next
description: Do the current step of this project's eight-step RAG experiment workflow, then stop and hand over to the owner for approval. The owner types /labkit-next to start a step.
disable-model-invocation: true
allowed-tools: Bash(labkit status *) Bash(labkit check *)
---
<!--
GUIDE: The owner's one-command loop for the eight-step workflow (docs/WORKFLOW.md). Keep it a thin loop:
step content lives in docs/steps/NN-*.md and must not be copied here, or the two drift apart.
It cannot enforce the stop-before-approve rule; the rule is an instruction, and the owner is the check.
Keep guide comment in place after editing.
-->

# Do the current step, then stop

You carry out ONE step of the workflow. The owner approves it. You never do.

## Rules

1. Never run `labkit approve` or `labkit revoke`, and never start the next step.
2. Ask, do not guess, what only the owner can decide: the project facts in `docs/WORKFLOW.md` (task, corpus, models, budget, cost cap, deadline), which models to use, what counts as a correct answer.
3. Never edit a frozen file (a prompt, golden file, or config an evaluated experiment used) and never run the test split (`--final`) unless the current step is 8 and the owner told you to run it.
4. Claim a criterion only when you checked it. Evidence says where a reader can see it: a file, a section of `report/PROCESS.md`, a number with its n and split.

## The loop

1. Run `labkit status`. Note the current step N. If every step is approved, say so and stop.
2. Read `docs/WORKFLOW.md`, then only `docs/steps/NN-*.md` for step N, plus the files that step file tells you to read (step 7 names step 6, step 6 names the card template). Do not read the other steps.
3. Run `labkit check`. It lists the exit criteria: which hold, which are checked by code, which are open.
4. Do what the step file asks and you can do. Where an item needs an owner decision, ask before acting. Write the outcome into the matching section of `report/PROCESS.md` (step N is section N; the call ledger is §10).
5. Run `labkit check` again. For each open criterion that now holds and is not checked by code, run `labkit tick N I --evidence "..."`, where I is its number in the check output. Leave the rest open and say why.
6. Report, then stop. In the owner's language, short:
   - what you did: files created or changed, commands run, numbers with n and split
   - which criteria hold (by code, or claimed with your evidence) and which are open, and what blocks each open one
   - decisions you need from the owner
   - only if every criterion holds, a hand-over line for the owner to run, such as `labkit approve N --by NAME`. Do not run it.

## Notes

- `labkit tick` works on the main branch only. If you are on a card branch, say what to tick after the merge; do not switch branches yourself.
- Step 6 is a loop of cards: one card per `/labkit-next` (`labkit new-card`). The owner approves step 6 when the experiment loop is over.
- Step 7 is optional: say what the triage points at and let the owner decide whether to do it or skip it.
- Step 8 runs the test split once. Confirm with the owner before the first test command.
