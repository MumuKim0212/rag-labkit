"""The toy judge: a rubric a program can apply, standing in for the LLM judge of a real project.

It scores 0..2 the way labkit.yaml's `max_score: 2` expects:
- an answerable question: 2 if the answer holds every content word of the reference answer, 1 if it holds at least
  half of them, else 0 (an abstention scores 0);
- an unanswerable one: 2 if the system abstained, else 0.

It checks recall only, so it is lenient to a long answer; a real judge reads the answer. The judge model named in the
config differs from the answer model, as labkit requires, and its calls are recorded like any others.
"""

from labkit.cost import record_call
from labkit.judges import register_judge

from .text import tokens, words


@register_judge("toy-judge")
def grade(item: dict, run: dict, cfg: dict) -> dict:
    steps: dict = {}
    answer = run.get("answer") or ""
    if not item.get("answerable"):
        score, reason = (2, "abstained, as the documents do not say") if run["status"] == "abstained" else (0, "answered a question the documents cannot answer")
    elif run["status"] != "answered":
        score, reason = 0, f"status {run['status']}"
    else:
        key = set(words(item["target_answer"]))
        share = len(key & set(words(answer))) / len(key) if key else 1.0
        score = 2 if share >= 1.0 else 1 if share >= 0.5 else 0
        reason = f"{share:.0%} of the reference's content words are in the answer"
    record_call(steps, node="judge", model=cfg["judge"]["model"], input_tokens=tokens(answer + item["target_answer"]) + 40,
                output_tokens=12)
    return {"correctness": score, "reason": reason, "calls": steps["calls"]}
