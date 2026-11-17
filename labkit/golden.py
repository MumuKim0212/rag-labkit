"""Golden items, the index scope each item runs on, and the status each expected behavior allows.

Files are JSONL in golden/ (format: labkit/template/golden/README.md). A qid may be a string or an integer; it is
read as a string everywhere (the run log keeps text) with surrounding blanks removed, so 12, "12" and " 12 " are the
same question. Two qids that differ only by case (D1, d1) are refused rather than run as two questions. Exactly the files listed under
`golden.files` in labkit.yaml are loaded (default: golden.jsonl): a listed file that does not exist is an
error, and files not listed are ignored, so a stray file in golden/ never adds or duplicates questions.

scope: a label telling the system which part of the corpus to search for this item. It comes from, in order,
cfg["corpus"]["set_scope"][item["set"]] (the set must be listed there), the item's own `scope`, or "default".

Every item's `split` must be exactly "dev" or "test": an item with any other value (missing, "Test", " test") matches
neither split, so it would be left out of every run without a word. Loading refuses it, naming the line and qid
(`labkit golden-check` loads with check_split=False to report each one instead). A `--sets` name that no item of the
split has is refused too, instead of running the sets that do exist and calling the experiment complete.

The fields the run reads must have a usable shape, or loading refuses the item with its line and qid: `question`
non-empty text, `set` non-empty text or left out, `targets` a list of {file, page} (null means none), `evidence` a
list of text. They used to fail as a bare KeyError or TypeError after the questions had been run and graded.
"""

import json
import unicodedata
from pathlib import Path

from .paths import golden_dir as default_golden_dir
from .project import load_project

SPLITS = ("dev", "test")


# Characters that print as nothing yet are not control or format characters (so unicodedata's category misses them):
# the Hangul fillers, the combining grapheme joiner, Khmer inherent vowels, Mongolian and ordinary variation selectors,
# the Braille blank. `Q1` plus one of these looks exactly like `Q1`. (The Cf, Cc, Zl, Zp and Zs characters are tested by
# category below.)
_BLANK_RANGES = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x2800, 0x2800),
                 (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xE0100, 0xE01EF))


def _invisible(c: str) -> bool:
    category = unicodedata.category(c)
    return (category in ("Cf", "Cc", "Zl", "Zp") or (category == "Zs" and c != " ")
            or any(low <= ord(c) <= high for low, high in _BLANK_RANGES))


def shape_problem(item: dict, target_entries: bool = True) -> str | None:
    """What is wrong with the shape of an item's fields, or None. These would otherwise surface as a bare KeyError or
    TypeError after the questions were run and graded (a sort of mixed set names, a target without a page).
    `target_entries=False` checks only that `targets` is a list: labkit golden-check reports each bad target itself."""
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        return f"`question` must be non-empty text (got {question!r})"
    name = item.get("set")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        return f"`set` must be non-empty text, or left out (got {name!r})"
    targets = item.get("targets")
    good_target = lambda t: (isinstance(t, dict) and isinstance(t.get("file"), str) and t["file"].strip()  # noqa: E731
                             and isinstance(t.get("page"), int) and not isinstance(t["page"], bool))
    if targets is not None and not (isinstance(targets, list) and (not target_entries or all(map(good_target, targets)))):
        return f'`targets` must be a list of {{"file": text, "page": whole number}} (got {str(targets)[:60]})'
    evidence = item.get("evidence")
    if evidence is not None and not (isinstance(evidence, list) and all(isinstance(e, str) for e in evidence)):
        return f"`evidence` must be a list of text quotes (got {str(evidence)[:60]})"
    for key in ("expected_behavior", "scope"):  # a list here was a TypeError at evaluate, after the run was paid for
        value = item.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return f"`{key}` must be non-empty text, or left out (got {str(value)[:60]})"
    return None


