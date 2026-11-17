"""Small, dependency-free helpers for evaluating a RAG system without LLM calls.

Modules
- runlog   : SQLite log of every system run (retrieved ids, context, answer, tokens, latency)
- metrics  : retrieval / context metrics against a golden set, and checks for choosing a proxy metric
- paired   : per-question paired comparison with a bootstrap CI (also used to measure noise)
- headroom : before an answer run, count the questions a change could actually fix or break

Run the CLIs with `python -m labkit.eval.paired a.jsonl b.jsonl` (labkit installed, or run from the repo root).
Requires Python 3.10+, standard library only.
"""

import json
import random
import time
from pathlib import Path

READ_SECONDS = 2  # how long a read waits for a file another process is replacing


def read_text(path) -> str:
    """A file's text, tried again for a moment while Windows says another process holds it: labkit replaces results
    files whole (harness._atomic_write), and a read that meets the rename got PermissionError."""
    give_up = time.monotonic() + READ_SECONDS
    while True:
        try:
            return Path(path).read_text(encoding="utf-8")
        except PermissionError:
            if time.monotonic() > give_up:
                raise
            time.sleep(random.uniform(0.001, 0.01))


def load_jsonl(path, where: dict | None = None) -> list[dict]:
    """Rows of a JSONL file. `where` keeps rows whose fields equal the given values (compared as strings),
    so one results file holding several configs can be filtered, e.g. {"method": "vector"}."""
    # split on newlines only: str.splitlines would also cut at a U+2028 that json.dumps leaves inside a string
    rows = [json.loads(line) for line in read_text(path).split("\n") if line.strip()]
    if where:
        rows = [r for r in rows if all(str(r.get(k)) == v for k, v in where.items())]
    return rows


def load_summary(path) -> dict:
    """A results/<experiment>/summary.json. One that cannot be read (an evaluation cut off, a file damaged by hand)
    is a ValueError that names it and says what to do, not a bare JSON message that stops every command."""
    path = Path(path)
    try:
        data = json.loads(read_text(path))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"{path} is not a readable summary ({' '.join(str(e).split())[:80]}): an evaluation that was "
                         f"cut off or a damaged file leaves this. Restore it from git, or remove the folder "
                         f"{path.parent.name} and evaluate the experiment again.") from e
    return data


def to_map(rows: list[dict], field: str, key: str = "qid") -> dict:
    """{row[key]: row[field]} for rows where the field is present and not None. Later rows win,
    so a file with appended re-grades or reruns yields the latest value per question."""
    return {r[key]: r[field] for r in rows if r.get(field) is not None}


def parse_where(*filter_lists: list[str]) -> dict:
    """CLI helper: ["method=vector"], ["context=chunk5"] -> {"method": "vector", "context": "chunk5"}.
    Later lists override earlier ones, so a shared --where can be refined per file."""
    where = {}
    for filters in filter_lists:
        where.update(f.split("=", 1) for f in filters or [])
    return where
