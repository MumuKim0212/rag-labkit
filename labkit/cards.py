"""Experiment cards (experiments/NNN-slug.md, from experiments/_TEMPLATE.md) and the gate in front of runs.

Sections 1–4 (hypothesis, measurement, go/no-go criteria, LLM budget) are written BEFORE results, so the
criteria cannot be fitted to the outcome. labkit enforces what code can:

- A run of a variant config (one whose `changed` is not empty) needs the card named by `card:` in the config,
  and sections 1–4 of that card must have no template placeholders left. The baseline and noise reruns use the
  baseline config (`changed` is empty), so they are exempt; a smoke test (`--limit` up to SMOKE_LIMIT) is exempt,
  and for that reason it is kept apart: its rows are logged but belong to no result (labkit.eval.runlog.is_smoke).
- The hash of sections 1–4 is recorded when a run starts. `labkit evaluate` compares the oldest hash recorded for
  that card, by any experiment (this machine's run log and the committed summaries), with the card as it is now
  and warns if sections 1–4 were edited since: code cannot prove they were written first, but it can show they
  were not changed afterwards, including between an LLM-free run and the answer run of the same card. "Oldest" is
  by the moment the gate passed (a UTC stamp on the record, and `first_run_at` in a summary), not by when a summary
  was written: a teammate who ran first and evaluated after you still comes first. Times without an offset are read
  as the local clock; a summary from before the stamp is dated by when it was written, an approximation.
- An answer run (not `--retrieval-only`, not a smoke test) also needs section 5 to say the LLM-free gates passed:
  its `- Gates passed?` line starts with `yes`, or `n/a` for an answer-step change, which has no LLM-free gate. Code
  reads the line, not the numbers, so a wrong `yes` is not caught; a forgotten one is.
- The card's `Config:` header must name the config that uses it, or `labkit run` warns (a card is not tied to a
  config by content, so this is what catches a variant that names another change's card). A card outside the
  project is refused.
"""

import hashlib
import posixpath
import re
from datetime import datetime, timezone
from pathlib import Path

SMOKE_LIMIT = 3
SECTION = re.compile(r"^## (\d+)\. ", re.MULTILINE)
# Template placeholders: an _italic span_ (not an identifier like dev_004_x) or a blank like `__`.
PLACEHOLDER = re.compile(r"(?<![\w`])_(?=\S)[^_\n]*?(?<=\S)_(?![\w`])|(?<!\w)__+(?!\w)")
SECTION_NAMES = {1: "Hypothesis", 2: "Measurement", 3: "Go/no-go criteria", 4: "LLM budget"}


def sections(text: str) -> dict[int, str]:
    marks = list(SECTION.finditer(text))
    return {int(m.group(1)): text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
            for i, m in enumerate(marks)}


def problems(path: Path) -> list[str]:
    """What is still unwritten in sections 1–4 of a card."""
    found = sections(Path(path).read_text(encoding="utf-8"))
    out = []
    for number, name in SECTION_NAMES.items():
        if number not in found:
            out.append(f"section {number} ({name}) is missing")
            continue
        bullets = [line.strip() for line in found[number].splitlines() if line.strip().startswith("- ")]
        left = [b for b in bullets if PLACEHOLDER.search(b)]
        if not bullets:
            out.append(f"section {number} ({name}) has no content")
        elif left:
            out.append(f"section {number} ({name}) still has placeholders: {left[0][:70]}")
    return out


def sections_hash(path: Path) -> str:
    found = sections(Path(path).read_text(encoding="utf-8"))
    body = "\n".join(line.rstrip() for n in SECTION_NAMES for line in found.get(n, "").strip().splitlines())
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]


def is_smoke(limit: int | None) -> bool:
    """`--limit` at most SMOKE_LIMIT is a smoke test: it needs no card and its rows count toward nothing."""
    return limit is not None and limit <= SMOKE_LIMIT


def names_config(path: Path, config_file: str | None) -> bool:
    """Does the card's `Config:` header line (`Branch: … · Config: configs/NNN-slug.yaml`) name this config? Content
    cannot say whether sections 1–4 describe the change, but a card that names another config is the sign that
    a variant borrowed a finished card to pass the gate."""
    if not config_file:
        return True
    return any("Config:" in line and config_file in line.replace("\\", "/")
               for line in Path(path).read_text(encoding="utf-8").splitlines())


GATES_LINE = re.compile(r"^- Gates passed\?(.*)$", re.IGNORECASE)


