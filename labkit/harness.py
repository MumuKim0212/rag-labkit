"""Batch run, grading, evaluation, and the comparison report. The CLI (labkit run / grade / evaluate / report)
is a thin layer over these functions, so every teammate runs, scores, and summarizes experiments the same way.

Flow per experiment (names: config.experiment_name):
    run_experiment      golden questions -> system -> out/runs.db            (resumable)
    grade_experiment    runs -> judge -> out/grades/<experiment>.jsonl       (resumable)
    evaluate_experiment runs + grades -> results/<experiment>/{summary.json, metrics.jsonl, grades.jsonl[, runs.jsonl]}
    build_report        results/*/summary.json -> results/SUMMARY.md, out/report.html

What the project supplies (registered by name, loaded via `modules:` in labkit.yaml; see labkit.plugins):
    system      a System (labkit.systems), built from cfg["system"], or passed in directly
    judge       grade(item, run, cfg) (labkit.judges), chosen by cfg["judge"]["name"], or passed as grade_fn
    measures    per-question LLM-free columns (labkit.measures), listed under `measures:` in labkit.yaml
    index_dir_fn  optional, (cfg, scope) -> folder with build.json {"calls": [...]}, to report index-building cost;
                  by default built from `index_dir` in labkit.yaml (labkit.index), which is how the CLI gets it
Folders default to the project root's layout (labkit.paths); every function takes `root` to point elsewhere.
"""

import html
import json
import math
import os
import random
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import contract, cost
from .cards import SMOKE_LIMIT, earliest, edited_after, same_card, sections_hash, is_smoke as is_smoke_limit
from .cards import gate as card_gate
from .config import same_model, settings
from .eval import load_jsonl, load_summary
from .eval.runlog import RunLog, is_smoke, without_surrogates
from .freeze import changed_since, file_hash
from .freeze import check_run as check_frozen
from .freeze import file_hashes, recorded_name
from .golden import SPLITS, check_scopes, load_golden, scope_of
from .judges import JUDGES, judge_for
from .measures import run_measures
from .index import index_key
from .paths import check_experiment_name, check_grades_suffix, check_no_case_clash
from .paths import grades_dir as default_grades_dir
from .paths import pricing_path, project_root
from .paths import report_html as default_report_html
from .paths import results_dir as default_results_dir
from .paths import runs_db as default_runs_db
from .project import describe_diff, load_project, run_settings, scoring_settings, settings_diff
from .repro import environment
from .systems import build_system

BASELINE_NAME = "000_baseline"  # default `compare_to`: <split>_000_baseline


class SplitLocked(RuntimeError):
    pass


def guard_test(name: str, final: bool) -> None:
    """Test numbers are produced once, at the final workflow step. Callers pass --final explicitly."""
    if name.casefold().startswith("test") and not final:  # casefold: the name fallback for old rows may say Test
        raise SplitLocked(f"{name}: the test split is locked until the final workflow step; pass --final then")


def _recorded_splits(rows: list[dict]) -> set:
    """The splits `labkit run` recorded in an experiment's rows (config["selection"])."""
    return {((r.get("config") or {}).get("selection") or {}).get("split") for r in rows} - {None}


def experiment_split(experiment: str, rows: list[dict]) -> str:
    """The split an experiment ran on. It is what `labkit run` recorded, not a guess from the name: an experiment
    called `final_try` that ran the test split is still the test split. Only a log without the record (made by
    an older version) falls back to the first part of the name."""
    recorded = _recorded_splits(rows)
    if len(recorded) > 1:
        raise ValueError(f"{experiment} holds runs on more than one split ({sorted(recorded)}); "
                         "rerun each split under its own name")
    return next(iter(recorded)) if recorded else experiment.split("_", 1)[0].casefold()


def guard_experiment(experiment: str, rows: list[dict], final: bool) -> str:
    """guard_test on the split the experiment really ran on; returns that split."""
    split = experiment_split(experiment, rows)
    guard_test(split, final)
    return split


