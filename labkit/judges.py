"""Judges: how a run's answer is scored. The judge is the project's (prompt, model, rubric); labkit only
fixes the contract and records which judge graded what.

    @register_judge("rubric-v1")
    def grade(item, run, cfg) -> dict:
        steps = {}                                    # record_call appends to steps["calls"]
        reply = call_my_model(...)
        record_call(steps, node="judge", model="...", input_tokens=reply.in_tokens, output_tokens=reply.out_tokens)
        return {"correctness": 0 | 1 | 2, "reason": "...", "calls": steps["calls"]}

`item` is the golden item (qid, question, target_answer, evidence, targets, …), `run` the logged run row (status,
answer, context, citations, steps, …), `cfg` the config the run used.

Required: `correctness`, a number from 0 to max_score (labkit.yaml, default 2); anything else (a string, None, a
value above max_score) stops grading before the row is saved. Optional: `reason`, `calls` (labkit.cost.record_call;
judge cost goes into the summary; calls that cannot be costed, such as None or a call without a `model`, stop grading too). Any other numeric (or boolean) field, e.g. `faithfulness`, is carried into metrics.jsonl and
averaged in summary["metrics"].

The judge is chosen in the experiment config, so it is part of config_hash and shows up in `changed`:

    judge: {name: rubric-v1, model: ..., prompt: prompts/judge_v1.md}

Use a different model from the answer model, and keep the judge fixed once grades are used in a decision:
changing it invalidates earlier grades.
"""

from typing import Callable

from .registry import Registry

JUDGES: Registry = Registry("judge")
register_judge = JUDGES.register


def judge_for(cfg: dict) -> Callable[[dict, dict, dict], dict]:
    """The grade function a run's config asks for (`judge.name`). With no name, the only registered judge."""
    name = (cfg.get("judge") or {}).get("name")
    if name is None:
        if len(JUDGES) == 1:
            return next(iter(JUDGES.values()))
        raise KeyError(f"config has no judge.name and {len(JUDGES)} judges are registered ({sorted(JUDGES) or 'none'})")
    return JUDGES.lookup(name)
