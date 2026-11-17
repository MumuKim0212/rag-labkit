"""Start a project from the template (`labkit init`) and add experiment cards to it (`labkit new-card`).

init copies labkit/template/ into a new folder and never overwrites anything: the target must be empty (a
.git folder is fine). new-card takes the next number, copies the project's own experiments/_TEMPLATE.md to
experiments/NNN-slug.md, and writes a variant config configs/NNN-slug.yaml next to it. The card still has its
placeholders, so the card gate (labkit.cards) blocks a real run until sections 1-4 are written.
"""

import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import yaml

from .repro import _git

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # used with fullmatch: a `$` anchor would let a trailing newline through
NUMBERED = re.compile(r"^(\d{3})[a-z]?-")
GIT_TEXT = dict(capture_output=True, text=True, encoding="utf-8", errors="replace")  # git writes UTF-8, not the console code page
PLAIN_BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./#+-]*")  # what is accepted where git is not there to ask


def template_dir() -> Path:
    return Path(__file__).resolve().parent / "template"


def yaml_value(value) -> str:
    """`value` as YAML text that reads back as the same value, quoted when it must be (a title with a colon, `[WIP]`,
    a leading dash or `#`): writing it plain made `description: Hybrid search: alpha 0.5`, which is not YAML."""
    line = yaml.safe_dump({"k": value}, allow_unicode=True, width=10 ** 6).strip()
    return line[len("k: "):]


def check_branch_name(name: str) -> str:
    """A name git accepts for a branch (asked of git itself when it is installed), checked BEFORE init copies anything:
    a rejection after the copy left a folder that init would not touch again."""
    if not name.strip():
        raise ValueError("--main-branch must be a branch name")
    if "@{" in name:
        ok = False
    elif shutil.which("git"):
        ok = subprocess.run(["git", "check-ref-format", "--branch", name], **GIT_TEXT).returncode == 0
    else:
        ok = bool(PLAIN_BRANCH.fullmatch(name)) and ".." not in name and not name.endswith((".", "/", ".lock"))
    if not ok:
        raise ValueError(f"--main-branch {name!r} is not a valid branch name: no spaces, `..`, `~`, `^`, `:`, `?`, "
                         "`*`, `[`, `\\` or `@{`, and it does not start with `-` or end with `/`, `.` or `.lock`")
    return name


# ---------------------------------------------------------------- init

def init_project(target: Path | str, name: str | None = None, git: bool = False, main_branch: str = "main") -> list[str]:
    """Create a project in `target`; returns the created files, relative to it."""
    target = Path(target).resolve()
    if target.exists() and not target.is_dir():
        raise FileExistsError(f"{target} is a file")
    if target.exists() and any(p.name != ".git" for p in target.iterdir()):
        raise FileExistsError(f"{target} is not empty: `labkit init` never overwrites. Use a new or empty folder.")
    check_branch_name(main_branch)
    if git and not (target / ".git").exists() and not shutil.which("git"):
        # before anything is copied: a half-made project is not empty, so init would refuse to try again
        raise RuntimeError("--git needs git on PATH, and git was not found: install git, or run init without --git "
                           "and `git init` later")
    shutil.copytree(template_dir(), target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if name:
        _replace(target / "CLAUDE.md", "# _Project name_: RAG experiment repo", f"# {name}: RAG experiment repo")
    if main_branch != "main":
        _replace(target / "labkit.yaml", "main_branch: main", f"main_branch: {yaml_value(main_branch)}")
    if git and not (target / ".git").exists():
        done = subprocess.run(["git", "init", "-b", main_branch], cwd=target, **GIT_TEXT)
        if done.returncode != 0:  # `-b` needs git 2.28: on an older git, init and then name the branch
            done = subprocess.run(["git", "init"], cwd=target, **GIT_TEXT)
            if done.returncode == 0:
                done = subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{main_branch}"], cwd=target,
                                      **GIT_TEXT)
        if done.returncode != 0:
            raise RuntimeError(f"git init failed: {done.stderr.strip() or done.stdout.strip()}")
    created = (p.relative_to(target) for p in target.rglob("*") if p.is_file())
    return sorted(p.as_posix() for p in created if ".git" not in p.parts)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise ValueError(f"{path.name} no longer contains {old!r}; the template changed, update labkit.scaffold")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")  # LF like the other template files


# ---------------------------------------------------------------- new-card

def next_number(root: Path) -> int:
    """One more than the highest NNN (or NNNb) prefix among cards and configs, so numbers are never reused."""
    found = [0]
    for folder, pattern in (("experiments", "*.md"), ("configs", "*.yaml")):
        for path in (Path(root) / folder).glob(pattern):
            match = NUMBERED.match(path.name)
            if match:
                found.append(int(match.group(1)))
    return max(found) + 1


def _branch_user(root: Path) -> str:
    name = (_git(root, "config", "user.name") or "user").lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "user"


def _fill_header(text: str, number: int, title: str, ident: str, user: str) -> str:
    """Fill the card's header lines (title, status, date, branch and config); sections 1-7 stay as they are."""
    out = []
    for line in text.splitlines():
        if line.startswith("# NNN"):
            line = f"# {number:03d}: {title}"
        elif line.startswith("Status:"):
            line = "Status: planned"
        elif line.startswith("Date:"):
            line = f"Date: {date.today().isoformat()}"
        elif line.startswith("Branch:"):
            line = f"Branch: `{user}/{ident}` · Config: `configs/{ident}.yaml`"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def new_card(root: Path | str, slug: str, title: str | None = None, base: str = "baseline.yaml") -> dict:
    root = Path(root)
    if not SLUG.fullmatch(slug):
        raise ValueError(f"slug {slug!r}: use lowercase letters, digits and single hyphens, e.g. hybrid-search")
    template = root / "experiments" / "_TEMPLATE.md"
    if not template.exists():
        raise FileNotFoundError(f"{template} does not exist: run `labkit new-card` inside a project made by `labkit init`")
    if not (root / "configs" / base).exists():
        raise FileNotFoundError(f"configs/{base} does not exist: `--base` names the config this card builds on")
    number = next_number(root)
    ident = f"{number:03d}-{slug}"
    card, config = root / "experiments" / f"{ident}.md", root / "configs" / f"{ident}.yaml"
    for path in (card, config):
        if path.exists():
            raise FileExistsError(f"{path} already exists")
    title = " ".join((title or slug.replace("-", " ")).split())  # one line: a line break would end the card's heading
    user = _branch_user(root)
    card.write_text(_fill_header(template.read_text(encoding="utf-8"), number, title, ident, user), encoding="utf-8",
                    newline="\n")
    config.write_text(
        f"base: {yaml_value(base)}                      # change to the last adopted config (report/PROCESS.md §6.2)\n"
        f"name: {yaml_value(f'{number:03d}_' + slug.replace('-', '_'))}\n"  # plain 001_12 is YAML 1.1 octal 74
        f"card: experiments/{ident}.md\n"
        f"description: {yaml_value(title)}\n"
        "# compare_to: dev_000_baseline    # the experiment of the chain this builds on\n"
        "\n"
        "# Only the keys this card changes go below: one change per card.\n", encoding="utf-8", newline="\n")
    return {"number": number, "card": card.relative_to(root).as_posix(), "config": config.relative_to(root).as_posix(),
            "branch": f"{user}/{ident}", "experiment": f"dev_{number:03d}_{slug.replace('-', '_')}"}
