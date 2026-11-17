"""Frozen files: what a result was produced from must not change under it.

A prompt, golden file, or config that an experiment used must stay as it was, or two results that look
comparable are not. labkit records a short hash of each input file with every run and checks two things when a
run starts:

- Within one experiment, always: resuming under the same name needs the same config (`config_hash`) and the
  same input files. Resuming after an edit would mix two setups into one result.
- Across experiments, once evaluated: a file used by an experiment that has a results/<experiment>/summary.json
  is frozen. A smoke test or a run nobody evaluated freezes nothing, so you can still iterate before a result
  counts. summary.json is committed, so a teammate's evaluated experiment freezes the file for you too.

A smoke test (`--limit` up to cards.SMOKE_LIMIT) is no part of its experiment: its rows are left out of the
within-experiment check above, so a prompt fixed after reading the smoke output does not block the real run.

Input files are every file that exists inside the project and is named by a string anywhere in the config (a plain
value, or inside a list or a nested mapping: prompts, the corpus extraction, ...), the golden files listed in
labkit.yaml, the config file with its `base:` chain, and configs/baseline.yaml. A file that is deleted after the run
counts as changed. Not hashed: a folder (name its files instead), a path outside the project, and a path that does
not exist when the run starts (it may be an output the run creates). Line endings are ignored, so CRLF and LF
checkouts hash the same.

labkit.yaml itself is not an input file (a new report column must not block a run). The two settings in it that
fix what a run means are recorded with every run instead (project.run_settings: the golden file list and
max_score), and an experiment is never resumed, graded or evaluated after either changed.

A file frozen by an evaluated experiment stays frozen under any other name that reaches it (a link, another case on
a case-insensitive file system): the freeze compares the file itself, not the path as written.

Limits of that: the file a name reaches is looked up when the check runs, not remembered from the run. Pointing a
folder link at another version after an evaluated run makes a name that reaches the new target look changed (a false
alarm) and the file the run really read is no longer protected under its own name; a hard link is a second file as
far as labkit can tell. A file reached through a link that leads outside the project is outside it, and is not hashed
(the test is where the path leads, not how it is written). Retargeting links is not supported: make a new file.

To use a changed file, copy it to a new version and point the config at the copy. If an evaluated experiment
was invalid, remove its results/<experiment>/ folder (a visible, reviewable change): it then freezes nothing.
"""

import hashlib
import json
import os
from pathlib import Path

from .config import config_chain, settings
from .eval import load_summary
from .paths import baseline_path, golden_dir, results_dir
from .project import describe_diff, settings_diff


HASH_BLOCK = 1 << 20  # bytes read at a time: a corpus extraction named in a config can be gigabytes


def file_hash(path: Path) -> str:
    """A short hash of the file's content with CRLF read as LF, without holding the file in memory (it used to read it
    whole and copy it again to change the line endings: twice the file's size, and again for every experiment that
    names it when a report is built). The value is the same as for the whole content, so recorded hashes still match."""
    digest, carry = hashlib.sha1(), b""
    with Path(path).open("rb") as f:
        while block := f.read(HASH_BLOCK):
            block = carry + block
            carry = block[-1:] if block.endswith(b"\r") else b""  # a CR at the end may be the first half of a CRLF
            digest.update((block[:-1] if carry else block).replace(b"\r\n", b"\n"))
    digest.update(carry)
    return digest.hexdigest()[:10]


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _identity(root: Path, name: str) -> str:
    """Which file a recorded name is NOW: its real path, case-folded where the file system is. Two spellings of one
    file (a link, another case) compare equal; keyed by the written name alone, the second spelling escaped the freeze."""
    try:
        return os.path.normcase(str((Path(root) / name).resolve()))
    except (OSError, ValueError):
        return os.path.normcase(name)


