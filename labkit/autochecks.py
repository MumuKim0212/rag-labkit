"""Automatic exit-criteria checks. A criterion in docs/steps/NN-*.md that code can verify carries a tag:

    - [ ] Baseline dev run and grades saved <!-- auto: baseline_evaluated -->

`labkit check` runs the check by name; criteria without a tag are claimed with `labkit tick … --evidence`.
A check is `fn(root, project) -> (ok, detail)`. It proves only what files and git can show (a run exists, a
config is committed), never that a judgment was good; the approver still reads the evidence.
"""

import json
from collections import Counter
from pathlib import Path

import yaml

from .config import load_config, same_model
from .eval import load_summary
from .eval.runlog import RunLog
from .freeze import changed_since
from .golden import load_golden
from .harness import experiment_split
from .paths import config_dir, grades_dir, results_dir, runs_db
from .project import describe_diff, load_project, scoring_settings, settings_diff
from .registry import Registry
from .repro import _git

AUTOCHECKS: Registry = Registry("auto check")
register_check = AUTOCHECKS.register

BASELINE = "dev_000_baseline"
PLACEHOLDER = "example-"  # the model names in the template's configs/baseline.yaml: two different words, not a choice


@register_check("split_and_test_untouched")
def split_and_test_untouched(root: Path, project: dict) -> tuple[bool, str]:
    try:
        items = load_golden(root=root)
    except (FileNotFoundError, ValueError) as e:
        return False, str(e)
    splits = Counter(i.get("split") for i in items)
    if not splits["dev"] or not splits["test"]:
        return False, f"need golden items in both dev and test; found {dict(splits)}"
    db = runs_db(root)
    ran = []
    if db.exists():  # by the split each run recorded, so a test run named `final_try` is found too
        log = RunLog(db)
        ran = [name for name, _ in log.experiments() if experiment_split(name, log.load(name)) == "test"]
    # a folder named test_x whose summary says dev is a dev result; one with no summary cannot say, so it counts
    ran += [p.name for p in results_dir(root).glob("test*") if p.is_dir() and not (p / "summary.json").exists()]
    for p in sorted(results_dir(root).glob("*/summary.json")):
        try:
            if load_summary(p).get("split") == "test":
                ran.append(p.parent.name)
        except ValueError as e:  # a summary that cannot be read may be a test result: the check cannot pass on it
            return False, str(e)
    if ran:
        return False, f"the test split was already run: {sorted(set(ran))}"
    return True, f"{splits['dev']} dev / {splits['test']} test items, no test run so far"


@register_check("judge_differs_from_answer")
def judge_differs_from_answer(root: Path, project: dict) -> tuple[bool, str]:
    files = [p for p in sorted(config_dir(root).glob("*.yaml")) if p.name != "pricing.yaml"]
    if not (config_dir(root) / "baseline.yaml").exists():
        return False, "configs/baseline.yaml does not exist"
    problems, checked = [], 0
    for path in files:
        try:
            cfg = load_config(path, root=root)
        except (ValueError, yaml.YAMLError) as e:  # a config that cannot load is a failed check, not a crash
            problems.append(f"{path.name}: {' '.join(str(e).split())[:200]}")
            checked += 1
            continue
        answer, judge = (cfg.get("answer") or {}).get("model"), (cfg.get("judge") or {}).get("model")
        if not answer or not judge:
            problems.append(f"{path.name}: set answer.model and judge.model")
        elif str(answer).startswith(PLACEHOLDER) or str(judge).startswith(PLACEHOLDER):
            problems.append(f"{path.name}: answer.model or judge.model is still a template placeholder ({PLACEHOLDER}…)")
        elif same_model(answer, judge):
            problems.append(f"{path.name}: judge and answer both use {judge}")
        checked += 1
    return (not problems), ("; ".join(problems) if problems else f"judge differs from answer in {checked} config(s)")


