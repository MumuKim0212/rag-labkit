"""Experiment configs: configs/baseline.yaml plus variants that override only what they change.

    cfg = load_config("configs/004-hybrid.yaml")
    cfg["changed"]            # vs configs/baseline.yaml: {"retrieval.method": {"from": "vector", "to": "hybrid"}}
    cfg["changed_from_base"]  # vs the file named by `base:`: what this config itself changes
    cfg["config_hash"]        # hash of the merged settings (meta and execution keys excluded)

A variant names its parent with `base:` (relative to the variant's folder); chains are merged top-down.
The baseline is configs/baseline.yaml in the project root (labkit.paths) unless `baseline` is given.
Settings are inherited; what identifies one file (`name`, `card`, `description`, `compare_to`) is not: a
variant without its own `name:` would otherwise reuse its parent's experiment name, and one without `card:` the
parent's card. `run.concurrency` (worker threads) says how many workers, not what is computed, so it is not hashed and
not in `changed`; the other keys under `run:` (timeout_s, max_retries, ...) are settings like any other. (The config
file is still an input file: once it is frozen, e.g. the baseline after its evaluation, set the worker count in a
child config rather than editing the file.)
See labkit/template/configs/README.md.
"""

import copy
import hashlib
import json
import math
from pathlib import Path

import yaml

from .paths import baseline_path, project_root

PER_FILE_KEYS = ("name", "card", "description", "compare_to")  # identify one config file: never inherited
EXECUTION_KEYS = ("concurrency",)  # under `run:`: how many workers, not what is computed. Other keys under run:
#                                    (timeout_s, max_retries, ...) change which questions fail or what a flaky system
#                                    returns, so they stay in the hash and in `changed`.
META_KEYS = ("base", *PER_FILE_KEYS, "changed", "changed_from_base", "config_hash", "config_file")
# Keys labkit computes or writes into the config it stores with each run. They share one namespace with the user's
# keys, so a config that used one would be overwritten or read as labkit's own: `smoke: true` in a baseline once
# turned every full run into smoke rows that were never resumed or evaluated. Whatever labkit.harness adds to the
# stored run config must be listed here (a test checks it), and a user config may not use any of them.
RESERVED_KEYS = frozenset({"changed", "changed_from_base", "config_hash", "config_file",
                           "environment", "retrieval_only", "file_hashes", "selection", "card_check", "smoke",
                           "index_key", "project_settings"})