def gates_problem(path: Path) -> str | None:
    """Why section 5 does not say the LLM-free gates passed, or None. `yes …` passes, and so does `n/a …`, which a card
    for an answer-step change writes (it has no LLM-free gate). Code reads the line, not the numbers above it: a wrong
    `yes` is not caught, a forgotten one is."""
    found = sections(Path(path).read_text(encoding="utf-8")).get(5)
    if found is None:
        return "section 5 (LLM-free results) is missing"
    for line in found.splitlines():
        match = GATES_LINE.match(line.strip())
        if not match:
            continue
        answer = match.group(1).strip()
        if not answer or PLACEHOLDER.search(answer):
            return "the `Gates passed?` line of section 5 is not filled in"
        if answer.casefold().startswith(("yes", "n/a")):
            return None
        return f"section 5 says the gates did not pass ({answer[:40]})"
    return "section 5 has no `- Gates passed?` line"


def gate(cfg: dict, root: Path, limit: int | None = None, answer_run: bool = False) -> dict:
    """Raise unless the run may start; return the card record to store with the run ({} when exempt).
    `config_named` is False when the card's header does not name the config using it (the caller warns).
    An answer run (`answer_run`, not a smoke test) also needs section 5 to say the gates passed."""
    if not cfg.get("changed") or is_smoke(limit):
        return {}
    where = cfg.get("config_file") or cfg.get("name")
    card = cfg.get("card")
    if not card:
        raise ValueError(f"{where} changes {sorted(cfg['changed'])} but names no `card:`. "
                         "`labkit new-card <slug>` creates the card and the config; write sections 1–4 and "
                         "set `card:` in the config.")
    path = (Path(root) / card).resolve()
    try:
        relative = path.relative_to(Path(root).resolve()).as_posix()  # `./x.md`, `x\\y.md` and `X.md` all become one name
    except ValueError:
        raise ValueError(f"{where}: card {card} is outside the project") from None
    if not path.exists():
        raise FileNotFoundError(f"{where}: card {card} does not exist")
    if not path.is_file():
        raise ValueError(f"{where}: card {card} is a folder, not a card file")
    left = problems(path)
    if left:
        raise ValueError(f"{card}: write sections 1–4 before running ({'; '.join(left)})")
    gates = gates_problem(path) if answer_run else None
    if gates:  # CLAUDE.md: no batch LLM run without a card whose gates passed
        raise ValueError(f"{card}: {gates}. An answer run comes after the LLM-free gates: run `--retrieval-only` "
                         "and the headroom check, fill section 5 and write `- Gates passed? yes` (a card for an "
                         "answer-step change writes `n/a: answer-step change, always gets an answer run`); if they "
                         "did not pass, decide in section 7 without an answer run")
    return {"file": relative, "sections_hash": sections_hash(path), "at": now_stamp(),
            "config_named": names_config(path, cfg.get("config_file"))}


def same_card(a: str, b: str) -> bool:
    """Do two recorded card paths name one card? Records made before paths were normalized may carry `./`, backslashes
    or another case, so the comparison ignores those; the history of a card must not split by spelling."""
    def key(path: str) -> str:
        return posixpath.normpath(str(path).replace("\\", "/")).casefold()
    return key(a) == key(b)


def now_stamp() -> str:
    """The moment as UTC text with its offset: records made on machines in different time zones then sort correctly."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _instant(text) -> datetime:
    """A recorded time as a comparable moment. A time with no offset (a run-log row, an older record) is read as this
    machine's clock; text that cannot be read sorts last, so a damaged record is never taken as the first use."""
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return datetime.max.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.astimezone()


def earliest(candidates: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """(experiment, sections hash, time) of the oldest of (time, experiment, hash) records, or None. Of equal moments
    the first in the list wins, so list them in the order they were logged."""
    if not candidates:
        return None
    when, experiment, recorded = min(candidates, key=lambda c: _instant(c[0]))
    return experiment, recorded, when


def edited_after(file: str | None, first: tuple | None, root: Path) -> dict | None:
    """Compare the hash recorded when the card was first used (by any experiment) with the card now; None when
    the run had no card. A card is one decision: an edit between its LLM-free run and its answer run counts.
    `first` is (experiment, hash) or (experiment, hash, time); the time, when readable, is passed on in the summary
    as first_run_at so that the next machine compares when the card was used, not when summaries were written. It is
    the best time known: the use time, or for a summary from before the stamp, the time that summary was written."""
    if not file or not first:
        return None
    experiment, recorded, *rest = first
    path = Path(root) / file
    now = sections_hash(path) if path.exists() else None
    return {"file": file, "hash_at_first_run": recorded, "hash_now": now, "first_run_experiment": experiment,
            "first_run_at": rest[0] if rest and _instant(rest[0]) != _instant(None) else None,
            "edited_after_run": now != recorded}