def read_golden_file(path: Path, check_split: bool = True) -> list[dict]:
    items = []
    with Path(path).open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name}:{number}: not valid JSON: {e}") from e
            qid = item.get("qid") if isinstance(item, dict) else None
            if isinstance(qid, bool) or not isinstance(qid, (str, int)) or not str(qid).strip():
                raise ValueError(f"{path.name}:{number}: every golden item needs a `qid` (a string or an integer)")
            qid = unicodedata.normalize("NFC", str(qid).strip())  # NFD and NFC spellings of one Korean qid are one qid
            hidden = next((c for c in qid if _invisible(c)), None)
            if hidden:  # a zero-width space, a Hangul filler or a non-breaking space looks like a plain one
                raise ValueError(f"{path.name}:{number}: qid {qid!r} has an invisible character (U+{ord(hidden):04X})")
            item["qid"] = qid  # the run log stores it as text, so 12 and "12" (and "d1 " and "d1") are one
            if check_split and item.get("split") not in SPLITS:
                raise ValueError(f'{path.name}:{number}: {item["qid"]}: `split` must be exactly "dev" or "test" '
                                 f"(got {item.get('split')!r}); an item with any other value is left out of every run")
            if check_split and (problem := shape_problem(item)):
                raise ValueError(f"{path.name}:{number}: {item['qid']}: {problem}")
            items.append(item)
    return items


def load_golden(split: str | None = None, sets=None, files=None, golden_dir: Path | None = None,
                root: Path | None = None, check_split: bool = True) -> list[dict]:
    golden_dir = Path(golden_dir or default_golden_dir(root))
    items, source, by_case = [], {}, {}
    for name in files or load_project(root)["golden"]["files"]:
        path = golden_dir / name
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist. Golden files are the ones listed under `golden.files` "
                                    "in labkit.yaml (default: golden.jsonl).")
        for item in read_golden_file(path, check_split):
            qid = item["qid"]
            if qid in source:
                raise ValueError(f"duplicate qid {qid!r} in {source[qid]} and {name}")
            if qid.casefold() in by_case:  # D1 and d1 would be two questions, but nobody means that
                raise ValueError(f"qid {qid!r} differs from {by_case[qid.casefold()]!r} only by case ({name}): "
                                 "rename one, or they look like the same question to a person")
            source[qid] = name
            by_case[qid.casefold()] = qid
            items.append(item)
    if split:
        items = [i for i in items if i.get("split") == split]
    if sets:
        known = {i["set"] for i in items if isinstance(i.get("set"), str)}
        unknown = sorted(set(sets) - known)
        if unknown:
            raise ValueError(f"unknown set(s) {', '.join(map(str, unknown))} for the {split or 'golden'} split; "
                             f"it has: {', '.join(sorted(known)) or 'no sets'}")
        items = [i for i in items if i.get("set") in set(sets)]
    return items


def scope_of(item: dict, cfg: dict) -> str:
    set_scope = (cfg.get("corpus") or {}).get("set_scope")
    if set_scope:
        return set_scope[item["set"]]
    return item.get("scope", "default")


def check_scopes(items: list[dict], cfg: dict) -> None:
    """Every golden set the run covers must have an entry in `corpus.set_scope` (when the config has one). Found per
    question, in the middle of a batch, it stopped the run after the earlier questions had been paid for."""
    set_scope = (cfg.get("corpus") or {}).get("set_scope")
    if not set_scope or not isinstance(set_scope, dict):
        return
    missing = sorted({str(i.get("set")) for i in items if i.get("set") not in set_scope})
    if missing:
        raise ValueError(f"`corpus.set_scope` in the config has no entry for the golden set(s) {', '.join(missing)}; "
                         f"it names {', '.join(map(str, set_scope)) or 'none'}. Add them before the run: a question "
                         "with no scope would stop the batch after the earlier ones were paid for.")


def behavior_of(item: dict, project: dict) -> tuple[str | None, set[str] | None]:
    """(reporting group, statuses that count as the right response), or (None, None) when the project
    defines no behaviors. An item whose behavior is not in the table is an error, not a silent skip."""
    behaviors = project.get("behaviors") or {}
    if not behaviors:
        return None, None
    behavior = item.get("expected_behavior") or (project.get("answerable_default") if item.get("answerable") else None)
    if behavior not in behaviors:
        raise KeyError(f"{item['qid']}: no `behaviors` entry in labkit.yaml for expected_behavior={behavior!r}")
    entry = behaviors[behavior]
    return entry["group"], set(entry["statuses"])


def status_ok(item: dict, run: dict, project: dict) -> int | None:
    """1 if the system's status is one the item's expected behavior allows; None when no behaviors are defined."""
    _, allowed = behavior_of(item, project)
    return None if allowed is None else int(run.get("status") in allowed)