def _strings(value):
    """Every string in a config value, going down into lists and mappings (a prompt may sit in `[{prompt: x}]`)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _written_name(root: Path, value: str, resolved: Path) -> str:
    """The name a config's path is recorded under: the path as written (normalized), not where it resolves to. A
    `prompts/cur` that is a link to `prompts/v1` must stay `prompts/cur/answer.md`: keyed by the target, pointing the
    link at v2 only renamed the key, and nothing looked changed."""
    written = Path(os.path.normpath(value))
    if written.is_absolute():
        try:
            return written.relative_to(root).as_posix()
        except ValueError:
            return resolved.relative_to(root).as_posix()
    name = written.as_posix()
    return resolved.relative_to(root).as_posix() if name.startswith("../") else name


def recorded_name(root: Path, value: str) -> str | None:
    """The key a path written in a config is recorded under in `file_hashes` (None: it leads outside the project, so
    it was never hashed). `/abs/proj/prompts/x.md` and `../proj/prompts/x.md` are recorded as `prompts/x.md`."""
    root = Path(root).resolve()
    try:
        return _written_name(root, value, (root / value).resolve())
    except (OSError, ValueError):
        return None


def referenced_files(cfg: dict, project: dict, root: Path) -> list[str]:
    """Project files this run reads, relative to the root with forward slashes, under the path as the config writes it
    (the file read through that path is what is hashed, so retargeting a link is a change)."""
    root = Path(root).resolve()
    names: set[str] = set()
    for value in _strings(settings(cfg)):
        if 0 < len(value) < 260 and "\n" not in value:
            try:
                candidate = (root / value).resolve()
                if candidate.is_file() and _inside(root, candidate):
                    names.add(_written_name(root, value, candidate))
            except (OSError, ValueError):
                continue
    resolved: list[Path] = []
    if cfg.get("config_file"):
        config_path = root / cfg["config_file"]
        if config_path.exists():
            resolved.extend(config_chain(config_path))
    if baseline_path(root).exists():
        resolved.append(baseline_path(root).resolve())
    for name in project["golden"]["files"]:
        path = golden_dir(root) / name
        if path.exists():
            resolved.append(path.resolve())
    names.update(p.relative_to(root).as_posix() for p in resolved if _inside(root, p))
    return sorted(names)


def file_hashes(cfg: dict, project: dict, root: Path) -> dict[str, str]:
    root = Path(root).resolve()
    return {name: file_hash(root / name) for name in referenced_files(cfg, project, root)}


def changed_since(recorded: dict | None, root: Path) -> list[str]:
    """Files whose content now differs from the hash recorded at run time (or that no longer exist)."""
    root = Path(root)
    return [name for name, digest in (recorded or {}).items()
            if not (root / name).is_file() or file_hash(root / name) != digest]


def frozen_by(root: Path) -> dict[str, list[tuple[str, str]]]:
    """{file: [(experiment, hash), ...]} from every evaluated experiment in results/."""
    frozen: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(results_dir(root).glob("*/summary.json")):
        summary = load_summary(path)
        for name, digest in (summary.get("file_hashes") or {}).items():
            frozen.setdefault(name, []).append((path.parent.name, digest))
    return frozen


def check_run(cfg: dict, hashes: dict[str, str], experiment: str, earlier_rows: list[dict], root: Path,
              project_settings: dict | None = None) -> None:
    """Raise ValueError listing everything that makes this run unsafe; return when it is fine to start."""
    problems = []
    for row in earlier_rows:
        before = (row.get("config") or {}).get("project_settings")
        if settings_diff(before, project_settings):
            problems.append(f"labkit.yaml changed since {experiment} started ({describe_diff(before, project_settings)}): "
                            "resuming would mix two setups in one result. Restore labkit.yaml, or run under a new "
                            "experiment name (--name).")
            break
    for row in earlier_rows:
        before = row.get("config") or {}
        if before.get("config_hash") and before["config_hash"] != cfg.get("config_hash"):
            problems.append(f"{experiment} was started with config {before['config_hash']} and the config is now "
                            f"{cfg.get('config_hash')}: resuming would mix two setups in one result. Run the changed "
                            "config under a new experiment name (--name), or restore the old config.")
            break
    edited = sorted({name for row in earlier_rows for name, digest in ((row.get("config") or {}).get("file_hashes") or {}).items()
                     if name in hashes and hashes[name] != digest})
    if edited:
        problems.append(f"{', '.join(edited)} changed since {experiment} started: resuming would mix two setups in "
                        "one result. Restore the file, or start a new experiment with the edited copy.")
    deleted = sorted({name for row in earlier_rows for name in ((row.get("config") or {}).get("file_hashes") or {})
                      if name not in hashes and not (Path(root) / name).is_file()})  # `hashes` lists only what exists
    if deleted:
        problems.append(f"{', '.join(deleted)} deleted since {experiment} started: resuming would mix two setups in "
                        "one result. Restore the file, or start a new experiment.")
    frozen: dict[str, list[tuple[str, str]]] = {}
    for recorded, entries in frozen_by(root).items():  # by the file itself: `alias/x.md` and `prompts/x.md` are one file
        frozen.setdefault(_identity(root, recorded), []).extend(entries)
    for name, digest in hashes.items():
        for used_by, was in frozen.get(_identity(root, name), []):
            if was != digest:
                problems.append(f"{name} was used by the evaluated experiment {used_by} and has changed since. A file "
                                f"used in a decision is never edited: copy it to a new version and point the config "
                                f"at the copy. (If {used_by} is invalid, remove results/{used_by}/ and it stops "
                                "freezing the file.)")
                break
    if problems:
        raise ValueError("frozen files:\n  " + "\n  ".join(problems))