def _read(path: Path) -> dict:
    if Path(path).is_dir():  # opening a folder is a PermissionError on Windows and an IsADirectoryError elsewhere
        raise ValueError(f"{path} is a folder, not a config file: pass the .yaml file")
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _unstorable(value, where: str = ""):
    """(where, what) of the first value in a config that the run log cannot store as JSON, or None. YAML turns an
    unquoted `2026-01-01` into a date and `.inf` into infinity; a date made the run die inside RunLog.save after the
    first question was answered and paid for, and infinity made the experiment impossible to evaluate."""
    if value is None or isinstance(value, (str, bool, int)):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else (where, f"{value}, which is not a finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{where}.{key}" if where else str(key)
            if not isinstance(key, str):  # `yes:` and `on:` are True, `1:` an integer, `2026-01-01:` a date: a date key
                # crashed the config hash with a bare TypeError, mixed number and text keys did too, and the run log
                # stores every key as text, so 1 and "1" would be one
                return here, f"a key ({key!r}, a {type(key).__name__}) that is not text"
            found = _unstorable(item, here)
            if found:
                return found
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            found = _unstorable(item, f"{where}[{index}]")
            if found:
                return found
        return None
    return where, f"{value!r}, a {type(value).__name__}"


def _read_checked(path: Path) -> dict:
    """A config file as written, after refusing the keys labkit keeps for itself and values it cannot store."""
    raw = _read(path)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: a config is a mapping of settings, not {type(raw).__name__}")
    for key in sorted(RESERVED_KEYS & raw.keys()):
        raise ValueError(f"{path}: `{key}` is a key labkit writes itself (reserved: {', '.join(sorted(RESERVED_KEYS))}); "
                         "rename it in the config")
    found = _unstorable(raw)
    if found:
        where, what = found
        raise ValueError(f"{path}: `{where}` is {what}. A config holds text, numbers, true/false, lists and mappings, "
                         "and every key is text: write a date, or a key like yes, on or 1, in quotes, for example "
                         "'2026-01-01'")
    _check_shapes(path, raw)
    return raw


def _check_shapes(path: Path, raw: dict) -> None:
    """The keys labkit itself reads must have the shape it reads them in. `answer: gpt-x` (text for a mapping), a
    worker count of "4" or a card that is a list were bare AttributeError and TypeError tracebacks, some of them
    after the first question had been paid for."""
    for key in ("answer", "judge", "run", "corpus"):
        if raw.get(key) is not None and not isinstance(raw[key], dict):
            raise ValueError(f"{path}: `{key}` must be a mapping (for example `{key}: {{model: ...}}`), got {raw[key]!r}")
    for key in ("card", "compare_to", "description", "base"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            raise ValueError(f"{path}: `{key}` must be text, got {raw[key]!r}")
    judge = raw.get("judge") or {}
    if judge.get("name") is not None and (not isinstance(judge["name"], str) or not judge["name"].strip()):
        raise ValueError(f"{path}: `judge.name` must be the name a judge is registered under (text), got {judge['name']!r}")
    if judge.get("prompt") is not None and (not isinstance(judge["prompt"], str) or not judge["prompt"].strip()):
        raise ValueError(f"{path}: `judge.prompt` must be a file path (text), got {judge['prompt']!r}")
    set_scope = (raw.get("corpus") or {}).get("set_scope")
    if set_scope is not None and not (isinstance(set_scope, dict) and all(isinstance(v, str) for v in set_scope.values())):
        raise ValueError(f"{path}: `corpus.set_scope` must map each golden set to a scope label "
                         f"(for example `set_scope: {{base_positive: A}}`), got {set_scope!r}")
    run = raw.get("run") or {}
    if "concurrency" in run:
        workers = run["concurrency"]
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError(f"{path}: `run.concurrency` (worker threads) must be a whole number of at least 1, "
                             f"got {workers!r}")


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _resolve(path: Path, seen: frozenset = frozenset()) -> dict:
    path = Path(path).resolve()
    if path in seen:
        raise ValueError(f"config inheritance loop at {path}")
    raw = _read_checked(path)
    parent = raw.get("base")
    if parent:
        inherited = {k: v for k, v in _resolve(path.parent / parent, seen | {path}).items() if k not in PER_FILE_KEYS}
        merged = deep_merge(inherited, raw)
    else:
        merged = raw
    merged.pop("base", None)
    return merged


def config_chain(path: Path) -> list[Path]:
    """The config file and the files it inherits from through `base:`, nearest first."""
    chain, seen = [], set()
    path = Path(path).resolve()
    while path not in seen:
        seen.add(path)
        chain.append(path)
        parent = _read(path).get("base")
        if not parent:
            break
        path = (path.parent / parent).resolve()
    return chain


def settings(cfg: dict) -> dict:
    """The config without meta keys and the worker count: what actually determines a run."""
    out = {k: v for k, v in cfg.items() if k not in META_KEYS}
    run = out.get("run")
    if isinstance(run, dict):
        rest = {k: v for k, v in run.items() if k not in EXECUTION_KEYS}
        if rest:
            out["run"] = rest
        else:
            del out["run"]
    return out


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in d.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(flatten(value, dotted + "."))
        else:
            out[dotted] = value
    return out


def changed_keys(cfg: dict, baseline: dict) -> dict:
    a, b = flatten(settings(baseline)), flatten(settings(cfg))
    return {k: {"from": a.get(k), "to": b.get(k)} for k in sorted(a.keys() | b.keys()) if a.get(k) != b.get(k)}


def config_hash(cfg: dict) -> str:
    blob = json.dumps(settings(cfg), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def same_model(a, b) -> bool:
    """Do two model names name one model? Case and surrounding blanks do not tell models apart, and nothing is not a
    model: the step-1 check and `labkit run` both ask this, and used to answer differently for `Model-A` and `model-a`."""
    return bool(a) and bool(b) and str(a).strip().casefold() == str(b).strip().casefold()


def load_config(path, baseline: Path | None = None, root: Path | None = None) -> dict:
    root = Path(root).resolve() if root else project_root()
    path = Path(path)
    cfg = _resolve(path)
    if cfg.get("name") in (None, ""):  # `name:` left empty is no name: it was the experiment dev_None
        cfg["name"] = path.stem
    cfg["changed"] = changed_keys(cfg, _resolve(baseline or baseline_path(root)))
    parent = _read(path).get("base")  # a chain (a card built on an adopted one) shows its own change here
    cfg["changed_from_base"] = changed_keys(cfg, _resolve(path.parent / parent)) if parent else cfg["changed"]
    cfg["config_hash"] = config_hash(cfg)
    try:
        cfg["config_file"] = path.resolve().relative_to(root).as_posix()
    except ValueError:
        cfg["config_file"] = str(path)
    return cfg


def experiment_name(cfg: dict, split: str, retrieval_only: bool = False) -> str:
    """<split>_<config name>; retrieval-only runs (LLM-free comparisons) get a `_retrieval` suffix."""
    return f"{split}_{cfg['name']}" + ("_retrieval" if retrieval_only else "")
