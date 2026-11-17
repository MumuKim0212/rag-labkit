"""Project settings that code needs, read from labkit.yaml in the project root. Everything is optional.

    golden:
      files: [golden.jsonl]        # files in golden/ to load, exactly these (default: golden.jsonl)
      pages: corpus/pages.jsonl    # extracted page text for `labkit golden-check` (default: none; --pages overrides)
    max_score: 2                  # score of a fully correct answer (grades are 0..max_score); a positive number
    answerable_default: answer_with_citation   # behavior assumed for answerable items without `expected_behavior`
    behaviors:                     # what the right response looks like, per expected_behavior value
      answer_with_citation: {group: answer, statuses: [answered]}
      abstain_and_redirect: {group: abstain, statuses: [abstained]}
      require_freshness_check: {group: abstain, statuses: [abstained, answered]}   # either is acceptable
    pythonpath: [src]              # folders added to sys.path before importing `modules` (default: [.])
    modules: [myproj.systems]      # imported so their @register_system / @register_judge / @register_measure run
    measures: [retrieval, citation, status]    # per-question measures to compute (default: these three)
    index_keys: [corpus, chunking, embedding]  # config keys (dotted prefixes) that change the index (default: none)
    index_dir: out/index/{index_key}/{scope}   # where your index builder wrote build.json {"calls": [...]}, so
                                   # `labkit evaluate` can report cost.index_build_usd (default: none = null)
    main_branch: main              # tick / approve / revoke are refused on any other git branch (default: main)
    report:
      columns:                     # report columns (default: labkit.harness.COLUMNS)
        - scores.correctness_mean                                   # shorthand: a path into summary.json
        - {header: Faithfulness, path: metrics.faithfulness, format: "{:.2f}", higher_is_better: true}

Without `behaviors`, the status check is skipped (status_ok is None) and the report has no behavior groups.
Shapes are checked when the file is read (`statuses` is a list, `report.columns` entries are paths or mappings with
a `path`), and a key labkit does not read that looks like one it does (`maxscore`) is warned about once.
The human-readable project facts (corpus, models, budget, deadline) live in docs/WORKFLOW.md, not here.
"""

import difflib
import math
import warnings
from pathlib import Path

import yaml

from .paths import MARKER, project_root

DEFAULTS = {
    "golden": {"files": ["golden.jsonl"], "pages": None},
    "max_score": 2,
    "answerable_default": None,
    "behaviors": {},
    "pythonpath": ["."],
    "modules": [],
    "measures": ["retrieval", "citation", "status"],
    "index_keys": [],
    "index_dir": None,
    "main_branch": "main",
    "report": {"columns": None},
}


def run_settings(project: dict) -> dict:
    """The labkit.yaml settings fixed once a run starts: which golden files supply the questions and the scale the
    judge grades on. They are recorded with every run, and an experiment is never resumed, graded or evaluated
    under different ones (labkit.freeze, labkit.harness)."""
    return {"golden_files": list(project["golden"]["files"]), "max_score": project["max_score"]}


def scoring_settings(project: dict) -> dict:
    """run_settings plus what only reads the stored runs: the behavior table behind status_ok. Written to each
    summary.json so that `labkit compare` and `labkit report` can say when two results were scored differently.
    Fixing the behavior table and re-evaluating (no LLM calls) is allowed, so it is recorded, not frozen."""
    return {**run_settings(project), "answerable_default": project["answerable_default"],
            "behaviors": project["behaviors"] or {}}


def settings_diff(before: dict | None, now: dict | None) -> list[str]:
    """The settings that differ between two records, sorted; empty when either record is missing (an older log)."""
    if before is None or now is None:
        return []
    return [k for k in sorted(before.keys() | now.keys()) if before.get(k) != now.get(k)]


def describe_diff(before: dict, now: dict) -> str:
    return "; ".join(f"{k}: {before.get(k)!r} -> {now.get(k)!r}"[:120] for k in settings_diff(before, now))


def _check_list_of_str(path: Path, key: str, value) -> None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{path}: `{key}` must be a list of strings")


