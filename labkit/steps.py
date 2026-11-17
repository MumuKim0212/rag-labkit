"""Where the project is in the eight-step workflow, and who approved what.

The exit criteria of step N are the checkboxes under "## Exit criteria" in docs/steps/NN-*.md, read as Markdown
shows them: a box or a heading inside a code block or an HTML comment is not one, and the section ends at the next
`#` or `##` heading (up to three spaces before it). Headings underlined with === or --- are not recognized. Each one is
either checked by code (a trailing `<!-- auto: name -->` tag, labkit.autochecks) or claimed by someone with
`labkit tick N I --evidence "..."`. When every criterion holds, the owner runs `labkit approve N --by NAME`,
and only then does step N+1 become the current step.

State is report/state.yaml (committed): ticks with their evidence, approvals, and an append-only log. A tick is
tied to the criterion's text; if the step file's wording changes later, the old tick is shown as stale.
tick/approve/revoke are refused on any git branch but `main` (`main_branch` in labkit.yaml), so the record is
one history and cannot conflict between branches. The check needs git; where git cannot report a branch (no work
tree, git missing or failing) it lets the record through, because refusing would make the record impossible there.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .autochecks import AUTOCHECKS
from .paths import state_path, steps_dir
from .project import load_project
from .repro import _git

STEP_COUNT = 8
OPTIONAL = {7}  # may be skipped with a stated reason
CRITERION = re.compile(r"^- \[[ xX]\] (.*?)\s*(?:<!--\s*(?i:auto):\s*([\w-]+)\s*-->)?\s*$")  # AUTO: is the tag too
# a checkbox-looking item, quoted or numbered too, must parse; `- [Docs](x.md) …` is a link, not a box
BULLET_BOX = re.compile(r"^[>\s]*(?:[-*+]|\d+[.)])\s*\[(?![^\]]*\]\()")
# The section is read by CommonMark's rules for what can hide a line: a heading has at most 3 spaces before it, a fence
# opens with 3+ backticks (no backtick after them) or 3+ tildes and closes with at least as many of the same, and an
# HTML comment hides everything up to its `-->`. Setext headings (text underlined with === or ---) are not read.
EXIT_HEADING = re.compile(r"^ {0,3}##[ \t]+Exit criteria(?:[ \t].*)?$")  # `## Exit criteria (per card)` too
SECTION_END = re.compile(r"^ {0,3}#{1,2}(?:[ \t]|$)")  # the next `#` or `##` heading; a `###` inside the section is not
FENCE = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")


def _closes(line: str, fence: str) -> bool:
    match = re.match(r"^ {0,3}(" + re.escape(fence[0]) + "{3,})[ \t]*$", line)
    return bool(match) and len(match.group(1)) >= len(fence)


def _opens_comment(line: str) -> bool:
    """The line starts an HTML comment that it does not close (`<!-- auto: x -->` opens and closes on one line)."""
    start = line.rfind("<!--")
    return start >= 0 and "-->" not in line[start + 4:]


@dataclass
class Criterion:
    index: int
    text: str
    auto: str | None

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()[:8]


@dataclass
class Result:
    criterion: Criterion
    status: str   # ok-auto | ok-claimed | open | fail | stale
    detail: str

    @property
    def ok(self) -> bool:
        return self.status in ("ok-auto", "ok-claimed")


# ---------------------------------------------------------------- step files

def step_file(root: Path, n: int) -> Path:
    if not 1 <= n <= STEP_COUNT:
        raise ValueError(f"steps are numbered 1..{STEP_COUNT}, not {n}")
    found = sorted(steps_dir(root).glob(f"{n:02d}-*.md"))
    if not found:
        raise FileNotFoundError(f"no step file docs/steps/{n:02d}-*.md in {root}: not a labkit project here. Run inside "
                                "one (`labkit init PATH` makes one), or pass --root PATH")
    return found[0]


def criteria(root: Path, n: int) -> list[Criterion]:
    path = step_file(root, n)
    items, fence, in_comment, in_section = [], None, False, False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if fence:  # a checkbox, or an `## Exit criteria`, shown as an example in a code block is not one
            fence = None if _closes(raw, fence) else fence
            continue
        if in_comment:  # nor is one inside a comment, and a `## heading` there ends nothing
            in_comment = "-->" not in raw
            continue
        opened = FENCE.match(raw)
        if opened:
            fence = opened.group(1)
            continue
        in_comment = _opens_comment(raw)
        if not in_section:
            in_section = bool(EXIT_HEADING.match(raw))  # the first real heading, not a mention of it in the text
            continue
        if SECTION_END.match(raw):  # the next section ("## See also", "## Notes"): not an exit criterion
            break
        line = raw.strip()
        if not BULLET_BOX.match(line):
            continue
        match = CRITERION.match(line)
        if not match:  # dropping it would let a step be approved with fewer criteria than the file shows
            raise ValueError(f"{path.name}:{number}: {line[:60]!r} does not parse as a criterion; write "
                             "it as `- [ ] text` (one space after the box, an optional `<!-- auto: name -->` last)")
        if "<!--" in match.group(1):  # `<!-- auto : x -->` or `auto: a, b` is a typo of the tag, not a manual criterion
            raise ValueError(f"{path.name}:{number}: {line[:60]!r} has an HTML comment that is not a "
                             "trailing `<!-- auto: name -->` tag (name: letters, digits, `_`, `-`; nothing after it): "
                             "as written it would be a manual criterion that `labkit tick` can claim, not checked by code")
        items.append(Criterion(len(items) + 1, match.group(1), match.group(2)))
    if not in_section:
        raise ValueError(f"{path.name} has no '## Exit criteria' section")
    if not items:
        raise ValueError(f"{path.name} has no criteria under '## Exit criteria': a step with none would be approved on nothing")
    for item in items:
        if item.auto and item.auto not in AUTOCHECKS:
            raise KeyError(f"{step_file(root, n).name} criterion {item.index} names auto check {item.auto!r}; "
                           f"known: {sorted(AUTOCHECKS)}")
    return items


# ---------------------------------------------------------------- state

def load_state(root: Path) -> dict:
    path = state_path(root)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    state = raw or {}
    state.setdefault("version", 1)
    state["steps"] = {int(k): v for k, v in (state.get("steps") or {}).items()}
    state.setdefault("log", [])
    return state


def save_state(root: Path, state: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Written by `labkit tick / approve / revoke`. Do not edit by hand; this is the audit record.\n"
    path.write_text(header + yaml.safe_dump(state, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _who(root: Path, by: str | None) -> str:
    name = (by or _git(root, "config", "user.name") or "").strip()
    if not name:
        raise ValueError("say who you are: pass --by NAME (or set git config user.name)")
    return name


def _require_main(root: Path) -> None:
    """tick / approve / revoke write the shared record; do them on the main branch so it has one history.
    Outside a git repository there is nothing to check."""
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        return
    main = load_project(root)["main_branch"]
    ref = _git(root, "symbolic-ref", "-q", "HEAD")  # refs/heads/<branch>; the --short form prints heads/<branch> when
    branch = ref.removeprefix("refs/heads/") if ref else None  # a tag has the same name, and refused the real main
    if branch != main:
        raise ValueError(f"run this on the {main!r} branch (you are on {branch or 'a detached HEAD'!r}): "
                         "step claims and approvals are one shared record. Switch to main, or open a worktree for it.")


def _log(state: dict, who: str, action: str, step: int, detail: str = "") -> None:
    state["log"].append({"at": _now(), "by": who, "action": action, "step": step, "detail": detail})


def is_done(state: dict, n: int) -> bool:
    entry = state["steps"].get(n) or {}
    return "approved" in entry or "skipped" in entry


def current_step(state: dict) -> int | None:
    return next((n for n in range(1, STEP_COUNT + 1) if not is_done(state, n)), None)


# ---------------------------------------------------------------- checking

def evaluate(root: Path, n: int, state: dict | None = None) -> list[Result]:
    root = Path(root)
    state = state or load_state(root)
    project = load_project(root)
    claims = (state["steps"].get(n) or {}).get("criteria") or {}
    results = []
    for item in criteria(root, n):
        if item.auto:
            ok, detail = AUTOCHECKS[item.auto](root, project)
            results.append(Result(item, "ok-auto" if ok else "fail", detail))
            continue
        claim = {int(k): v for k, v in claims.items()}.get(item.index)
        if claim is None:
            results.append(Result(item, "open", "not claimed: labkit tick "
                                  f"{n} {item.index} --evidence \"…\""))
        elif claim.get("digest") != item.digest:
            results.append(Result(item, "stale", "the criterion's wording changed since it was claimed; claim it again"))
        else:
            results.append(Result(item, "ok-claimed", f"{claim['by']}, {claim['at']}: {claim['evidence']}"))
    return results


def tick(root: Path, n: int, index: int, evidence: str, by: str | None = None) -> str:
    root = Path(root)
    _require_main(root)
    state = load_state(root)
    current = current_step(state)
    if current is not None and n > current:
        raise ValueError(f"step {n} is not open yet: the current step is {current}")
    if not evidence or not evidence.strip():
        raise ValueError("--evidence is required: say what shows the criterion holds (a file, a section, a number)")
    items = criteria(root, n)
    if not 1 <= index <= len(items):
        raise ValueError(f"step {n} has criteria 1..{len(items)}, not {index}")
    item = items[index - 1]
    if item.auto:
        raise ValueError(f"criterion {index} is checked by code ({item.auto}); it cannot be claimed")
    who = _who(root, by)
    state["steps"].setdefault(n, {}).setdefault("criteria", {})[index] = {
        "digest": item.digest, "evidence": evidence.strip(), "by": who, "at": _now()}
    _log(state, who, "tick", n, f"{index}: {evidence.strip()}")
    save_state(root, state)
    return f"step {n} criterion {index} claimed by {who}"


def approve(root: Path, n: int, by: str | None = None, note: str = "", skip: bool = False) -> str:
    root = Path(root)
    _require_main(root)
    state = load_state(root)
    who = _who(root, by)
    if is_done(state, n):
        raise ValueError(f"step {n} is already approved; use `labkit revoke {n}` first if it must be redone")
    earlier = [m for m in range(1, n) if not is_done(state, m)]
    if earlier:
        raise ValueError(f"step {earlier[0]} is not approved yet; steps are approved in order")
    if skip:
        if n not in OPTIONAL:
            raise ValueError(f"step {n} cannot be skipped; only {sorted(OPTIONAL)} are optional")
        if not note.strip():
            raise ValueError("--note is required to skip a step: say why it is not needed")
        state["steps"].setdefault(n, {})["skipped"] = {"by": who, "at": _now(), "note": note.strip()}
        _log(state, who, "skip", n, note.strip())
        message = f"step {n} skipped by {who}"
    else:
        blocking = [r for r in evaluate(root, n, state) if not r.ok]
        if blocking:
            lines = "\n".join(f"  {r.criterion.index}. [{r.status}] {r.criterion.text}: {r.detail}" for r in blocking)
            raise ValueError(f"step {n} cannot be approved; criteria not met:\n{lines}")
        state["steps"].setdefault(n, {})["approved"] = {"by": who, "at": _now(), "note": note.strip()}
        _log(state, who, "approve", n, note.strip())
        message = f"step {n} approved by {who}"
    save_state(root, state)
    return message


def revoke(root: Path, n: int, reason: str, by: str | None = None) -> str:
    root = Path(root)
    _require_main(root)
    state = load_state(root)
    who = _who(root, by)
    if not is_done(state, n):
        raise ValueError(f"step {n} is not approved")
    later = [m for m in range(n + 1, STEP_COUNT + 1) if is_done(state, m)]
    if later:
        raise ValueError(f"step {later[0]} is approved and builds on step {n}: revoke it first")
    if not reason or not reason.strip():
        raise ValueError("--reason is required: say what was wrong")
    state["steps"][n] = {}  # approval and claims go; the log keeps the history
    _log(state, who, "revoke", n, reason.strip())
    save_state(root, state)
    return f"step {n} revoked by {who}; claim and approve it again after the rework"


# ---------------------------------------------------------------- reports

def format_status(root: Path) -> str:
    root = Path(root)
    state = load_state(root)
    current = current_step(state)
    lines = []
    for n in range(1, STEP_COUNT + 1):
        entry = state["steps"].get(n) or {}
        name = step_file(root, n).stem[3:].replace("-", " ")
        if "approved" in entry:
            a = entry["approved"]
            lines.append(f"  {n}  {name:<14} approved by {a['by']} on {a['at'][:10]}")
        elif "skipped" in entry:
            s = entry["skipped"]
            lines.append(f"  {n}  {name:<14} skipped by {s['by']} on {s['at'][:10]}: {s['note']}")
        elif n == current:
            results = evaluate(root, n, state)
            lines.append(f"  {n}  {name:<14} CURRENT  {sum(r.ok for r in results)}/{len(results)} criteria hold")
        else:
            lines.append(f"  {n}  {name:<14} waiting")
    if current is None:
        lines.append("\nAll steps are approved or skipped.")
    else:
        lines.append(f"\nCurrent step: {current}. Read {step_file(root, current).relative_to(root).as_posix()}, "
                     f"then `labkit check {current}`.")
    return "\n".join(lines)


def format_check(root: Path, n: int | None = None) -> tuple[str, bool]:
    root = Path(root)
    state = load_state(root)
    n = n if n is not None else current_step(state)  # `check 0` is refused by step_file, not read as no number
    if n is None:
        return "All steps are approved or skipped.", True
    entry = state["steps"].get(n) or {}
    head = f"Step {n}: {step_file(root, n).name}"
    if is_done(state, n):
        who = (entry.get("approved") or entry.get("skipped"))
        return f"{head}\n  already {'approved' if 'approved' in entry else 'skipped'} by {who['by']} on {who['at'][:10]} " \
               "(criteria are not re-checked)", True
    results = evaluate(root, n, state)
    labels = {"ok-auto": "ok  (code)", "ok-claimed": "ok  (claimed)", "open": "OPEN", "fail": "FAIL", "stale": "STALE"}
    lines = [head]
    for r in results:
        lines.append(f"  {r.criterion.index}. [{labels[r.status]}] {r.criterion.text}\n       {r.detail}")
    ok = all(r.ok for r in results)
    lines.append("\nAll criteria hold. The owner can run: labkit approve %d --by NAME" % n if ok
                 else "\nNot done: %d of %d criteria hold." % (sum(r.ok for r in results), len(results)))
    return "\n".join(lines), ok