def check_grade(grade, qid, max_score) -> None:
    """The judge contract: `correctness` is a number from 0 to max_score. Anything else would be averaged
    into a score without anyone noticing (a string, None, 7 on a 0-2 scale, NaN)."""
    value = grade.get("correctness") if isinstance(grade, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= max_score:
        raise ValueError(f"the judge returned correctness={value!r} for {qid}; it must be a number from 0 to "
                         f"{max_score} (`max_score` in labkit.yaml). Fix the judge before grading further.")
    for key, bad in _non_finite(grade):  # an extra field (faithfulness, ...) is averaged into the summary too
        raise ValueError(f"the judge returned {key}={bad!r} for {qid}; a number in a grade must be finite "
                         "(not NaN or infinity), at any depth. Fix the judge before grading further.")
    if "calls" in grade and (problem := cost.calls_problem(grade["calls"])):  # evaluate sums them: one bad call stops it
        raise ValueError(f"the judge returned calls that cannot be costed for {qid} ({problem}): record each judge "
                         "call with labkit.cost.record_call. Fix the judge before grading further.")


def _non_finite(value, path: str = ""):
    """(path, value) of every NaN or infinity inside a nested value, e.g. ("calls[0].input_tokens", nan)."""
    if isinstance(value, float) and not math.isfinite(value):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _non_finite(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _non_finite(v, f"{path}[{i}]")


def _finite(value):
    """The value with every NaN or infinity replaced by None: NaN is not JSON, and a system's own diagnostic in the
    run log must not make an export that tools cannot read."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


REPLACE_SECONDS = 5  # how long a writer keeps trying to rename over a file that someone has open


def _atomic_write(path: Path, text: str) -> None:
    """Write through a temporary file and rename it over the target: a crash or a full disk leaves the old whole file
    (or none), never a half-written summary that stops every later command."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # one temporary name per write: with a shared `<file>.tmp`, two writers removed or renamed each other's file
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")  # LF everywhere: Windows would write CRLF into results/
        give_up = time.monotonic() + REPLACE_SECONDS
        while True:
            try:
                os.replace(tmp, path)
                break
            except PermissionError:  # Windows: another reader or writer has the target open for a moment
                if time.monotonic() > give_up:
                    raise
                time.sleep(random.uniform(0.001, 0.02))  # at random, so two waiting writers do not retry in step
    finally:
        tmp.unlink(missing_ok=True)


def _plain(value):
    """A JSON-plain copy of a value (what the log can store), or None when there is none."""
    try:
        return without_surrogates(json.loads(json.dumps(contract.plain(value), default=str)))  # numpy: not "1000"
    except (TypeError, ValueError):
        return None


def _write_jsonl(path: Path, rows) -> None:
    _atomic_write(path, "".join(json.dumps(_finite(row), ensure_ascii=False, default=str) + "\n" for row in rows))


def _mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 4) if values else None


def _quantile(values, q):
    """Linear interpolation between the two nearest values, so the p50 of [1, 2, 3, 4] is 2.5, not 3."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low]) * (position - low), 3)


def _expected_qids(rows: list[dict], split: str, items: dict) -> set:
    """The questions an experiment was meant to cover: the split, and the `--sets` that each run recorded.
    A run without --sets covers the whole split. A `--limit` run still expects all of them."""
    sets = _selected_sets(rows)
    return {qid for qid, item in items.items() if item.get("split") == split and (sets is None or item.get("set") in sets)}


def _selected_sets(rows: list[dict]) -> set | None:
    """The `--sets` the runs recorded, or None when some run covered the whole split."""
    selections = [(r.get("config") or {}).get("selection") or {} for r in rows]
    return None if any(not s.get("sets") for s in selections) else {name for s in selections for name in s["sets"]}


def _require_items(experiment: str, rows: list[dict], items: dict) -> None:
    """Every question an experiment ran must still be in the golden files; scoring needs its targets and set."""
    gone = sorted({r["qid"] for r in rows} - items.keys())
    if gone:
        raise ValueError(f"{experiment} has runs of questions that are no longer in the golden files: "
                         f"{', '.join(gone[:5])}{' …' if len(gone) > 5 else ''}. The golden files changed after the "
                         "run: restore them, or rerun the experiment under a new name.")


def _known_experiments(log: RunLog, root: Path | None) -> set[str]:
    """Experiment names that exist: in this machine's run log, and as folders in results/ (committed by teammates)."""
    folder = Path(default_results_dir(root))
    return {name for name, _ in log.experiments()} | ({p.name for p in folder.iterdir() if p.is_dir()}
                                                     if folder.is_dir() else set())


def _kind(row: dict) -> str:
    return "retrieval-only" if (row.get("config") or {}).get("retrieval_only") else "answer"


def _require_same_settings(experiment: str, rows: list[dict], project: dict, doing: str) -> None:
    """Grading divides by max_score and evaluating reads the questions from `golden.files` as they are now, so both
    must be what the run recorded. (The behavior table is not checked: re-evaluating after fixing it is allowed.)"""
    for row in rows:
        before = (row.get("config") or {}).get("project_settings")
        if before is not None:  # rows from an older labkit carry no record: nothing to compare
            now = run_settings(project)
            if settings_diff(before, now):
                raise ValueError(f"labkit.yaml changed since {experiment} ran ({describe_diff(before, now)}), so it cannot "
                                 f"be {doing}: the result would mix two setups. Restore labkit.yaml, or rerun "
                                 "the experiment under a new name.")
            return


# ---------------------------------------------------------------- run

def run_experiment(cfg: dict, split: str, *, experiment: str, sets=None, limit: int | None = None,
                   retrieval_only: bool = False, final: bool = False, db: Path | None = None,
                   golden_dir: Path | None = None, system=None, root: Path | None = None, warn=None) -> dict:
    """`warn(message)` is called, before any question runs, with each warning (a card that names another config); a
    caller that passes none finds them under result["warnings"]."""
    check_experiment_name(experiment)
    guard_test(split, final)
    if limit is not None and limit < 1:  # 0 or -1 would slip under the card gate's smoke-test exemption
        raise ValueError(f"--limit must be at least 1 (got {limit}); leave it out to run every question")
    if sets is not None and not list(sets):  # an empty list reads as no filter below and would run, and bill, the whole split
        raise ValueError("--sets names no golden set (the value is empty or only commas); leave it out to run the whole split")
    if system is None and (not isinstance(cfg.get("system"), str) or not cfg["system"].strip()):
        raise ValueError(f"{cfg.get('config_file') or cfg.get('name')}: the config has no `system:` naming a registered "
                         "system (a string); add it to the config or to the baseline it builds on")
    answer_model, judge_model = ((cfg.get(key) or {}).get("model") for key in ("answer", "judge"))
    if not retrieval_only and same_model(answer_model, judge_model):  # step 1 checks this once; runs go on for months
        raise ValueError(f"judge and answer both use {judge_model}: a model grading its own answers is not an "
                         "independent judge. Set `judge.model` to another model in the config.")
    judge_name = (cfg.get("judge") or {}).get("name")
    if not retrieval_only and judge_name is not None and judge_name not in JUDGES:
        # found only at `labkit grade`, the config was already frozen by then: fixing the name meant paying for a rerun
        raise ValueError(f"{cfg.get('config_file') or cfg.get('name')}: judge.name {judge_name!r} is not a registered "
                         f"judge (registered: {sorted(JUDGES) or 'none'}). Register it (labkit.judges.register_judge, in "
                         "a module listed under `modules:` in labkit.yaml) before the run, or fix the name.")
    root = Path(root) if root else project_root()
    missing = None if retrieval_only else _missing_judge_prompt(cfg, root)
    if missing:  # the prompt is hashed only if it exists when the run starts: without it its freeze was silently off
        raise ValueError(f"judge.prompt names {missing}, which does not exist: create it before the first run (the "
                         "judge prompt is frozen from then on), or remove the key if the judge uses no prompt file")
    project = load_project(root)
    card = card_gate(cfg, root, limit, answer_run=not retrieval_only)  # variant configs need a written card (labkit.cards)
    items = load_golden(split=split, sets=sets, golden_dir=golden_dir, root=root)
    if not items:
        raise ValueError(f"no golden items for split={split!r} sets={sets!r}")
    check_scopes(items, cfg)
    log = RunLog(db or default_runs_db(root))
    known = _known_experiments(log, root)
    check_no_case_clash(experiment, known)
    for other in sorted(known):  # `<other>__<x>` is where `grade --experiment <other> --suffix __<x>` writes its grades
        if experiment.casefold().startswith(other.casefold() + "__"):  # one file when the file system ignores case
            raise ValueError(f"{experiment} would share the grades file of {other} with the suffix "
                             f"{experiment[len(other):]}: choose a name that is not another experiment's name plus "
                             "two underscores")
    hashes = file_hashes(cfg, project, root)
    earlier = log.load(experiment)
    other = _recorded_splits(earlier) - {split}
    if other:  # one name, one split: the split of an experiment is read back from its rows
        raise ValueError(f"{experiment} already holds runs on the {sorted(other)} split; use another --name for {split}")
    smoke = is_smoke_limit(limit)  # a few questions to read the output: logged, but part of no result
    real_earlier = [r for r in earlier if not is_smoke(r)]
    held = {_kind(r) for r in real_earlier} - {"retrieval-only" if retrieval_only else "answer"}
    if held:  # the kinds measure different things and are graded differently: resuming one as the other mixes them
        raise ValueError(f"{experiment} already holds {' and '.join(sorted(held))} runs; a retrieval-only run and an "
                         "answer run are different experiments: use another --name")
    check_frozen(cfg, hashes, experiment, real_earlier, root, run_settings(project))  # labkit.freeze
    done = log.done_qids(experiment)  # smoke rows do not count: the real run repeats those questions
    todo = [i for i in items if i["qid"] not in done][:limit]
    card_warning = None
    if card and not card.get("config_named", True):  # a warning, not a refusal: card headers may be written by hand
        card_warning = (f"{card['file']} has no `Config:` header line naming {cfg.get('config_file')}: a card must "
                        "belong to the config that uses it (a variant that borrows another change's finished card "
                        "passes the gate without describing its own change)")
        if warn is not None:
            warn(card_warning)  # now, before the batch is paid for, not in the report after it
    system = system or build_system(cfg)
    if retrieval_only and not callable(getattr(system, "retrieve", None)):  # else every question is an AttributeError row
        raise ValueError(f"{type(system).__name__} has no retrieve(question, scope, steps=None): --retrieval-only runs "
                         "the search alone, so the system must implement it (labkit.systems)")
    ready = getattr(system, "ready", None)
    if callable(ready):  # e.g. the index must exist: stop before any question runs, not half-way through
        answer = ready()
        if not (isinstance(answer, tuple) and len(answer) == 2):
            raise ValueError(f"{type(system).__name__}.ready() must return (ok: bool, detail: str), got {answer!r}")
        if not answer[0]:
            raise RuntimeError(f"the system is not ready: {answer[1]}")
    expected_index = index_key(cfg, project)
    run_config = {**cfg, "environment": environment(root), "retrieval_only": retrieval_only, "file_hashes": hashes,
                  "project_settings": run_settings(project),  # labkit.yaml: golden list and max_score at this run
                  "selection": {"split": split, "sets": sorted(sets) if sets else None}}  # what `evaluate` must find
    if card:
        run_config["card_check"] = card
    if smoke:
        run_config["smoke"] = True
    if expected_index:
        run_config["index_key"] = expected_index

    def one(item):
        scope = scope_of(item, cfg)
        start = time.perf_counter()
        try:
            if retrieval_only:
                steps = {"calls": []}
                units = system.retrieve(item["question"], scope, steps)
                result = {"status": "retrieval_only", "context": units, "n_searches": 1, "n_llm_calls": 0,
                          "steps": steps,
                          "searches": [{"query": item["question"], "chunk_ids": [u["chunk_id"] for u in units]}]}
            else:
                result = system.run(item["question"], scope)
        except NotImplementedError:
            raise
        except Exception as e:  # one failing question must not stop the batch; rerun resumes it
            result = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        if not isinstance(result, dict) or not isinstance(result.get("status"), str) or not result["status"].strip():
            # a result with no status would be saved as NULL, counted as finished, graded and scored: it is a failed
            # question instead, and whatever the call cost (its steps) is kept
            steps = result.get("steps") if isinstance(result, dict) else None
            result = {"status": "error", "steps": steps if isinstance(steps, dict) else None,
                      "error": f"the system returned {type(result).__name__} with no `status` (a non-empty text such "
                               "as answered, abstained or error): fix the system"}
        elif result["status"] != "error" and result["status"].strip().casefold() == "error":
            # `Error`, `ERROR` and `error ` mean the reserved word; as a word of their own they were graded and scored
            # as finished answers (complete: true)
            result = {**result, "status": "error", "error": result.get("error") or f"the system returned status "
                                                                                    f"{result['status']!r}"}
        used =(result.get("steps") or {}).get("index_key") if isinstance(result.get("steps"), dict) else None
        if expected_index and used and used != expected_index:  # a variant quietly reused another index
            result = {"status": "error", "steps": result.get("steps"),  # the calls were made and paid for: keep them
                      "error": f"index mismatch: the config's index_key is {expected_index} but "
                               f"the system used {used}; build the index for this config"}
        result = contract.plain(result)  # numpy counts and tuples: stored as they are, they broke evaluate
        problem = contract.check_result(result)
        if problem:  # a shape evaluate, grade or show cannot read: a failed question now, not a traceback later
            steps = result.get("steps")
            own = f"; the system's own error was: {result.get('error')}" if result["status"] == "error" else ""
            result = {"status": "error",  # what the calls cost is kept when it can be costed
                      "steps": steps if isinstance(steps, dict) and not cost.steps_problem(steps) else None,
                      "error": f"the system returned a result labkit cannot use ({problem}): fix the system{own}"}
        problem = cost.steps_problem(result.get("steps"))
        if problem:  # a call list that cannot be costed would stop the batch here or at evaluate: a failed question instead
            result = {"status": "error", "steps": None,
                      "error": f"the recorded calls cannot be costed ({problem}): add every call to steps['calls'] "
                               "with labkit.cost.record_call"}
        # labkit's own numbers, not the system's: one source with the summary's totals (steps.calls) and the clock
        result["latency_s"] = round(time.perf_counter() - start, 3)
        calls = cost.calls_of(result.get("steps"))
        result["input_tokens"] = sum(c.get("input_tokens", 0) for c in calls)  # a call may omit a count
        result["output_tokens"] = sum(c.get("output_tokens", 0) for c in calls)
        if not (retrieval_only and "n_llm_calls" in result):  # the retrieval-only result says 0 itself
            result["n_llm_calls"] = sum(1 for c in calls if c.get("kind") != "embedding")
        result["n_searches"] = contract.search_count(result)
        result.update(system=cfg["system"], config=run_config, question=item["question"])
        try:
            log.save(result, experiment=experiment, qid=item["qid"])
        except Exception as e:  # the call is paid for: a result the log cannot store (a numpy number, a non-text answer)
            # must not abort the batch and lose it, so it becomes a failed question that keeps what the call cost
            result = {"status": "error", "system": cfg["system"], "config": run_config, "question": item["question"],
                      "steps": _plain(result.get("steps")),
                      "error": without_surrogates(f"the result could not be saved ({type(e).__name__}: {e}): fix the system"),
                      **{k: result[k] for k in ("latency_s", "input_tokens", "output_tokens", "n_llm_calls")
                         if isinstance(result.get(k), (int, float)) and not isinstance(result.get(k), bool)}}
            log.save(result, experiment=experiment, qid=item["qid"])
        return result["status"]

    statuses = {}
    with ThreadPoolExecutor(max_workers=(cfg.get("run") or {}).get("concurrency", 1)) as pool:
        futures = [pool.submit(one, item) for item in todo]
        try:
            for future in as_completed(futures):
                status = future.result()
                statuses[status] = statuses.get(status, 0) + 1
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    result = {"experiment": experiment, "skipped_done": len(done), "ran": len(todo), "statuses": statuses,
              "smoke": smoke}
    if card_warning and warn is None:  # a library caller that passed no `warn` gets it in the result
        result["warnings"] = [card_warning]
    return result


# ---------------------------------------------------------------- grade

def _missing_judge_prompt(cfg: dict, root: Path) -> str | None:
    """`judge.prompt` as written when it names no file (relative to the project root, or absolute), else None."""
    prompt = (cfg.get("judge") or {}).get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None
    return None if (Path(root) / prompt).is_file() else prompt


def _check_judge_prompt(experiment: str, runs: list[dict], root: Path | None) -> str | None:
    """Refuse to grade with a judge prompt other than the one the runs recorded (it is among their file hashes when the
    file existed at run time). The judge is fixed at step 1; grading half an experiment, or a regrade, with an edited
    prompt puts two standards in one result. Returns the prompt's hash now, for the grade rows (None: no prompt)."""
    config = (runs[0]["config"] or {}) if runs else {}
    prompt = (config.get("judge") or {}).get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return None
    here = Path(root or project_root())
    path = here / prompt
    now = file_hash(path) if path.is_file() else None
    key = recorded_name(here, prompt)  # an absolute or `../project/…` spelling is recorded as the project-relative name
    recorded = False
    for name, digest in (config.get("file_hashes") or {}).items():
        if key is not None and os.path.normpath(name) == os.path.normpath(key):
            recorded = True
            if now is None or now != digest:
                raise ValueError(f"the judge prompt {name} {'was deleted' if now is None else 'changed'} since "
                                 f"{experiment} ran: grading with another prompt would put two standards in one "
                                 "result. Restore the file, or run the experiment again under a new name.")
    if now is None and not recorded:  # grades with judge_prompt_hash None: nothing would show which prompt graded them
        raise ValueError(f"judge.prompt names {prompt}, which does not exist: create the judge prompt before grading "
                         f"{experiment}")
    return now


def grade_experiment(experiment: str, *, grade_fn=None, suffix: str = "", final: bool = False, db: Path | None = None,
                     golden_dir: Path | None = None, grades_dir: Path | None = None,
                     root: Path | None = None) -> dict:
    """Grade the latest run per question. `suffix` writes a separate file (e.g. "__regrade1" for judge noise).

    The judge is the one the runs' config names (cfg["judge"]["name"], labkit.judges) unless `grade_fn` is given.
    It should differ from the answer model and stay fixed; its model and prompt are recorded in each row."""
    check_experiment_name(experiment)
    check_grades_suffix(suffix)
    log = RunLog(db or default_runs_db(root))
    if suffix and (experiment + suffix).casefold() in {k.casefold() for k in _known_experiments(log, root)}:
        raise ValueError(f"{experiment + suffix} is an experiment of its own, and {experiment} with suffix {suffix} "
                         "would write its grades file: choose another suffix (for example __regrade1)")
    rows = log.load(experiment)
    guard_experiment(experiment, rows, final)
    if not rows:  # a typo in the name: say so, instead of creating an empty grades file and reporting graded_now 0
        known = ", ".join(name for name, _ in log.experiments()) or "none"
        raise ValueError(f"no runs logged for {experiment}; logged experiments: {known}")
    project = load_project(root)
    max_score = project["max_score"]
    _require_same_settings(experiment, [r for r in rows if not is_smoke(r)], project, "graded")
    items = {i["qid"]: i for i in load_golden(golden_dir=golden_dir, root=root)}
    runs = [r for r in log.latest(experiment) if r["status"] != "retrieval_only"]
    _require_items(experiment, runs, items)
    prompt_hash = _check_judge_prompt(experiment, runs, root)
    if grade_fn is None and runs:
        grade_fn = judge_for(runs[0]["config"])
    path = Path(grades_dir or default_grades_dir(root)) / f"{experiment}{suffix}.jsonl"
    # a run is graded when a row names its run_id: a row for the same qid that graded an earlier run (the run log was
    # lost, a question was rerun) says nothing about this answer
    graded = {r.get("run_id") for r in load_jsonl(path)} if path.exists() else set()
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8", newline="\n") as f:  # LF on Windows too: these files get copied into results/
        for run in runs:
            if run["run_id"] in graded:
                continue
            judge_cfg = (run["config"] or {}).get("judge") or {}
            grade = grade_fn(items[run["qid"]], run, run["config"])
            check_grade(grade, run["qid"], max_score)  # before the row is written: a bad grade is never saved
            row = {"qid": run["qid"], "run_id": run["run_id"], **grade,
                   "judge_model": judge_cfg.get("model"), "judge_prompt": judge_cfg.get("prompt"),
                   "judge_prompt_hash": prompt_hash,  # which text of the prompt graded this answer
                   "created_at": datetime.now().isoformat(timespec="seconds")}
            f.write(json.dumps(without_surrogates(row), ensure_ascii=False) + "\n")
            f.flush()
            n += 1
    return {"grades": str(path), "graded_now": n, "already_graded": sum(1 for r in runs if r["run_id"] in graded)}


# ---------------------------------------------------------------- evaluate

COST_COLUMNS = ("usd", "input_tokens", "output_tokens", "thinking_tokens", "n_llm_calls", "n_searches", "latency_s")
NOT_METRICS = {"qid", "correctness", *COST_COLUMNS}  # columns that are not averaged into summary["metrics"]
GRADE_NOT_METRICS = {"qid", "run_id", "calls", "created_at"}


def question_metrics(item: dict, run: dict, prices: dict, project: dict | None = None) -> dict:
    """One metrics.jsonl row: identity, the project's measures (labkit.measures), then cost and latency."""
    project = project if project is not None else load_project()
    row = {"qid": item["qid"], "set": item.get("set") or "all", "status": run["status"]}
    row.update({k: None if isinstance(v, float) and not math.isfinite(v) else v  # NaN is not valid JSON
                for k, v in run_measures(item, run, project).items()})
    run_cost = cost.totals(cost.calls_of(run.get("steps")), prices)
    row.update(usd=round(run_cost["usd"], 6), input_tokens=run.get("input_tokens"),
               output_tokens=run.get("output_tokens"), thinking_tokens=run_cost["thinking_tokens"],
               n_llm_calls=run.get("n_llm_calls"), n_searches=run.get("n_searches"), latency_s=run.get("latency_s"))
    return row


def _is_number(value) -> bool:
    """A value that may be averaged: booleans count (their mean is a rate), NaN and infinity do not (the mean would
    be NaN, written to summary.json as a bare token that is not valid JSON)."""
    return isinstance(value, (int, float)) and math.isfinite(value)


def metric_means(rows: list[dict]) -> dict:
    """Mean of every numeric column that some row has a value for (None and missing values are skipped).
    Rows without targets lack the retrieval columns, so those means cover only the rows that have them."""
    columns = {k for row in rows for k, v in row.items() if k not in NOT_METRICS and _is_number(v)}
    return {k: _mean([row[k] for row in rows if _is_number(row.get(k))]) for k in sorted(columns)}


def _index_dir_fn(project: dict, root: Path | None):
    """(cfg, scope) -> folder, from `index_dir` in labkit.yaml; None when the project did not say where builds are."""
    pattern = project.get("index_dir")
    if not pattern:
        return None
    return lambda cfg, scope: Path(root or project_root()) / pattern.format(index_key=index_key(cfg, project) or "",
                                                                          scope=scope)


def _index_build_usd(cfg: dict, scopes: set, prices: dict, index_dir_fn):
    if index_dir_fn is None:
        return None
    total = 0.0
    for scope in scopes:
        build = Path(index_dir_fn(cfg, scope)) / "build.json"
        if not build.exists():  # one scope unknown makes the total unknown: a partial sum would pass for the whole
            return None
        total += cost.totals(json.loads(build.read_text(encoding="utf-8")).get("calls", []), prices)["usd"]
    return round(total, 6) if scopes else None


def _first_card_record(rows: list[dict]) -> dict | None:
    """The card record stored by the first real run that passed the card gate (callers leave smoke rows out)."""
    return next((r["config"]["card_check"] for r in rows if (r.get("config") or {}).get("card_check")), None)


def _card_check(own: dict | None, log: RunLog, results: Path, root: Path) -> dict | None:
    """Has sections 1–4 of the experiment's card changed since the card was first used? "First" is the oldest record
    of that card in the whole run log and in the committed summaries, not this experiment's own first run: an
    LLM-free run and the answer run of one card are two experiments, and the edit between them is the one to catch."""
    if not own:
        return None
    candidates = list(log.card_records(own["file"], same_card))
    for path in sorted(results.glob("*/summary.json")):  # teammates' experiments, whose runs are not in this machine's log
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        check = summary.get("card_check") or {}
        if check.get("file") and same_card(check["file"], own["file"]) and check.get("hash_at_first_run"):
            # dated by when the card was first used, as its own summary recorded; a summary from before that field
            # falls back to the time it was written (evaluation time: later than the use, so an approximation)
            candidates.append((check.get("first_run_at") or summary.get("created_at") or "9999",
                               summary.get("experiment") or path.parent.name, check["hash_at_first_run"]))
    return edited_after(own["file"], earliest(candidates), root)


def _require_unfinished(summary_path: Path, split: str) -> None:
    """--overwrite where a test result is involved (the experiment is a test run, or the folder already holds a test
    summary, whatever the new run's split) is for finishing an evaluation made before grading or a rerun was complete
    (`complete: false`). A complete test result is the one number the project reports: replacing it after seeing it
    would make "test split once" a habit rather than a rule. A summary that cannot be read is not known to be
    unfinished, so it is refused too: remove the folder to declare it invalid."""
    if not summary_path.exists():
        return
    try:
        existing = json.loads(summary_path.read_text(encoding="utf-8"))
        complete, was = existing.get("complete"), existing.get("split")
    except (json.JSONDecodeError, AttributeError, OSError):
        complete, was = None, None
    if was in SPLITS and was != split:  # an unfinished test result turned into a finished dev one, or the reverse
        raise SplitLocked(f"{summary_path.parent} holds a {was} result and this run is on the {split} split: --overwrite "
                          f"finishes a result, it does not replace a {was} result with a {split} one. Use another "
                          "--name, or remove the folder in a reviewed commit and say why in report/PROCESS.md.")
    if split != "test" and was != "test":
        return  # dev over dev: the rule is the documented one, "never after a decision", kept by hand
    if complete is not False:
        raise SplitLocked(f"{summary_path.parent} holds a complete (or unreadable) test result, and a test result is "
                          "never overwritten: the test numbers are produced once. If it is invalid, remove the folder "
                          "in a reviewed commit and say why in report/PROCESS.md; otherwise run the system under a "
                          "new name.")


def evaluate_experiment(experiment: str, *, with_runs: bool = False, final: bool = False, overwrite: bool = False,
                        db: Path | None = None, golden_dir: Path | None = None, grades_dir: Path | None = None,
                        results_dir: Path | None = None, prices: dict | None = None, index_dir_fn=None,
                        root: Path | None = None) -> dict:
    check_experiment_name(experiment)
    log = RunLog(db or default_runs_db(root))
    attempts = log.load(experiment)  # every row: finished, failed, superseded, and smoke tests
    real = [r for r in attempts if not is_smoke(r)]  # what the experiment consists of
    split = guard_experiment(experiment, attempts, final)
    project = load_project(root)
    max_score = project["max_score"]
    index_dir_fn = index_dir_fn or _index_dir_fn(project, root)
    _require_same_settings(experiment, real, project, "evaluated")
    out_dir = Path(results_dir or default_results_dir(root)) / experiment
    if (out_dir / "summary.json").exists() and not overwrite:
        raise FileExistsError(f"{out_dir} exists; a folder used in a decision is never overwritten "
                              "(rerun under a new name), otherwise pass --overwrite")
    if overwrite:
        _require_unfinished(out_dir / "summary.json", split)
    prices = prices or cost.load_prices(pricing_path(root))
    runs = log.latest(experiment)
    if not runs:
        if real:
            raise ValueError(f"every question of {experiment} failed ({len(real)} attempts); "
                             f"first error: {real[0].get('error')}")
        if attempts:
            raise ValueError(f"{experiment} has only smoke-test runs (--limit {SMOKE_LIMIT} or less), which belong to "
                             f"no result: run it without --limit, or with a limit above {SMOKE_LIMIT}")
        raise ValueError(f"no runs for {experiment}")
    failed = {r["qid"] for r in real if r["status"] == "error"} - {r["qid"] for r in runs}
    errors = len(failed)
    items = {i["qid"]: i for i in load_golden(golden_dir=golden_dir, root=root)}
    if len({_kind(r) for r in runs}) > 1:  # `cfg` below is the first row's: it must speak for all of them
        raise ValueError(f"{experiment} mixes retrieval-only and answer runs (an older labkit allowed it): "
                         "rerun each kind under its own name")
    _require_items(experiment, runs, items)
    cfg = runs[0]["config"]  # a real run: a smoke test never supplies the config, environment, or file hashes
    expected = _expected_qids(real, split, items)
    never_ran = expected - {r["qid"] for r in runs} - failed
    unexpected = {r["qid"] for r in runs} - expected  # only a golden file edited after the run puts a question here
    selected = _selected_sets(real)  # the sets together cover the whole split when no set of the split is left out
    sets_record = None if selected is None or selected >= {i.get("set") for i in items.values() if i.get("split") == split} \
        else sorted(selected)

    metrics = [question_metrics(items[r["qid"]], r, prices, project) for r in runs]
    grades_path = Path(grades_dir or default_grades_dir(root)) / f"{experiment}.jsonl"
    grade_rows = load_jsonl(grades_path) if grades_path.exists() else []
    by_run = {g.get("run_id"): g for g in grade_rows}
    grades = {r["qid"]: by_run[r["run_id"]] for r in runs if r["run_id"] in by_run}  # only grades OF the scored runs
    for m in metrics:
        grade = grades.get(m["qid"])
        if grade:
            check_grade(grade, m["qid"], max_score)  # a grades file edited or written by another judge is checked too
            m["correctness"] = grade.get("correctness")
            for key, value in grade.items():  # extra numeric judge fields (e.g. faithfulness) become columns too
                if key not in m and key not in GRADE_NOT_METRICS and _is_number(value):
                    m[key] = value

    judge_calls = [c for g in grade_rows for c in g.get("calls", [])]  # every judge call paid for, replaced grades too
    regrade_calls = [c for p in sorted(Path(grades_dir or default_grades_dir(root)).glob(f"{experiment}__regrade*.jsonl"))
                     for g in load_jsonl(p) for c in g.get("calls", [])]  # noise measurements: paid for, in no score
    run_calls = [c for r in runs for c in cost.calls_of(r.get("steps"))]
    run_cost, judge_cost = cost.totals(run_calls, prices), cost.totals(judge_calls, prices)
    regrade_cost = cost.totals(regrade_calls, prices)
    graded = [m for m in metrics if m.get("correctness") is not None]
    n = len(runs)
    counted = {r["run_id"] for r in runs}  # the run each question is scored on
    wasted = cost.totals([c for r in attempts if r["run_id"] not in counted for c in cost.calls_of(r.get("steps"))],
                         prices)  # failed, superseded, and smoke-test attempts: paid for, but in no score
    git_of = [((r["config"] or {}).get("environment") or {}).get("git") or {} for r in runs]
    commits = sorted({g.get("commit") or "" for g in git_of} - {""})
    # the code each scored run came from: the commit, plus a hash of any uncommitted edits (labkit.repro);
    # more than one version means a mixed result
    code_versions = sorted({f"{g['commit']}+{g['changes']}" if g.get("changes") else g["commit"]
                            for g in git_of if g.get("commit")})

    # Scores average the questions that ran and were graded. A failed question is not scored as 0 (that would mix
    # a system's wrong answers with an outage), so completeness is stated next to the numbers instead.
    incomplete = []
    if never_ran:
        incomplete.append(f"{len(never_ran)} of {len(expected)} expected questions never ran")
    if unexpected:
        incomplete.append(f"{len(unexpected)} scored questions are not in the {split} split of the golden files now "
                          f"({', '.join(sorted(unexpected)[:5])}{' …' if len(unexpected) > 5 else ''}): "
                          "the golden files changed after the run")
    if failed:
        incomplete.append(f"{len(failed)} questions failed on every attempt: {', '.join(sorted(failed)[:5])}"
                          + (" …" if len(failed) > 5 else ""))
    if not cfg.get("retrieval_only") and len(graded) < n:
        incomplete.append(f"{n - len(graded)} of {n} answers are not graded")

    def score_block(rows):
        scored = [m["correctness"] for m in rows if m.get("correctness") is not None]
        return {"n": len(rows),
                "correctness_mean": _mean([c / max_score for c in scored]),
                "fully_correct": _mean([int(c == max_score) for c in scored]),
                "metrics": metric_means(rows)}

    overall = score_block(metrics)
    summary = {
        "experiment": experiment,
        "card": cfg.get("card"),
        "split": split,
        "sets": sets_record,  # None: the whole split (including a split run set by set)
        "system": cfg["system"],
        "retrieval_only": cfg.get("retrieval_only", False),
        "complete": not incomplete,  # False: the numbers below cover only part of what was meant to run
        "incomplete": incomplete,
        "n_expected": len(expected),
        "n_questions": n,
        "n_errors": errors,
        "n_graded": len(graded),
        "max_score": max_score,
        "project_settings": scoring_settings(project),  # labkit.yaml as it was when scored (compare and report read it)
        "pricing_hash": file_hash(pricing_path(root)) if pricing_path(root).is_file() else None,
        "index_key": cfg.get("index_key"),
        "file_hashes": cfg.get("file_hashes"),
        "file_check": {"changed_since_run": changed_since(cfg.get("file_hashes"), Path(root or project_root()))},
        "card_check": _card_check(_first_card_record(real), log, out_dir.parent, Path(root or project_root())),
        "compare_to": cfg.get("compare_to") or f"{split}_{BASELINE_NAME}",
        "changed": cfg.get("changed", {}),
        "changed_from_base": cfg.get("changed_from_base", cfg.get("changed", {})),
        "description": cfg.get("description"),
        "config_file": cfg.get("config_file"),
        "config_hash": cfg.get("config_hash"),        "config": settings(cfg),
        "run": cfg.get("run"),  # execution settings (workers); not part of config_hash
        "environment": cfg.get("environment"),
        "environment_check": {"git_commits": commits, "code_versions": code_versions,
                              "mixed_commits": len(code_versions) > 1},
        "scores": {"correctness_mean": overall["correctness_mean"], "fully_correct": overall["fully_correct"]},
        "metrics": overall["metrics"],  # mean of every numeric measure column (labkit.measures) and judge field
        "by_set": {s: score_block([m for m in metrics if m["set"] == s]) for s in sorted({m["set"] for m in metrics})},
        "cost": {
            "usd": round(run_cost["usd"], 6),  # the runs that are scored
            "wasted_usd": round(wasted["usd"], 6),  # failed and superseded attempts
            "total_usd": round(run_cost["usd"] + wasted["usd"], 6),  # what the experiment cost to run
            "usd_per_question": round(run_cost["usd"] / n, 6),
            "usd_by_model": {k: round(v, 6) for k, v in run_cost["usd_by_model"].items()},
            "judge_usd": round(judge_cost["usd"], 6),  # grading the scored runs; not in total_usd
            "judge_regrade_usd": round(regrade_cost["usd"], 6),  # `labkit grade --suffix ...` passes (judge noise)
            "index_build_usd": _index_build_usd(cfg, {scope_of(items[r["qid"]], cfg) for r in runs}, prices,
                                                index_dir_fn),
            "input_tokens": run_cost["input_tokens"],
            "output_tokens": run_cost["output_tokens"],
            "thinking_tokens": run_cost["thinking_tokens"],
            "tokens_per_question": round((run_cost["input_tokens"] + run_cost["output_tokens"]) / n, 1),
        },
        "calls": {"llm": run_cost["llm_calls"], "llm_per_question": round(run_cost["llm_calls"] / n, 2),
                  "searches_per_question": _mean([r.get("n_searches") for r in runs]),
                  "judge": judge_cost["llm_calls"]},
        "latency_s": {"p50": _quantile([r.get("latency_s") for r in runs], 0.5),
                      "p90": _quantile([r.get("latency_s") for r in runs], 0.9)},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    try:
        summary_text = json.dumps(summary, ensure_ascii=False, indent=2, default=str, allow_nan=False)
    except ValueError as e:  # NaN or infinity in a cost or count: refuse rather than write what is not JSON
        raise ValueError(f"the summary of {experiment} would contain NaN or infinity ({e}); a call record or a measure "
                         "has a non-finite number: fix the system or judge that produced it") from e
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "metrics.jsonl", metrics)
    if grades:
        _write_jsonl(out_dir / "grades.jsonl", grades.values())
    else:  # no grade of a scored run: a file from an earlier evaluation would sit beside a summary that says n_graded 0
        (out_dir / "grades.jsonl").unlink(missing_ok=True)
    if with_runs or (out_dir / "runs.jsonl").exists():  # an existing export is refreshed, never left stale
        _write_jsonl(out_dir / "runs.jsonl", log.load(experiment))
    _atomic_write(out_dir / "summary.json", summary_text)  # last: its existence is what says "evaluated"
    return summary


# ---------------------------------------------------------------- report

COLUMNS = [  # (header, path in summary.json, format, higher_is_better)
    ("Score", "scores.correctness_mean", "{:.3f}", True),
    ("Fully correct", "scores.fully_correct", "{:.1%}", True),
    ("Status match", "metrics.status_ok", "{:.1%}", True),
    ("Citation on target", "metrics.citation_on_target", "{:.1%}", True),
    ("page_hit@5", "metrics.page_hit@5", "{:.3f}", True),
    ("evid@5", "metrics.evid@5", "{:.3f}", True),
    ("gold@5", "metrics.gold@5", "{:.2f}", True),
    ("USD/question", "cost.usd_per_question", "{:.5f}", False),
    ("Tokens/question", "cost.tokens_per_question", "{:.0f}", False),
    ("LLM calls/question", "calls.llm_per_question", "{:.2f}", False),
    ("p50 s", "latency_s.p50", "{:.2f}", False),
    ("p90 s", "latency_s.p90", "{:.2f}", False),
]


def report_columns(project: dict) -> list[tuple]:
    """COLUMNS, or the project's `report.columns` (labkit.project): a string is a path into summary.json
    (header = its last part, format {:.3f}, higher is better); a mapping gives header, path, format, higher_is_better.
    Metric names must not contain a dot, since paths are dot-separated."""
    spec = project["report"]["columns"]
    if not spec:
        return COLUMNS
    columns = []
    for entry in spec:
        if isinstance(entry, str):
            entry = {"path": entry}
        if "path" not in entry:
            raise ValueError(f"report.columns entry needs a `path`: {entry!r}")
        columns.append((entry.get("header") or entry["path"].rsplit(".", 1)[-1], entry["path"],
                        entry.get("format", "{:.3f}"), bool(entry.get("higher_is_better", True))))
    return columns


def _get(summary: dict, path: str):
    value = summary
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _fmt(value, fmt):
    return "–" if value is None else fmt.format(value)


def load_summaries(results_dir: Path | None = None, include_test: bool = False, root: Path | None = None) -> list[dict]:
    summaries = []
    for path in sorted(Path(results_dir or default_results_dir(root)).glob("*/summary.json")):
        s = load_summary(path)
        if include_test or s.get("split") != "test":
            summaries.append(s)
    return summaries


def _changed_text(s: dict) -> str:
    if not s.get("changed"):
        return "baseline config"
    return ", ".join(f"{k}: {v['from']} → {v['to']}" for k, v in s["changed"].items())


def _md(text) -> str:
    """Text for one cell of a Markdown table: a line break would end the row and an unescaped `|` would add a column."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _sets_text(s: dict) -> str:
    return ", ".join(s["sets"]) if s.get("sets") else "the whole split"


def _not_comparable(s: dict, base: dict) -> list[str]:
    """Why a difference from `base` would be a made-up number: scored under other labkit.yaml settings, or on other
    questions (`--sets a` against the whole split gave `1.000 (+0.250)` for an identical config and system)."""
    reasons = []
    differ = settings_diff(s.get("project_settings"), base.get("project_settings"))
    if differ:
        reasons.append(f"different labkit.yaml settings ({', '.join(differ)})")
    if s.get("split") and base.get("split") and s["split"] != base["split"]:  # a test result that names a dev baseline
        reasons.append(f"different split ({s['split']} vs {base['split']})")
    if (s.get("sets") or None) != (base.get("sets") or None):
        reasons.append(f"different sets ({_sets_text(s)} vs {_sets_text(base)})")
    return reasons


def _other_kind(s: dict, base: dict) -> bool:
    """A retrieval-only result against an answer run (or the reverse): cost, tokens, calls, latency and scores are
    not the same kind of number, so only the metrics.* columns are compared."""
    return bool(s.get("retrieval_only")) != bool(base.get("retrieval_only"))


def _n_text(s: dict, flags=()) -> str:
    """The question count; an incomplete experiment shows how many of the expected ones it covers, and one whose
    input files or card were edited after it ran says so (the summary's own checks are only as of its evaluation)."""
    text = f"{s['n_questions']}/{s.get('n_expected', '?')} incomplete" if s.get("complete") is False \
        else str(s["n_questions"])
    return text + (f" ⚠ {'; '.join(flags)}" if flags else "")


def live_flags(s: dict, root: Path) -> list[str]:
    """What has changed since this experiment was evaluated, checked against the files as they are now."""
    flags = []
    if changed_since(s.get("file_hashes"), root):
        flags.append("inputs changed since the run")
    check = s.get("card_check") or {}
    if check.get("file") and check.get("hash_at_first_run"):
        path = root / check["file"]
        if (sections_hash(path) if path.exists() else None) != check["hash_at_first_run"]:
            flags.append("card sections 1–4 edited since it was first used")
    return flags


def build_report(results_dir: Path | None = None, md_path: Path | None = None, html_path: Path | None = None,
                 include_test: bool = False, final: bool = False, columns=None, root: Path | None = None) -> dict:
    if include_test:
        guard_test("test", final)
    columns = columns or report_columns(load_project(root))
    results_dir = Path(results_dir or default_results_dir(root))
    html_path = Path(html_path or default_report_html(root))
    summaries = load_summaries(results_dir, include_test)
    by_name = {s["experiment"]: s for s in summaries}
    edited = {s["experiment"]: live_flags(s, Path(root or project_root()))
              for s in summaries}  # live: the files and the card as they are now against what the run recorded
    unlike = {}  # experiment -> why it cannot be compared with its comparison baseline
    for s in summaries:
        base = by_name.get(s.get("compare_to"))
        if base and base is not s and _not_comparable(s, base):
            unlike[s["experiment"]] = _not_comparable(s, base)
    repriced = {s["experiment"] for s in summaries  # costed from another pricing.yaml than its comparison baseline
                if (base := by_name.get(s.get("compare_to"))) and base is not s and s.get("pricing_hash")
                and base.get("pricing_hash") and s["pricing_hash"] != base["pricing_hash"]}
    other_kind = {s["experiment"] for s in summaries
                  if (base := by_name.get(s.get("compare_to"))) and base is not s and _other_kind(s, base)}
    md_path = md_path or results_dir / "SUMMARY.md"

    def delta(s, path):
        base = by_name.get(s.get("compare_to"))
        if s["experiment"] in unlike:  # scored under other labkit.yaml settings: a difference would be a made-up number
            return None
        if s["experiment"] in repriced and "usd" in path.rsplit(".", 1)[-1]:  # a price change is not a cheaper system
            return None
        if s["experiment"] in other_kind and not path.startswith("metrics."):  # no LLM calls is not a cheaper answer
            return None
        a, b = _get(base, path) if base and base is not s else None, _get(s, path)
        if a is None or b is None:
            return None
        return b - a

    # Markdown (rendered on GitHub)
    head = "| Experiment | Card | Changed | Index | n | " + " | ".join(c[0] for c in columns) + " | Cost USD | Compared to |"
    lines = ["# Experiment results", "", f"Generated {datetime.now().isoformat(timespec='seconds')} by `labkit report`. "
             "Numbers in parentheses are the difference from the comparison baseline (compare_to).", "",
             head, "|" + "---|" * (len(columns) + 7)]
    for s in summaries:
        cells = []
        for header, path, fmt, _ in columns:
            d = delta(s, path)
            cell = _fmt(_get(s, path), fmt)
            cells.append(cell + (f" ({'+' if d >= 0 else ''}{fmt.format(d)})" if d not in (None, 0) else ""))
        lines.append(f"| {_md(s['experiment'])} | {_md(s.get('card') or '–')} | {_md(_changed_text(s))} | "
                     f"{_md(s.get('index_key') or '–')} | {_md(_n_text(s, edited[s['experiment']]))} | "
                     + " | ".join(cells)
                     + f" | {_fmt(_get(s, 'cost.total_usd'), '{:.4f}')} | {_md(s.get('compare_to') or '–')}"
                     + (f" ⚠ {_md('; '.join(unlike[s['experiment']]))}: no difference shown"
                        if s["experiment"] in unlike else "")
                     + (" ⚠ priced from another pricing.yaml: no USD difference shown"
                        if s["experiment"] in repriced else "")
                     + (" ⚠ retrieval-only against an answer run (or the reverse): only the metrics columns show a difference"
                        if s["experiment"] in other_kind else "") + " |")
    Path(md_path).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    # HTML (local, sortable)
    rows = []
    for s in summaries:
        tds = []
        for header, path, fmt, better in columns:
            value, d = _get(s, path), delta(s, path)
            cls = "" if d in (None, 0) else ("up" if (d > 0) == better else "down")
            sub = f"<small>{'+' if d > 0 else ''}{fmt.format(d)}</small>" if d not in (None, 0) else ""
            tds.append(f'<td class="num {cls}" data-v="{"" if value is None else value}">{_fmt(value, fmt)}{sub}</td>')
        note = (f"<small>⚠ {html.escape('; '.join(unlike[s['experiment']]))} "
                f"(vs {html.escape(str(s.get('compare_to')))}): no difference shown</small>"
                if s["experiment"] in unlike else "")
        if s["experiment"] in repriced:
            note += (f"<small>⚠ priced from another pricing.yaml than {html.escape(str(s.get('compare_to')))}: "
                     "no USD difference shown</small>")
        if s["experiment"] in other_kind:
            note += (f"<small>⚠ retrieval-only against an answer run (or the reverse) with {html.escape(str(s.get('compare_to')))}: "
                     "only the metrics columns show a difference</small>")
        rows.append(f"<tr><td>{html.escape(s['experiment'])}{note}</td><td>{html.escape(str(s.get('card') or '–'))}</td>"
                    f"<td>{html.escape(_changed_text(s))}</td><td>{html.escape(str(s.get('index_key') or '–'))}</td>"
                    f"<td class='num' data-v='{s['n_questions']}'>{html.escape(_n_text(s, edited[s['experiment']]))}</td>{''.join(tds)}</tr>")
    headers = "".join(f"<th>{html.escape(h)}</th>"
                      for h in ["Experiment", "Card", "Changed", "Index", "n"] + [c[0] for c in columns])
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(HTML_TEMPLATE.format(headers=headers, rows="\n".join(rows),
                                              created=datetime.now().isoformat(timespec="seconds")),
                         encoding="utf-8", newline="\n")
    return {"experiments": len(summaries), "markdown": str(md_path), "html": str(html_path)}


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>RAG experiment comparison</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #222; }}
table {{ border-collapse: collapse; font-size: 13px; }}
th, td {{ border-bottom: 1px solid #ddd; padding: 6px 10px; text-align: left; vertical-align: top; }}
th {{ cursor: pointer; background: #f6f6f6; position: sticky; top: 0; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td small {{ display: block; color: #666; }}
td.up small {{ color: #1a7f37; }} td.down small {{ color: #c62828; }}
</style></head><body>
<h1>RAG experiment comparison</h1>
<p>Generated {created}. The small number is the difference from the comparison baseline (green = better, red = worse).
Click a header to sort. Differences smaller than the measured noise are not results.</p>
<table id="t"><thead><tr>{headers}</tr></thead><tbody>
{rows}
</tbody></table>
<script>
document.querySelectorAll("#t th").forEach((th, i) => th.onclick = () => {{
  const body = document.querySelector("#t tbody"), rows = [...body.rows];
  const dir = th.dataset.dir = th.dataset.dir === "asc" ? "desc" : "asc";
  const key = r => {{ const c = r.cells[i]; return c.dataset.v !== undefined && c.dataset.v !== "" ? parseFloat(c.dataset.v) : c.textContent; }};
  rows.sort((a, b) => {{ const x = key(a), y = key(b); const r = x > y ? 1 : x < y ? -1 : 0; return dir === "asc" ? r : -r; }});
  rows.forEach(r => body.appendChild(r));
}});
</script></body></html>
"""
