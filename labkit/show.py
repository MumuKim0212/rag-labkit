"""Read what an experiment logged: the question, the answer, the context the answer step received, the citations,
the searches, and the grade. For the smoke test before a batch and for triage (workflow steps 2 and 5).

    labkit show dev_000_baseline                 the latest row per question, smoke tests and failures included
    labkit show dev_000_baseline --qid Q1 --qid Q7
    labkit show dev_000_baseline --all           every logged row, in order
    labkit show dev_000_baseline --json          the rows as JSON lines, nothing cut

Smoke-test rows are marked `smoke`: they are in the log but belong to no result. A test experiment needs --final.
"""

import json
from pathlib import Path

from .eval import load_jsonl
from .eval.runlog import RunLog, is_smoke
from .harness import guard_experiment
from .paths import check_experiment_name
from .paths import grades_dir as default_grades_dir
from .paths import runs_db as default_runs_db


def _cut(text, chars: int) -> str:
    text = " ".join(str(text or "").split())
    return text if chars <= 0 or len(text) <= chars else text[:chars].rstrip() + f" … (+{len(text) - chars} chars)"


def format_run(row: dict, grade: dict | None = None, chars: int = 300) -> str:
    tokens = f"{row.get('input_tokens') or 0}/{row.get('output_tokens') or 0}"
    head = [f"== {row['qid']}  run {row['run_id']}  status={row['status']}" + ("  SMOKE (belongs to no result)" if is_smoke(row) else ""),
            f"   {row.get('created_at')}  latency {row.get('latency_s')}s  tokens in/out {tokens}  "
            f"llm calls {row.get('n_llm_calls')}  searches {row.get('n_searches')}"]
    lines = head + [f"question:  {_cut(row.get('question'), chars)}"]
    if row.get("error"):
        lines.append(f"error:     {_cut(row['error'], chars)}")
    if row.get("answer") is not None:
        lines.append(f"answer:    {_cut(row['answer'], chars)}")
    citations = row.get("citations") or []
    if citations:
        lines.append("citations: " + "; ".join(f"{c.get('source')} p.{c.get('page')} ({c.get('chunk_id')})" for c in citations))
    for i, search in enumerate(row.get("searches") or [], 1):
        lines.append(f"search {i}:  {_cut(search.get('query'), chars)} -> {', '.join(map(str, search.get('chunk_ids') or []))}")
    context = row.get("context") or []
    if context:
        lines.append(f"context ({len(context)} units given to the answer step):")
        lines += [f"  {u.get('chunk_id')}  {u.get('source')} p.{u.get('page')}: {_cut(u.get('content'), chars // 2 or 150)}"
                  for u in context]
    steps = row.get("steps") or {}
    if steps:
        calls = steps.get("calls") or []
        others = sorted(k for k in steps if k != "calls")
        lines.append(f"steps:     {len(calls)} model calls" + (f"; also {', '.join(others)}" if others else ""))
    if grade:
        lines.append(f"grade:     {grade.get('correctness')} ({grade.get('judge_model')}): {_cut(grade.get('reason'), chars)}")
    return "\n".join(lines)


def show(experiment: str, *, qids=None, all_rows: bool = False, as_json: bool = False, chars: int = 300,
         final: bool = False, db: Path | None = None, grades_dir: Path | None = None, root: Path | None = None) -> str:
    check_experiment_name(experiment)
    log = RunLog(db or default_runs_db(root))
    rows = log.load(experiment)
    if not rows:
        known = ", ".join(name for name, _ in log.experiments()) or "none"
        raise ValueError(f"no runs logged for {experiment}; logged experiments: {known}")
    guard_experiment(experiment, rows, final)  # reading a test answer is looking at test
    if qids:
        wanted = {str(q).strip() for q in qids}  # qids are stored trimmed (labkit.golden)
        rows = [r for r in rows if r["qid"] in wanted]
        if not rows:
            raise ValueError(f"{experiment} has no logged run for {', '.join(sorted(wanted))}")
    if not all_rows:  # the most recent row per question: a failure or a smoke row shows if it was the last thing that ran
        rows = list({r["qid"]: r for r in rows}.values())
    if as_json:
        return "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    path = Path(grades_dir or default_grades_dir(root)) / f"{experiment}.jsonl"
    grades = {g["run_id"]: g for g in load_jsonl(path) if g.get("run_id")} if path.exists() else {}
    return "\n\n".join(format_run(r, grades.get(r["run_id"]), chars) for r in rows)
