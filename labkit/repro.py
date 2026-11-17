"""What produced a result: git commit, uncommitted changes, Python and package versions.
Stored in every run's config and in summary.json, so numbers can be traced to code.

`dirty` only looks at the folders that define a run (code, configs, prompts, golden sets, and wherever the project's
own code lives: the `pythonpath` folders of labkit.yaml, or the packages named under `modules:` when pythonpath is
the project root), not at results or scratch files, so exporting results does not make the next run look uncommitted.
labkit.yaml itself is not watched: what it sets that decides a result is recorded in project_settings.
"""

import hashlib
import os
import platform
import re
import subprocess
from importlib import metadata
from pathlib import Path

import yaml

from .paths import project_root
from .project import load_project

TRACKED = ("src", "scripts", "configs", "prompts", "golden")
MAX_HASHED_BYTES = 50 * 1024 * 1024  # an untracked file above this counts by its size, not its content


def code_folders(root: Path) -> list[str]:
    """The paths whose uncommitted edits make a run `dirty`: TRACKED, plus where the project's code is. A project that
    keeps its plugin in `lib/` (`pythonpath: [lib]`) or in a package at its root (`modules: [myproj.systems]` with the
    default pythonpath `.`) would otherwise have edits to the system itself go unseen. Entries outside the project
    cannot be tracked by its repository and are dropped; an unreadable labkit.yaml falls back to TRACKED."""
    root = Path(root)
    names = list(TRACKED)
    try:
        project = load_project(root)
    except (ValueError, OSError, yaml.YAMLError):
        return names
    here = root.resolve()
    for entry in project.get("pythonpath") or []:
        if str(entry) in ("", "."):  # the project root: results/ and out/ live there, so follow the modules instead
            for module in project.get("modules") or []:
                first = module.split(".")[0]
                if (root / first).is_dir():
                    names.append(first)
                elif (root / f"{first}.py").is_file():
                    names.append(f"{first}.py")
            continue
        try:
            relative = (root / entry).resolve().relative_to(here).as_posix()
        except ValueError:
            continue  # outside the project
        if relative != ".":
            names.append(relative)
    return list(dict.fromkeys(names))


def _git(root: Path, *args: str) -> str | None:
    try:  # git writes UTF-8 whatever the console code page is: text=True alone decoded a Korean name with cp949 and crashed
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _git_bytes(root: Path, *args: str) -> bytes | None:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _changes_hash(root: Path, folders: list[str]) -> str | None:
    """A short hash of the uncommitted edits (git diff HEAD) and the content of new files in the run-defining
    folders; None when there are none. `dirty` says code was edited, this says which edit: two runs on one commit
    with `dirty` true both times are the same code only if this is the same too."""
    diff = _git_bytes(root, "diff", "HEAD", "--", *folders)
    listed = _git_bytes(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *folders)
    if diff is None or listed is None:
        return None
    new = sorted(n for n in listed.split(b"\0") if n and b"__pycache__" not in n and not n.endswith((b".pyc", b".pyo")))
    if not diff and not new:
        return None
    digest = hashlib.sha1(diff)
    for name in new:
        digest.update(b"\0" + name + b"\0")
        path = root / os.fsdecode(name)  # git gives bytes: fsdecode maps back to the same bytes on POSIX, even under an ASCII locale
        try:
            size = path.stat().st_size
            digest.update(f"size={size}".encode() if size > MAX_HASHED_BYTES  # not read whole into memory
                          else path.read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            pass
    return digest.hexdigest()[:10]


def git_info(root: Path | None = None, tracked: tuple[str, ...] | None = None) -> dict:
    root = Path(root or project_root())
    folders = [t for t in (tracked if tracked is not None else code_folders(root)) if (root / t).exists()]
    status = _git(root, "status", "--porcelain", *(["--", *folders] if folders else []))
    dirty = None if status is None else bool(status)
    return {"commit": _git(root, "rev-parse", "--short", "HEAD"), "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": dirty, "changes": _changes_hash(root, folders) if dirty and folders else None}


def requirement_names(root: Path | None = None, filename: str = "requirements.txt") -> list[str]:
    path = Path(root or project_root()) / filename
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.split(r"[=<>!~\[ ]", line, maxsplit=1)[0])
    return names


def package_versions(root: Path | None = None) -> dict:
    versions = {}
    for name in requirement_names(root):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def environment(root: Path | None = None) -> dict:
    return {"python": platform.python_version(), "git": git_info(root), "packages": package_versions(root)}
