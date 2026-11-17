"""Log every system run to SQLite so later analysis does not need a rerun.

One row per run of one question. Store what was retrieved and what was actually passed to the answer
step, not only the answer: questions like "was the gold page in the context?" or "did re-search help?"
are then answered from the log, without calling an LLM.

    from labkit.eval.runlog import RunLog
    log = RunLog("out/runs.db")
    log.save(result, experiment="dev_baseline", qid="Q001")
    done = log.done_qids("dev_baseline")      # resume an interrupted batch
    runs = log.latest("dev_baseline")         # last non-error, non-smoke run per question

    python -m labkit.eval.runlog export out/runs.db dev_baseline out/runs_dev_baseline.json
    python -m labkit.eval.runlog export out/runs.db dev_baseline results/dev_baseline/runs.jsonl  # one run per line
    python -m labkit.eval.runlog export out/runs.db test_x out.jsonl --final   # a test experiment needs --final

`result` is a dict with any of the COLUMNS below. Keep `context` as the exact list of units
(chunks or pages) given to the answer step, each {chunk_id, source, page, content}, so
labkit.eval.metrics.context_metrics can score it later.
"""

import json
import re
import sqlite3
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

JSON_FIELDS = ("config", "searches", "context", "citations", "steps")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def without_surrogates(value):
    """The value with every lone UTF-16 surrogate in its strings (a half emoji cut by a token limit, say) replaced by
    U+FFFD. UTF-8, which SQLite and the files labkit writes use, cannot hold one: saving it raised after the call
    had been paid for."""
    if isinstance(value, str):
        return _LONE_SURROGATE.sub("�", value)
    if isinstance(value, dict):
        return {without_surrogates(k): without_surrogates(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [without_surrogates(v) for v in value]
    return value
COLUMNS = (
    "system", "config", "question", "answer", "status", "searches", "context", "citations", "steps",
    "n_searches", "n_llm_calls", "input_tokens", "output_tokens", "latency_s", "error",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    experiment    TEXT,     -- experiment name, e.g. dev_baseline
    qid           TEXT,     -- golden-set question id
    system        TEXT,     -- rag / agent / ...
    config        TEXT,     -- JSON: chunking, retrieval method, k, models, prompt versions
    question      TEXT,
    answer        TEXT,     -- final answer including the source list
    status        TEXT,     -- answered / abstained / error
    searches      TEXT,     -- JSON [{query, chunk_ids, seconds}] in search order
    context       TEXT,     -- JSON [{chunk_id, source, page, content}] passed to the answer step
    citations     TEXT,     -- JSON [{chunk_id, source, page}] cited by the answer
    steps         TEXT,     -- JSON: intermediate decisions, e.g. an agent's sufficiency judgments
    n_searches    INTEGER,
    n_llm_calls   INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_s     REAL,
    error         TEXT,
    created_at    TEXT
)"""


def is_smoke(row: dict) -> bool:
    """A smoke-test row (`labkit run --limit N` with N small). It is logged so the output can be read, but it
    belongs to no result: it is not resumed from, graded, evaluated, or checked for frozen files."""
    return bool((row.get("config") or {}).get("smoke"))


class RunLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # batch runs usually write from a thread pool

    def _query(self, sql: str, params=()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commits on success
                conn.execute(SCHEMA)
                return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def save(self, result: dict, experiment: str, qid: str | None = None) -> str:
        row = without_surrogates({
            "run_id": uuid.uuid4().hex[:12],
            "experiment": experiment,
            "qid": qid,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            **{c: result.get(c) for c in COLUMNS},
        })
        for field in JSON_FIELDS:
            row[field] = json.dumps(row[field], ensure_ascii=False)
        with self._lock:
            self._query(
                f"INSERT INTO runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()),
            )
        return row["run_id"]

    def load(self, experiment: str) -> list[dict]:
        rows = [dict(r) for r in self._query(
            "SELECT * FROM runs WHERE experiment = ? ORDER BY created_at, rowid", (experiment,))]
        for row in rows:
            for field in JSON_FIELDS:
                row[field] = json.loads(row[field]) if row[field] else None
        return rows

    def latest(self, experiment: str, include_errors: bool = False, include_smoke: bool = False) -> list[dict]:
        """Last run per question (a rerun after a failure replaces the failed one). Smoke-test rows are left out."""
        by_qid = {}
        for row in self.load(experiment):
            if (include_errors or row["status"] != "error") and (include_smoke or not is_smoke(row)):
                by_qid[row["qid"]] = row
        return list(by_qid.values())

    def card_records(self, card_file: str, same=lambda a, b: a == b) -> list[tuple[str, str, str]]:
        """(created_at, experiment, sections hash) of every real run, in any experiment, that passed the card gate
        for this card file, in the order they were logged (smoke tests are exempt from the gate: no record).
        `same(recorded, wanted)` decides whether two recorded paths are one card (labkit.cards.same_card)."""
        found = []
        for row in self._query("SELECT experiment, created_at, config FROM runs WHERE config LIKE ? "
                               "ORDER BY created_at, rowid", ('%"card_check"%',)):
            check = (json.loads(row["config"]) or {}).get("card_check") or {}
            if check.get("file") and same(check["file"], card_file) and check.get("sections_hash"):
                # `at` is when the gate passed (UTC); a row from before that field is dated by its own row time
                found.append((check.get("at") or row["created_at"], row["experiment"], check["sections_hash"]))
        return found

    def done_qids(self, experiment: str) -> set[str]:
        return {r["qid"] for r in self.latest(experiment)}

    def experiments(self) -> list[tuple[str, int]]:
        return [(r["experiment"], r["n"]) for r in self._query(
            "SELECT experiment, COUNT(*) AS n FROM runs GROUP BY experiment ORDER BY MIN(created_at)")]


def main():
    final = "--final" in sys.argv
    argv = [a for a in sys.argv if a != "--final"]
    if len(argv) >= 4 and argv[1] == "export":
        db, experiment = argv[2], argv[3]
        out = argv[4] if len(argv) > 4 else f"runs_{experiment}.json"
        runs = RunLog(db).load(experiment)
        # the split the runs recorded, else the name's first part (rows from before the record): a test experiment's
        # answers are test numbers, and every labkit command asks for --final before showing them
        recorded = {((r.get("config") or {}).get("selection") or {}).get("split") for r in runs} - {None}
        if not final and any(s.casefold().startswith("test") for s in (recorded or {experiment.split("_", 1)[0]})):
            print(f"{experiment} is a test experiment: exporting its answers needs --final (the last workflow step "
                  "only)", file=sys.stderr)
            sys.exit(1)
        if out.endswith(".jsonl"):  # one run per line: diff-friendly for results/<experiment>/runs.jsonl
            text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs)
        else:
            text = json.dumps(runs, ensure_ascii=False, indent=2)
        Path(out).parent.mkdir(parents=True, exist_ok=True)  # e.g. scratch/ does not exist in a new project
        Path(out).write_text(text, encoding="utf-8", newline="\n")
        print(f"exported {len(runs)} runs -> {out}")
    elif len(argv) == 3 and argv[1] == "list":
        for name, n in RunLog(argv[2]).experiments():
            print(f"{name}\t{n}")
    else:
        print("usage: python -m labkit.eval.runlog export <db> <experiment> [out.json | out.jsonl] [--final]\n"
              "       python -m labkit.eval.runlog list <db>")
        sys.exit(1)


if __name__ == "__main__":
    main()
