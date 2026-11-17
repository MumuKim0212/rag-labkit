"""Per-question measures: LLM-free numbers computed from a golden item and a run, written to metrics.jsonl
and averaged into summary["metrics"].

A measure is `fn(item, run, project) -> dict` returning columns (numbers, None, or other values; only
numbers are averaged). Return {} when the measure does not apply, e.g. no targets on the item.

    @register_measure("answer_length")
    def answer_length(item, run, project):
        return {"answer_chars": len(run.get("answer") or "")}

Which measures run is `measures:` in labkit.yaml (default: retrieval, citation, status). Show a new column in
the report with `report.columns` (labkit.yaml), e.g. `metrics.answer_chars`.
"""

from .eval.metrics import citation_check, context_metrics, ranked_metrics
from .golden import behavior_of, status_ok
from .registry import Registry

MEASURES: Registry = Registry("measure")
register_measure = MEASURES.register


@register_measure("retrieval")
def retrieval(item: dict, run: dict, project: dict) -> dict:
    """What the answer step received (page_hit, gold, evid, ...) and the ranking quality (mrr, page_hit@k, ...).
    Only for items with targets. Two optional records in the run's `steps` add the same metrics under a prefix:
    - `first_context`: the first search of a multi-step system (`first_` prefix);
    - `pool`: the candidates before reranking or cutting to k (`pool_` prefix). `pool_page_hit` minus `page_hit`
      is the share of questions a reranker could still fix: gold was in the pool, but not in the context."""
    if not item.get("targets"):
        return {}
    context = run.get("context") or []
    row = {**context_metrics(item, context), **ranked_metrics(item, context)}
    steps = run.get("steps")
    for key, prefix in (("first_context", "first_"), ("pool", "pool_")):
        units = steps.get(key) if isinstance(steps, dict) else None
        if units is not None:
            row.update({f"{prefix}{k}": v for k, v in context_metrics(item, units).items()})
    return row


@register_measure("citation")
def citation(item: dict, run: dict, project: dict) -> dict:
    if run["status"] == "retrieval_only":
        return {}
    return {f"citation_{k}": v for k, v in citation_check(item, run).items()}


@register_measure("status")
def status(item: dict, run: dict, project: dict) -> dict:
    """Did the system respond the way the item's expected_behavior allows (needs `behaviors` in labkit.yaml)."""
    if run["status"] == "retrieval_only":
        return {}
    return {"status_ok": status_ok(item, run, project), "behavior_group": behavior_of(item, project)[0]}


def run_measures(item: dict, run: dict, project: dict, names=None) -> dict:
    row = {}
    for name in names if names is not None else project["measures"]:
        row.update(MEASURES.lookup(name)(item, run, project))
    return row
