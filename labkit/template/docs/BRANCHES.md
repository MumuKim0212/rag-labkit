<!--
GUIDE: Branches, pull requests and main for card work (steps 6 and 7) and the final test (step 8). Read it before
starting a card and before step 8; the other steps work on main and do not need it.
Keep guide comment in place after editing.
-->

# Branches and main

Ticks, approvals and the test split happen on `main` only (`docs/WORKFLOW.md`, "The eight steps"). Card work happens on a branch:

- **One branch per card**: `<user>/<card-slug>`, e.g. `ana/004-hybrid`. The branch holds the card, the variant config, `results/<experiment>/`, and any code the change needs.
- **Claim before you start.** Add your row to the status table in `report/PROCESS.md` on `main` (commit that change alone) so nobody takes the same card.
- **`main` is the best known setup plus the whole record.** Its adopted chain (`report/PROCESS.md` §6) is what new cards build on.
- **Merge by PR.** The PR contains the card with §5–7 filled. The owner approves the decision.
  - *Adopted* (criteria in card §3 met, and best among competing cards for the same bottleneck): merge everything. The adopted chain moves. The owner may adopt a card that misses its criteria; the card then says "below criteria, adopted by the owner" with the reason (§7), so the decision can still be traced to what was written before the results.
  - *Rejected or inconclusive*: still merge the card and `results/<experiment>/` (a failed idea is evidence), but not the config or code that would change what `main` runs. On the branch before the PR: `git rm configs/NNN-slug.yaml`, restore the code (`git checkout main -- <paths>`), and change the card's `Config:` header line to "removed, kept in git history at <commit>". The removed config is a changed input from then on: the summary's `file_check` lists it (if the run is evaluated after the removal), and `labkit report` and `labkit compare` keep flagging the experiment as inputs changed since the run. That warning is expected for a rejected card and does not go away. Record the decision in the chain table.
- **When two cards compete**, compare them on dev with `labkit compare` against the same baseline and take the one that meets its criteria with the better score at acceptable cost. Do not merge both unless a third card shows the combination works.
- **When `main` moves under your branch**, merge `main` in. If your card compared against the old chain, say so in §7 ("combined effect with <card> not measured"); rerun against the new chain only if the decision depends on it. Effects are not additive.
- **Cards that merge in turn conflict in two places**: the experiment and ledger rows in `report/PROCESS.md` (keep both sides) and `results/SUMMARY.md` (do not merge it by hand: run `labkit report` after the merge).
- **The test split is never run on a branch.** Step 8 runs once, from `main`, with `--final`. `labkit` only requires the `--final` flag and does not check the branch, so check where you are before the final run.
- **Parallel sessions in one folder overwrite each other.** Use a separate working copy (`git worktree add ../<name> <branch>`) for the second one.