def load_project(root: Path | None = None) -> dict:
    path = Path(root or project_root()) / MARKER
    raw = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: labkit.yaml is a mapping of settings, not {type(raw).__name__}")
    for key in ("golden", "report", "behaviors"):
        if raw.get(key) is not None and not isinstance(raw[key], dict):
            raise ValueError(f"{path}: `{key}` must be a mapping, got {raw[key]!r}")
    project = {**DEFAULTS, **raw}
    for key in ("golden", "report"):
        project[key] = {**DEFAULTS[key], **(raw.get(key) or {})}
    for key in ("pythonpath", "modules", "measures", "index_keys"):
        _check_list_of_str(path, key, project[key])
    score = project["max_score"]
    try:
        finite = isinstance(score, (int, float)) and math.isfinite(score)
    except OverflowError:  # an integer too large for a float
        finite = False
    if isinstance(score, bool) or not finite or score <= 0:
        # `"2"` or null crashed grading after the judge call was paid for, and `.nan` made every later command refuse
        # forever because nan != nan; 0, -1 and true made every judge answer invalid
        raise ValueError(f"{path}: `max_score` must be a positive number (the score of a fully correct answer), "
                         f"got {score!r}")
    if not isinstance(project["main_branch"], str) or not project["main_branch"]:
        raise ValueError(f"{path}: `main_branch` must be a branch name")
    pattern = project["index_dir"]
    if pattern is not None:
        try:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError
            pattern.format(index_key="k", scope="s")
        except (ValueError, KeyError, IndexError, AttributeError, TypeError):  # `{index_key.x}`, `{index_key[0]}`
            raise ValueError(f"{path}: `index_dir` must be a folder path that uses only the placeholders "
                             "{index_key} and {scope}, e.g. out/index/{index_key}/{scope}") from None
    _check_list_of_str(path, "golden.files", project["golden"]["files"])
    pages = project["golden"]["pages"]
    if pages is not None and (not isinstance(pages, str) or not pages):
        raise ValueError(f"{path}: `golden.pages` must be a file path")
    for name, entry in (project.get("behaviors") or {}).items():
        if not isinstance(entry, dict) or "group" not in entry or not entry.get("statuses"):
            raise ValueError(f"{path}: behaviors.{name} needs `group` and a non-empty `statuses` list")
        if not isinstance(entry["group"], str) or not entry["group"].strip():
            raise ValueError(f"{path}: behaviors.{name}.group must be a group name (text), got {entry['group']!r}")
        statuses = entry["statuses"]
        if not isinstance(statuses, list) or not all(isinstance(s, str) and s.strip() for s in statuses):
            # `statuses: answered` was read letter by letter, so status_ok came out 0.5 without a word
            raise ValueError(f"{path}: behaviors.{name}.statuses must be a list of statuses, got {statuses!r}; "
                             f"write a list: statuses: [answered]")
    _check_report_columns(path, project["report"]["columns"])
    _warn_unknown_keys(path, raw)
    return project


def _check_report_columns(path: Path, columns) -> None:
    """`report.columns` as labkit.harness.report_columns reads it; `columns: [1]` was a TypeError in `labkit report`."""
    if columns is None:
        return
    if not isinstance(columns, list):
        raise ValueError(f"{path}: `report.columns` must be a list of columns, got {columns!r}")
    for entry in columns:
        if isinstance(entry, str) and entry.strip():
            continue
        if isinstance(entry, dict) and not (isinstance(entry.get("path"), str) and entry["path"].strip()):
            raise ValueError(f"{path}: a `report.columns` entry needs a `path` into summary.json (text): {entry!r}")
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"].strip() \
                and all(entry.get(k) is None or isinstance(entry[k], str) for k in ("header", "format")) \
                and (entry.get("higher_is_better") is None or isinstance(entry["higher_is_better"], bool)):
            continue
        raise ValueError(f"{path}: each entry of `report.columns` is a path into summary.json (text) or a mapping with "
                         "a text `path` and optional text `header` and `format` and true/false `higher_is_better`; "
                         f"got {entry!r}")


_WARNED: set = set()


def _warn_unknown_keys(path: Path, raw: dict) -> None:
    """A key labkit does not read that looks like one it does (`maxscore`) is a typo that would silently change
    nothing; say so once per process. A key with no close match stays quiet: a plugin may keep its own settings."""
    for prefix, keys, known in (("", raw, DEFAULTS), ("golden.", raw.get("golden") or {}, DEFAULTS["golden"])):
        for key in keys:
            if not isinstance(key, str) or key in known:
                continue
            close = difflib.get_close_matches(key, list(known), n=1, cutoff=0.8)
            if close and (str(path), prefix + key) not in _WARNED:
                _WARNED.add((str(path), prefix + key))
                warnings.warn(LabkitWarning(f"{path}: unknown key `{prefix}{key}` (did you mean `{prefix}{close[0]}`?); "
                                            "labkit ignores it"), stacklevel=3)


class LabkitWarning(UserWarning):
    """A warning labkit's CLI prints as `labkit: warning: …`."""