def _baseline_summary(root: Path):
    path = results_dir(root) / BASELINE / "summary.json"
    return load_summary(path) if path.exists() else None  # raises ValueError for an unreadable one: callers fail the check


@register_check("baseline_evaluated")
def baseline_evaluated(root: Path, project: dict) -> tuple[bool, str]:
    try:
        summary = _baseline_summary(root)
    except ValueError as e:
        return False, str(e)
    if summary is None:
        return False, f"results/{BASELINE}/summary.json is missing: run, grade, and evaluate the baseline"
    if not summary.get("n_graded"):
        return False, f"{BASELINE} has no graded answers: run `labkit grade --experiment {BASELINE}` and evaluate again"
    if not summary.get("complete"):  # three of ten questions scoring 1.0 is not a baseline
        why = "; ".join(summary.get("incomplete") or ["the summary has no completeness record"])
        return False, (f"{BASELINE} is incomplete ({why}): finish the run and the grading, then "
                       f"`labkit evaluate --experiment {BASELINE} --overwrite`")
    if not {"changed", "config_file", "sets"} <= summary.keys():
        return False, (f"{BASELINE} has no record of which config and which questions it ran on (a summary from an "
                       f"older labkit): `labkit evaluate --experiment {BASELINE} --overwrite`")
    not_baseline = (
        f"{BASELINE} is on the {summary.get('split')} split, and the baseline is run on dev" if summary.get("split") != "dev"
        else f"{BASELINE} is a retrieval-only run, and the baseline is an answer run" if summary.get("retrieval_only")
        else f"{BASELINE} was run from {summary['config_file']}, not configs/baseline.yaml"
        if summary["config_file"] != "configs/baseline.yaml"
        else f"{BASELINE} changes {', '.join(sorted(summary['changed']))}: that is a variant, not the baseline config"
        if summary["changed"]
        else f"{BASELINE} covers only the sets {', '.join(summary['sets'])}, not the whole dev split"
        if summary["sets"] else None)
    if not_baseline:  # the check proves the BASELINE was evaluated, whatever name the experiment carries
        return False, not_baseline + ": run the baseline config on all of dev under this name"
    now = scoring_settings(project or load_project(root))
    if settings_diff(summary.get("project_settings"), now):  # e.g. max_score raised after the baseline was scored
        return False, (f"labkit.yaml changed since {BASELINE} was evaluated ({describe_diff(summary['project_settings'], now)}): "
                       f"restore it, or `labkit evaluate --experiment {BASELINE} --overwrite` (rerun it if the scale or "
                       "the golden list changed)")
    edited = changed_since(summary.get("file_hashes"), root)  # now, not as of the evaluation: files can change after it
    if edited:
        return False, (f"{', '.join(edited)} changed since {BASELINE} ran, so the repository no longer holds what "
                       "produced the baseline. Restore the file(s), or rerun the baseline and evaluate it again.")
    return True, f"{BASELINE}: {summary['n_questions']} of {summary['n_expected']} questions, {summary['n_graded']} graded"


@register_check("baseline_metrics_saved")
def baseline_metrics_saved(root: Path, project: dict) -> tuple[bool, str]:
    path = results_dir(root) / BASELINE / "metrics.jsonl"
    rows = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) if path.exists() else 0
    return rows > 0, (f"{rows} per-question rows" if rows else f"results/{BASELINE}/metrics.jsonl is missing or empty")


@register_check("baseline_config_committed")
def baseline_config_committed(root: Path, project: dict) -> tuple[bool, str]:
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return False, "not a git repository: commit configs/baseline.yaml so it can be treated as frozen"
    if _git(root, "ls-files", "--error-unmatch", "configs/baseline.yaml") is None:
        return False, "configs/baseline.yaml is not tracked by git"
    if _git(root, "status", "--porcelain", "--", "configs/baseline.yaml"):
        return False, "configs/baseline.yaml has uncommitted changes"
    return True, f"committed at {_git(root, 'log', '-1', '--format=%h', '--', 'configs/baseline.yaml')}"


