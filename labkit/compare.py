"""Paired comparison of two experiments from results/, with cost and latency next to the score.

    labkit compare dev_000_baseline dev_007_lite31                        # answer score (grades.jsonl)
    labkit compare dev_000_baseline dev_004_hybrid_retrieval --metric gold@5 --noise 5
    labkit compare test_a test_b --final                                  # a test experiment needs --final (step 8)

Score comparisons use grades.jsonl (`correctness`, divided by max_score from labkit.yaml); --metric compares a
metrics.jsonl column instead (max 1, or k for gold@k). Before an answer run, use `python -m labkit.eval.headroom`
on the same metrics files.

It warns, and still compares, when a result is incomplete, when the two were scored under different labkit.yaml
settings or priced differently, and when an input file or the card of a result changed after it ran. A
retrieval-only result has no grades, so it is compared with --metric only; against an answer run it warns and
leaves out the cost, call and latency lines.
"""

import json
import math
from pathlib import Path

from .eval import load_jsonl, load_summary, to_map
from .eval.paired import compare, report
from .freeze import changed_since
from .harness import guard_test, live_flags
from .paths import check_experiment_name, project_root, results_dir
from .project import load_project, settings_diff

COST_ROWS = (("USD/question", "cost", "usd_per_question"), ("tokens/question", "cost", "tokens_per_question"),
             ("LLM calls/question", "calls", "llm_per_question"), ("p90 latency s", "latency_s", "p90"))


def _load(folder: Path, file: str) -> list[dict]:
    path = folder / file
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist: run `labkit evaluate --experiment {folder.name}` first")
    return load_jsonl(path)


def compare_experiments(a: str, b: str, *, metric: str | None = None, max_score: float | None = None,
                        noise: int | None = None, final: bool = False, root: Path | None = None) -> str:
    folder = results_dir(root)
    check_experiment_name(a), check_experiment_name(b)
    if max_score is not None and (isinstance(max_score, bool) or not isinstance(max_score, (int, float))
                                  or not math.isfinite(max_score) or max_score <= 0):
        raise ValueError(f"--max-score must be a positive number (the score of a fully correct answer), got {max_score!r}")
    sa, sb = (_load_summary(folder / x) for x in (a, b))
    for name, s in ((a, sa), (b, sb)):  # the split the summary recorded, not the name: `final_try` may be test
        guard_test(s.get("split") or name.split("_", 1)[0], final)
    if metric:
        file, field = "metrics.jsonl", metric
        max_score = max_score or (float(field.split("@")[1]) if field.startswith("gold@") else 1.0)
    else:
        file, field = "grades.jsonl", "correctness"
        for name, s in ((a, sa), (b, sb)):  # evaluated, yet it has no grades.jsonl: `run labkit evaluate` was wrong advice
            if s.get("retrieval_only"):
                raise ValueError(f"{name} is a retrieval-only result: it has no grades; "
                                 "use --metric <column of metrics.jsonl>")
        if max_score is None:  # each result knows the scale it was graded on; today's labkit.yaml may say otherwise
            scales = {s["max_score"] for s in (sa, sb) if s.get("max_score") is not None}
            if len(scales) > 1:
                raise ValueError(f"{a} was scored 0..{sa['max_score']} and {b} 0..{sb['max_score']}: their grades are "
                                 "not on one scale. Pass --max-score N to compare them on a scale you choose anyway.")
            max_score = float(scales.pop()) if scales else float(load_project(root)["max_score"])
    scores_a, scores_b = (to_map(_load(folder / x, file), field) for x in (a, b))
    lines = [report(compare(scores_a, scores_b, max_score=max_score), a, b, noise=noise)]
    for name, s in ((a, sa), (b, sb)):  # the pairs cover only the questions both experiments scored
        if s.get("complete") is False:
            lines.insert(0, f"WARNING: {name} is incomplete ({'; '.join(s.get('incomplete') or [])}); "
                            "the comparison covers only questions both experiments scored.")
    here = Path(root or project_root())
    for name, s in ((a, sa), (b, sb)):  # live, as the report does: the files and the card as they are now
        flags = live_flags(s, here)
        if flags:
            files = changed_since(s.get("file_hashes"), here)
            where = f" ({', '.join(files)})" if files else ""
            lines.insert(0, f"WARNING: {name}: {'; '.join(flags)}{where}. The repository no longer holds exactly what "
                            "produced this result, so the comparison may not reflect it; restore the file or rerun "
                            "under a new name.")
    differ = settings_diff(sa.get("project_settings"), sb.get("project_settings"))
    if differ:  # e.g. the golden file list or the behavior table changed between them
        lines.insert(0, f"WARNING: {a} and {b} were scored under different labkit.yaml settings ({', '.join(differ)}): "
                        "the questions, the scale or the status rules differ, so the difference may not come from the system.")
    if sa.get("pricing_hash") and sb.get("pricing_hash") and sa["pricing_hash"] != sb["pricing_hash"]:
        lines.insert(0, f"WARNING: {a} and {b} were costed from different configs/pricing.yaml versions: "
                        "the cost lines below are not comparable.")
    if bool(sa.get("retrieval_only")) != bool(sb.get("retrieval_only")):  # as the report does (harness._other_kind)
        answer, retrieval = (a, b) if sb.get("retrieval_only") else (b, a)
        lines.insert(0, f"WARNING: {answer} is an answer run and {retrieval} is retrieval-only: cost, calls and "
                        "latency are not comparable; compare with --metric")
        return "\n".join(lines)
    for label, group, key in COST_ROWS:
        va, vb = (sa.get(group) or {}).get(key), (sb.get(group) or {}).get(key)
        ratio = f"  ({vb / va:.2f}x)" if va and vb is not None else ""
        lines.append(f"{label:>20}: {va} -> {vb}{ratio}")
    return "\n".join(lines)


def _load_summary(folder: Path) -> dict:
    path = folder / "summary.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist: run `labkit evaluate --experiment {folder.name}` first")
    return load_summary(path)
