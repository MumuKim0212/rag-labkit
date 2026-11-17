"""Review check for a golden set: do the evidence quotes exist on the pages they claim, without an LLM?

    labkit golden-check [--pages corpus/pages.jsonl] [--split dev|test]

The pages file holds the extracted text, one JSON object per line:
    {"file": "report.pdf", "page": 12, "text": "…"}        (`source` for `file` and `content` for `text` also work)
A page may span several lines of the file; they are joined. Set `golden.pages` in labkit.yaml to make it the default.

It checks what golden/README.md's review checklist asks code to check, per item:
- split is dev or test, `answerable` is true or false;
- an answerable item has targets and evidence, and every target (file, page) exists in the pages file
  (file names are compared in NFC);
- every evidence line of 8+ characters (the same segments labkit.eval.metrics scores) appears on one of the
  target pages after NFKC and whitespace removal; a quote with no such line can never match, so it is a problem too;
- an unanswerable item has neither targets nor evidence.

It reads golden items and page text only, never a system result, so it may look at the test split too.
"""

import json
from pathlib import Path

from .eval.metrics import evidence_segments, nfc, squash
from .golden import SPLITS, load_golden, shape_problem
from .project import load_project

FILE_FIELDS = ("file", "source")
TEXT_FIELDS = ("text", "content")


def load_pages(path: Path | str) -> dict[tuple[str, int], str]:
    """{(file in NFC, page): text} from a JSONL of extracted pages."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist: the pages file holds the extracted text, one "
                                '{"file": …, "page": …, "text": …} object per line')
    pages: dict[tuple[str, int], str] = {}
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name}:{number}: not valid JSON: {e}") from e
            file = next((row[k] for k in FILE_FIELDS if isinstance(row, dict) and isinstance(row.get(k), str)), None)
            text = next((row[k] for k in TEXT_FIELDS if isinstance(row, dict) and isinstance(row.get(k), str)), None)
            page = row.get("page") if isinstance(row, dict) else None
            if file is None or text is None or isinstance(page, bool) or not isinstance(page, int):
                raise ValueError(f"{path.name}:{number}: a page needs `file` (or `source`), an integer `page` and "
                                 "`text` (or `content`)")
            key = (nfc(file), page)
            pages[key] = pages[key] + "\n" + text if key in pages else text
    return pages


def check_items(items: list[dict], pages: dict[tuple[str, int], str]) -> list[dict]:
    """[{qid, problem}] for every item that fails a check; empty when the golden set is consistent with the pages."""
    squashed: dict[tuple[str, int], str] = {}

    def text_of(key):
        if key not in squashed:
            squashed[key] = squash(pages[key])
        return squashed[key]

    problems = []

    def add(item, problem):
        problems.append({"qid": item["qid"], "problem": problem})

    for item in items:
        if item.get("split") not in SPLITS:
            add(item, f"split must be dev or test (got {item.get('split')!r})")
        problem = shape_problem(item, target_entries=False)  # what every run refuses; unchecked, it crashed the checks below
        if problem:
            add(item, problem)
            continue
        answerable = item.get("answerable")
        if not isinstance(answerable, bool):
            add(item, f"answerable must be true or false (got {answerable!r})")
            continue
        targets, evidence = item.get("targets") or [], item.get("evidence") or []
        if not answerable:
            if targets or evidence:
                add(item, "unanswerable question lists targets or evidence: it measures refusal, so it has none")
            continue
        if not targets:
            add(item, "answerable question has no targets")
        if not evidence:
            add(item, "answerable question has no evidence quotes")
        keys = []
        for target in targets:
            file, page = (target.get("file"), target.get("page")) if isinstance(target, dict) else (None, None)
            if not isinstance(file, str) or isinstance(page, bool) or not isinstance(page, int):
                add(item, f"a target needs a `file` and an integer `page` (got {target!r})")
            elif (nfc(file), page) in pages:
                keys.append((nfc(file), page))
            else:
                add(item, f"target {file} p.{page} is not in the pages file")
        if not evidence:
            continue
        if not evidence_segments(evidence):
            add(item, "evidence has no line of 8 or more characters, so it can never match a page")
            continue
        if not keys:
            continue  # the missing targets are already reported; nothing to match against
        for quote in evidence:
            for line in quote.splitlines():
                segment = squash(line)
                if len(segment) >= 8 and not any(segment in text_of(key) for key in keys):
                    shown = line.strip()
                    add(item, f"evidence not found on the target pages: {shown[:60]}{'…' if len(shown) > 60 else ''}")
    return problems


def run_check(pages_path: Path | str | None = None, split: str | None = None, root: Path | None = None) -> tuple[str, bool]:
    """(report text, ok). The pages file is --pages, else golden.pages in labkit.yaml."""
    project = load_project(root)
    where = pages_path or project["golden"].get("pages")
    if not where:
        raise ValueError("no pages file: pass --pages, or set `golden.pages` in labkit.yaml")
    path = Path(where)
    if not path.is_absolute():
        path = Path(root or Path.cwd()) / path
    items = load_golden(split=split, root=root, check_split=False)  # a bad split is reported per item below
    problems = check_items(items, load_pages(path))
    failing = sorted({p["qid"] for p in problems})
    lines = [f"golden-check: {len(items)} items{f' ({split})' if split else ''}, pages from {path.name}: "
             f"{len(failing)} with problems, {len(problems)} problems"]
    lines += [f"  {p['qid']}: {p['problem']}" for p in problems]
    return "\n".join(lines), not problems