@register_check("judge_prompt_committed")
def judge_prompt_committed(root: Path, project: dict) -> tuple[bool, str]:
    """Every judge prompt a config names exists, is tracked by git and has no uncommitted edits: the step-1 criterion
    "Judge prompt fixed" rested on a sentence, and a missing file turned the prompt's freeze off without a word."""
    prompts, problems = {}, []
    for path in [p for p in sorted(config_dir(root).glob("*.yaml")) if p.name != "pricing.yaml"]:
        try:
            cfg = load_config(path, root=root)
        except (ValueError, yaml.YAMLError) as e:  # a config that cannot load is a failed check, not a crash
            problems.append(f"{path.name}: {' '.join(str(e).split())[:200]}")
            continue
        prompt = (cfg.get("judge") or {}).get("prompt")
        if isinstance(prompt, str) and prompt:
            prompts.setdefault(prompt, path.name)
    if not prompts and not problems:
        return True, "no config sets judge.prompt (the judge uses no prompt file)"
    if prompts and _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return False, "not a git repository: commit the judge prompt so it can be treated as fixed"
    for prompt, config in prompts.items():
        if not (Path(root) / prompt).is_file():
            problems.append(f"{prompt} (judge.prompt of {config}) does not exist")
        elif _git(root, "ls-files", "--error-unmatch", "--", prompt) is None:
            problems.append(f"{prompt} is not tracked by git")
        elif _git(root, "status", "--porcelain", "--", prompt):
            problems.append(f"{prompt} has uncommitted changes")
    if problems:
        return False, "; ".join(problems)
    return True, "committed: " + ", ".join(f"{p} at {_git(root, 'log', '-1', '--format=%h', '--', p)}" for p in prompts)


def _repeats(summary: dict, baseline: dict) -> bool:
    """A rerun measures the baseline's noise only if it is the baseline run again: same split, same config, every
    question, nothing failed. A subset, another config or a rerun with a failure measures something else."""
    same = ("split", "config_hash", "config_file", "sets", "file_hashes", "n_questions")  # what makes it the same run
    return (all(summary.get(k) == baseline.get(k) for k in same)
            and summary.get("complete") is True and not summary.get("retrieval_only"))


def _rows(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


@register_check("noise_runs_exist")
def noise_runs_exist(root: Path, project: dict) -> tuple[bool, str]:
    try:
        baseline = _baseline_summary(root)
    except ValueError as e:
        return False, str(e)
    if baseline is None:
        return False, f"{BASELINE} is not evaluated yet (results/{BASELINE}/summary.json is missing): the noise runs repeat it"
    reruns = []
    for folder in sorted(results_dir(root).glob(f"{BASELINE}_r*")):
        path = folder / "summary.json"
        if folder.name.endswith("_retrieval") or not path.exists():
            continue
        try:
            if _repeats(json.loads(path.read_text(encoding="utf-8")), baseline):
                reruns.append(folder.name)
        except json.JSONDecodeError:
            continue
    need = max(int(baseline.get("n_graded") or 0), 1)  # a regrade that skipped answers leaves part of the judge unmeasured
    regrade_files = [*grades_dir(root).glob(f"{BASELINE}__regrade*.jsonl"),
                     *(results_dir(root) / BASELINE).glob("grades__regrade*.jsonl")]
    regrades = [p.name for p in regrade_files if _rows(p) >= need]
    missing = [label for label, found in ((f"(a) a complete rerun of the baseline config named {BASELINE}_r<N>, evaluated", reruns),
                                          (f"(b) a regrade of all {need} graded answers (--suffix __regrade1)", regrades))
               if not found]
    return (not missing), ("missing " + " and ".join(missing) if missing else f"rerun {reruns[0]}, regrade {regrades[0]}")
