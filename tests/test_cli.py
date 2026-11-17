"""The labkit command line, in-process against a temp project whose own code is loaded through `modules:`."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from labkit.cli import main
from labkit.config import load_config
from labkit.env import TRACING_VARS
from labkit.eval.runlog import RunLog
from labkit.judges import JUDGES
from labkit.systems import SYSTEMS

GOLDEN = [
    {"qid": "POS001", "set": "s", "split": "dev", "question": "휴면성 증권계좌는?", "answerable": True,
     "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}],
     "evidence": ["휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"]},
    {"qid": "NEG001", "set": "s", "split": "dev", "question": "오늘 최고금리는?", "answerable": False,
     "expected_behavior": "abstain_and_redirect", "targets": [], "evidence": []},
    {"qid": "POS002", "set": "s", "split": "test", "question": "휴면 test", "answerable": True,
     "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}], "evidence": []},
]
PLUGIN = '''
from labkit.cost import record_call
from labkit.judges import register_judge
from labkit.systems import register_system

PAGE = {"chunk_id": "c1", "source": "a.pdf", "page": 3, "content": "휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"}
OTHER = {"chunk_id": "c2", "source": "b.pdf", "page": 1, "content": "관련 없는 내용"}


class Sys:
    def retrieve(self, question, scope, steps=None):
        return [PAGE, OTHER] if "휴면" in question else [OTHER]

    def run(self, question, scope):
        steps = {}
        record_call(steps, node="generate", model="m", input_tokens=1000, output_tokens=200, seconds=0.1)
        if "휴면" in question:
            return {"status": "answered", "answer": "6개월 [1]", "context": self.retrieve(question, scope),
                    "steps": steps, "n_searches": 1, "citations": [{"chunk_id": "c1", "source": "a.pdf", "page": 3}]}
        return {"status": "abstained", "answer": "자료 없음", "context": [OTHER], "steps": steps, "n_searches": 1,
                "citations": []}


@register_system("cli-sys")
def make(cfg):
    return Sys()


@register_judge("cli-judge")
def grade(item, run, cfg):
    calls = []
    record_call({"calls": calls}, node="judge", model="j", input_tokens=500, output_tokens=50)
    return {"correctness": 2 if run["status"] == "answered" else 1, "reason": "fake", "calls": calls}
'''


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    for var in TRACING_VARS:                      # setup() forces these off; restore the original state afterwards
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    write(tmp_path / "labkit.yaml", """
golden: {files: [provided.jsonl]}
pythonpath: [src]
modules: [lk_cli_pkg.plugins]
behaviors:
  answer_with_citation: {group: answer, statuses: [answered]}
  abstain_and_redirect: {group: abstain, statuses: [abstained]}
""")
    write(tmp_path / "configs" / "baseline.yaml",
          "name: 000_baseline\nsystem: cli-sys\njudge: {name: cli-judge, model: j}\nrun: {concurrency: 1}\n")
    write(tmp_path / "configs" / "pricing.yaml", "models:\n  m: {input: 0.75, output: 3.75}\n  j: {input: 0.75, output: 3.75}\n")
    write(tmp_path / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in GOLDEN))
    write(tmp_path / "src" / "lk_cli_pkg" / "__init__.py", "")
    write(tmp_path / "src" / "lk_cli_pkg" / "plugins.py", PLUGIN)
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    for name, registry in (("cli-sys", SYSTEMS), ("cli-judge", JUDGES)):
        registry.pop(name, None)
    for module in [m for m in sys.modules if m.startswith("lk_cli_pkg")]:
        del sys.modules[module]
    sys.path[:] = [p for p in sys.path if not p.endswith("src")]


def test_full_workflow_through_the_cli(project, capsys):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev"]) == 0
    out = capsys.readouterr().out
    assert "dev_000_baseline: changed vs baseline = nothing" in out and "'ran': 2" in out
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev"]) == 0     # resumes: nothing left to run
    assert "'ran': 0" in capsys.readouterr().out

    assert main(["grade", "--experiment", "dev_000_baseline"]) == 0
    assert "'graded_now': 2" in capsys.readouterr().out
    assert main(["evaluate", "--experiment", "dev_000_baseline", "--with-runs"]) == 0
    summary = json.loads((project / "results" / "dev_000_baseline" / "summary.json").read_text(encoding="utf-8"))
    assert summary["scores"]["correctness_mean"] == 0.75 and summary["metrics"]["status_ok"] == 1.0
    capsys.readouterr()

    # a second run of the same config, measured the way the noise step does
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--name", "dev_000_baseline_r2"]) == 0
    assert main(["grade", "--experiment", "dev_000_baseline_r2"]) == 0
    assert main(["evaluate", "--experiment", "dev_000_baseline_r2"]) == 0
    capsys.readouterr()
    assert main(["compare", "dev_000_baseline", "dev_000_baseline_r2", "--noise", "0"]) == 0
    out = capsys.readouterr().out
    assert "n=2" in out and "USD/question" in out and "net +0 questions vs noise 0" in out
    assert main(["compare", "dev_000_baseline", "dev_000_baseline_r2", "--metric", "page_hit@5"]) == 0
    capsys.readouterr()

    assert main(["report"]) == 0
    assert "dev_000_baseline_r2" in (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")


HEADER = "# 004: x\n\nStatus: planned\nBranch: `ana/004-x` · Config: `configs/004-x.yaml`\n\n"
CARD = HEADER + "".join(f"## {n}. {name}\n\n- {text}\n\n" for n, name, text in (
    (1, "Hypothesis", "Change one variable: retrieval.k"), (2, "Measurement", "Compared against dev_000_baseline"),
    (3, "Go/no-go criteria", "Adopt if net wins are at least 3"), (4, "LLM budget", "4 answers and 4 judge calls"))
) + "## 5. Results\n\nnone yet\n\n- Gates passed? yes\n"


def variant(project, card_text=CARD, card="experiments/004-x.md"):
    write(project / "configs" / "004-x.yaml", f"base: baseline.yaml\nname: 004_x\ncard: {card}\nretrieval: {{k: 9}}\n")
    return write(project / card, card_text)


def test_evaluate_warns_when_answers_were_never_graded(project, capsys):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_000_baseline"]) == 0
    assert "is incomplete" in capsys.readouterr().err


def test_evaluate_warns_when_a_batch_was_finished_on_another_commit(project, capsys, monkeypatch):
    write(project / "golden" / "provided.jsonl", "".join(
        json.dumps({**GOLDEN[0], "qid": f"POS{i}"}, ensure_ascii=False) + "\n" for i in range(1, 7)))
    for sha, extra in (("aaaaaaa", ["--limit", "4"]), ("bbbbbbb", [])):
        monkeypatch.setattr("labkit.harness.environment", lambda root, sha=sha: {"git": {"commit": sha}})
        assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", *extra]) == 0
    assert main(["grade", "--experiment", "dev_000_baseline"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_000_baseline"]) == 0
    assert "different versions of the code (aaaaaaa, bbbbbbb" in capsys.readouterr().err


def test_run_says_what_a_chained_variant_adds_to_its_base(project, capsys):
    write(project / "configs" / "004-x.yaml", "base: baseline.yaml\nname: 004_x\nretrieval: {k: 9}\n")
    write(project / "configs" / "005-y.yaml", "base: 004-x.yaml\nname: 005_y\nretrieval: {k: 10}\n")
    assert main(["run", "--config", "configs/005-y.yaml", "--split", "dev", "--limit", "1"]) == 0
    assert "changed vs its base" in capsys.readouterr().out
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--limit", "1"]) == 0
    assert "changed vs its base" not in capsys.readouterr().out                   # built on the baseline: nothing to add


def test_a_card_that_does_not_name_the_config_using_it_is_warned_about_not_refused(project, capsys):
    """Nothing tied a card to a config: a variant could borrow another change's finished card to pass the gate."""
    other = HEADER.replace("configs/004-x.yaml", "configs/001-alpha.yaml")
    for text, why in ((other + CARD[len(HEADER):], "another config"), (CARD.replace("Config:", "Konfig:"), "no Config line")):
        variant(project, text)
        assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--limit", "4", "--name", "dev_a"]) == 0
        err = capsys.readouterr().err
        assert "warning" in err and "experiments/004-x.md" in err and "configs/004-x.yaml" in err, why
        (project / "out" / "runs.db").unlink()
    variant(project)
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--limit", "4"]) == 0
    assert "warning" not in capsys.readouterr().err


def test_a_card_outside_the_project_is_refused(project, capsys):
    variant(project, card="../card.md")
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--limit", "4"]) == 1
    assert "outside the project" in capsys.readouterr().err


def test_a_card_edited_between_two_experiments_of_the_same_card_is_noticed(project, capsys):
    """The LLM-free run and the answer run are two experiments on one card. Each one's 'first run' hash was taken
    from the card as it was then, so an edit in between was invisible to both."""
    card = variant(project)
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--retrieval-only"]) == 0
    assert main(["evaluate", "--experiment", "dev_004_x_retrieval"]) == 0
    capsys.readouterr()
    write(card, CARD.replace("at least 3", "at least 1"))                       # loosened after seeing the proxy result
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_004_x"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_004_x"]) == 0
    err = capsys.readouterr().err
    assert "sections 1–4 were edited after the first run of dev_004_x_retrieval" in err
    summary = json.loads((project / "results" / "dev_004_x" / "summary.json").read_text(encoding="utf-8"))
    assert summary["card_check"]["edited_after_run"] and summary["card_check"]["first_run_experiment"] == "dev_004_x_retrieval"


def test_the_spelling_of_a_card_path_does_not_split_its_history(project, capsys):
    """`./experiments/x.md` and `experiments/x.md` were two cards to the history, so the edit between two runs went
    unseen when the second config spelled the path differently."""
    card = variant(project)
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--retrieval-only"]) == 0
    assert main(["evaluate", "--experiment", "dev_004_x_retrieval"]) == 0
    first = json.loads((project / "results" / "dev_004_x_retrieval" / "summary.json").read_text(encoding="utf-8"))
    assert first["card_check"]["file"] == "experiments/004-x.md"
    write(card, CARD.replace("at least 3", "at least 1"))
    write(project / "configs" / "005-y.yaml",
          "base: baseline.yaml\nname: 005_y\ncard: ./experiments/004-x.md\nretrieval: {k: 9}\n")      # another spelling
    capsys.readouterr()
    assert main(["run", "--config", "configs/005-y.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_005_y"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_005_y"]) == 0
    assert "after the first run of dev_004_x_retrieval" in capsys.readouterr().err
    second = json.loads((project / "results" / "dev_005_y" / "summary.json").read_text(encoding="utf-8"))
    assert second["card_check"]["file"] == "experiments/004-x.md" and second["card_check"]["edited_after_run"]


def test_the_report_notices_a_card_edited_after_the_experiment_was_evaluated(project, capsys):
    """The card check ran inside evaluate only, so an edit made afterwards was never flagged, while the report
    already re-checked the input files live."""
    card = variant(project)
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_004_x"]) == 0
    assert main(["evaluate", "--experiment", "dev_004_x"]) == 0
    assert main(["report"]) == 0
    assert "card sections 1–4 edited" not in (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")
    write(card, CARD.replace("at least 3", "at least 1"))                        # loosened after the result was known
    assert main(["report"]) == 0
    md = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "card sections 1–4 edited since it was first used" in md
    assert "card sections 1–4 edited" in (project / "out" / "report.html").read_text(encoding="utf-8")


def test_a_card_that_names_another_config_is_warned_about_before_the_batch_is_paid_for(project):
    """The warning came back in the result, after every question had been run and paid for."""
    from labkit.harness import run_experiment
    from labkit.config import load_config
    from labkit.plugins import load_modules
    load_modules(project)                                  # registers cli-judge, which `run` now checks, as the CLI does
    variant(project, HEADER.replace("configs/004-x.yaml", "configs/001-alpha.yaml") + CARD[len(HEADER):])

    class Stops:
        def retrieve(self, question, scope, steps=None):
            return []

        def run(self, question, scope):
            raise NotImplementedError("stage not built")                       # aborts the batch at the first question
    seen = []
    with pytest.raises(NotImplementedError):
        run_experiment(load_config(project / "configs" / "004-x.yaml", root=project), "dev", experiment="dev_x",
                       system=Stops(), warn=seen.append, root=project)
    assert len(seen) == 1 and "no `Config:` header line naming configs/004-x.yaml" in seen[0]


CARD_FILE = "experiments/004-x.md"


def card_use(at, experiment, hash_, tmp_path):
    """One run in the local log that passed the card gate for CARD_FILE at `at`."""
    RunLog(tmp_path / "runs.db").save(
        {"status": "answered", "config": {"card_check": {"file": CARD_FILE, "sections_hash": hash_, "at": at}}},
        experiment=experiment, qid="Q1")


def team_summary(tmp_path, name, created_at, hash_, first_run_at=None):
    check = {"file": CARD_FILE, "hash_at_first_run": hash_, "hash_now": hash_, "edited_after_run": False}
    if first_run_at:
        check["first_run_at"] = first_run_at
    write(tmp_path / "results" / name / "summary.json",
          json.dumps({"experiment": name, "created_at": created_at, "card_check": check}))


def test_a_summary_is_dated_by_when_the_card_was_used_not_when_it_was_evaluated(tmp_path):
    """The local log is ordered by run time and a committed summary was ordered by evaluation time. A teammate who ran
    first (09:00) and evaluated after my run (11:00) was taken as later, so the edit between our runs was missed."""
    from labkit.harness import _card_check
    card_use("2026-01-01T10:00:00+00:00", "dev_local", "LOCAL", tmp_path)
    team_summary(tmp_path, "dev_team", "2026-01-01T11:00:00+00:00", "TEAM", first_run_at="2026-01-01T09:00:00+00:00")
    own = {"file": CARD_FILE, "sections_hash": "LOCAL", "at": "2026-01-01T10:00:00+00:00"}
    check = _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)
    assert check["first_run_experiment"] == "dev_team" and check["hash_at_first_run"] == "TEAM"
    assert check["first_run_at"] == "2026-01-01T09:00:00+00:00"                    # carried on, for the next comparison


def test_records_in_different_time_zones_are_ordered_by_the_moment_not_by_their_text(tmp_path):
    """12:00+09:00 is 03:00 UTC, earlier than 05:00 UTC although the text sorts after it."""
    from labkit.harness import _card_check
    card_use("2026-01-01T05:00:00+00:00", "dev_local", "LOCAL", tmp_path)
    team_summary(tmp_path, "dev_seoul", "2026-01-02T00:00:00+00:00", "SEOUL", first_run_at="2026-01-01T12:00:00+09:00")
    own = {"file": CARD_FILE, "sections_hash": "LOCAL", "at": "2026-01-01T05:00:00+00:00"}
    check = _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)
    assert check["first_run_experiment"] == "dev_seoul" and check["first_run_at"] == "2026-01-01T12:00:00+09:00"


def test_a_log_row_is_dated_by_when_the_card_gate_passed_not_by_when_the_row_was_written(tmp_path):
    """A batch writes its rows for minutes or hours after the gate; the row time is the log's, the stamp is the card's."""
    from labkit.harness import _card_check
    card_use("2026-01-01T08:00:00+00:00", "dev_local", "LOCAL", tmp_path)               # row written now, gate in January
    team_summary(tmp_path, "dev_team", "2026-01-05T00:00:00+00:00", "TEAM", first_run_at="2026-01-01T09:00:00+00:00")
    own = {"file": CARD_FILE, "sections_hash": "LOCAL", "at": "2026-01-01T08:00:00+00:00"}
    check = _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)
    assert check["first_run_experiment"] == "dev_local" and check["hash_at_first_run"] == "LOCAL"


def test_a_summary_without_a_use_time_falls_back_to_its_evaluation_time(tmp_path):
    from labkit.harness import _card_check
    card_use("2026-01-01T10:00:00+00:00", "dev_local", "LOCAL", tmp_path)
    team_summary(tmp_path, "dev_old", "2026-01-01T08:00:00+00:00", "OLD")            # a summary from before the stamp
    own = {"file": CARD_FILE, "sections_hash": "LOCAL", "at": "2026-01-01T10:00:00+00:00"}
    check = _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)
    # the best time known is passed on: for a summary with no use time that is when it was written. Dropping it would
    # make the next machine date this record by a later summary's time, and the order would drift later each time.
    assert check["first_run_experiment"] == "dev_old" and check["first_run_at"] == "2026-01-01T08:00:00+00:00"
    team_summary(tmp_path, "dev_late", "2026-01-01T12:00:00+00:00", "LATE")
    assert _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)["first_run_experiment"] == "dev_old"


def test_a_log_row_from_before_the_stamp_is_dated_by_its_row_time(tmp_path):
    from labkit.harness import _card_check
    RunLog(tmp_path / "runs.db").save(
        {"status": "answered", "config": {"card_check": {"file": CARD_FILE, "sections_hash": "OLDROW"}}},
        experiment="dev_oldrow", qid="Q1")                                          # no `at`: a run logged before this field
    team_summary(tmp_path, "dev_team", "2099-01-01T00:00:00+00:00", "FUTURE", first_run_at="2099-01-01T00:00:00+00:00")
    own = {"file": CARD_FILE, "sections_hash": "OLDROW"}
    assert _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)["first_run_experiment"] == "dev_oldrow"


def test_instants_compare_across_time_zones_and_naive_local_times():
    from datetime import datetime
    from labkit.cards import _instant
    assert _instant("2026-01-01T12:00:00+09:00") == _instant("2026-01-01T03:00:00+00:00")        # the same moment
    assert _instant("2026-01-01T12:00:00") == datetime(2026, 1, 1, 12).astimezone()               # naive = this machine's clock
    assert _instant("2026-01-01T09:00:00+00:00") < _instant("2026-01-01T10:00:00+00:00")
    for unreadable in ("garbage", "9999", "", None):
        assert _instant(unreadable) > _instant("2999-01-01T00:00:00+00:00")                       # last, never first


def test_the_card_gate_stamps_when_the_card_was_used(project):
    from datetime import datetime, timedelta, timezone
    from labkit import cards
    variant(project)
    cfg = load_config(project / "configs" / "004-x.yaml", root=project)
    stamp = datetime.fromisoformat(cards.gate(cfg, project)["at"])
    assert stamp.tzinfo is not None and abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=1)


def test_every_summary_of_a_card_carries_the_time_of_its_first_use(project, capsys):
    card = variant(project)
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev", "--retrieval-only"]) == 0
    assert main(["evaluate", "--experiment", "dev_004_x_retrieval"]) == 0
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_004_x"]) == 0
    assert main(["evaluate", "--experiment", "dev_004_x"]) == 0
    first, second = (json.loads((project / "results" / name / "summary.json").read_text(encoding="utf-8"))["card_check"]
                     for name in ("dev_004_x_retrieval", "dev_004_x"))
    assert first["first_run_at"] and second["first_run_at"] == first["first_run_at"]      # the later one points back to it
    assert second["first_run_experiment"] == "dev_004_x_retrieval"


def test_a_newer_record_never_hides_an_older_one(project, capsys):
    """The oldest record decides, wherever it comes from: a summary dated later than this machine's own run must not
    replace it (picking the last record, or the newest, would pass the other card tests)."""
    variant(project)
    write(project / "results" / "dev_009_theirs" / "summary.json", json.dumps({
        "experiment": "dev_009_theirs", "created_at": "2099-01-01T00:00:00",
        "card_check": {"file": "experiments/004-x.md", "hash_at_first_run": "ffffffffff", "hash_now": "ffffffffff",
                       "edited_after_run": True}}))
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_004_x"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_004_x"]) == 0
    assert "warning" not in capsys.readouterr().err
    summary = json.loads((project / "results" / "dev_004_x" / "summary.json").read_text(encoding="utf-8"))
    assert summary["card_check"]["first_run_experiment"] == "dev_004_x" and not summary["card_check"]["edited_after_run"]


def test_the_earliest_record_also_counts_when_it_comes_from_a_teammates_committed_summary(project, capsys):
    from labkit.cards import sections_hash
    card = variant(project)
    write(project / "results" / "dev_004_x_retrieval" / "summary.json", json.dumps({
        "experiment": "dev_004_x_retrieval", "created_at": "2020-01-01T00:00:00",
        "card_check": {"file": "experiments/004-x.md", "hash_at_first_run": "0123456789", "hash_now": "0123456789",
                       "edited_after_run": False}}))                            # not in this machine's out/runs.db
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_004_x"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_004_x"]) == 0
    assert "after the first run of dev_004_x_retrieval" in capsys.readouterr().err
    summary = json.loads((project / "results" / "dev_004_x" / "summary.json").read_text(encoding="utf-8"))
    assert summary["card_check"]["hash_at_first_run"] == "0123456789" and sections_hash(card) != "0123456789"


def test_variant_needs_a_written_card_and_evaluate_warns_when_it_was_edited(project, capsys):
    write(project / "configs" / "004-x.yaml",
          "base: baseline.yaml\nname: 004_x\ncard: experiments/004-x.md\nretrieval: {k: 9}\n")
    card = write(project / "experiments" / "004-x.md", CARD.replace("Change one variable: retrieval.k", "Change _what_"))
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 1
    assert "still has placeholders" in capsys.readouterr().err
    write(card, CARD)
    assert main(["run", "--config", "configs/004-x.yaml", "--split", "dev"]) == 0
    assert main(["grade", "--experiment", "dev_004_x"]) == 0
    capsys.readouterr()
    assert main(["evaluate", "--experiment", "dev_004_x"]) == 0
    assert "warning" not in capsys.readouterr().err
    write(card, CARD.replace("at least 3", "at least 1"))                       # the criteria were loosened afterwards
    assert main(["evaluate", "--experiment", "dev_004_x", "--overwrite"]) == 0
    err = capsys.readouterr().err
    assert "warning" in err and "sections 1–4 were edited after the first run of dev_004_x" in err


def test_retrieval_only_and_sets_and_limit(project, capsys):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--retrieval-only", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert "dev_000_baseline_retrieval" in out and "'ran': 1" in out and "retrieval_only" in out
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--sets", "nothing"]) == 1
    assert "unknown set(s) nothing for the dev split" in capsys.readouterr().err
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--sets", "s, typo"]) == 1
    assert "unknown set(s) typo" in capsys.readouterr().err               # not "s" quietly run alone
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--sets", " s , s ", "--limit", "1"]) == 0


def test_test_split_needs_final(project, capsys):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "test"]) == 1
    assert "locked" in capsys.readouterr().err
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "test", "--final"]) == 0
    assert main(["grade", "--experiment", "test_000_baseline"]) == 1
    assert main(["evaluate", "--experiment", "test_000_baseline"]) == 1
    assert main(["report", "--include-test"]) == 1
    capsys.readouterr()
    assert main(["grade", "--experiment", "test_000_baseline", "--final"]) == 0


def test_errors_are_one_line_messages(project, capsys):
    write(project / "configs" / "bad.yaml", "base: baseline.yaml\nname: bad\nsystem: nope\n")
    assert main(["run", "--config", "configs/bad.yaml", "--split", "dev"]) == 1       # a variant needs a card first
    assert "names no `card:`" in capsys.readouterr().err
    assert main(["run", "--config", "configs/bad.yaml", "--split", "dev", "--limit", "1"]) == 1   # smoke size: exempt
    err = capsys.readouterr().err
    assert err.startswith("labkit: error: unknown system 'nope'") and "Traceback" not in err
    assert main(["compare", "dev_x", "dev_y"]) == 1
    assert "labkit evaluate --experiment dev_x" in capsys.readouterr().err
    write(project / "labkit.yaml", "modules: [lk_cli_pkg.missing]\npythonpath: [src]\n")
    assert main(["report"]) == 1
    assert "cannot be imported" in capsys.readouterr().err


def test_root_option(project, tmp_path_factory, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))               # cwd outside the project
    assert main(["--root", str(project), "run", "--config", str(project / "configs" / "baseline.yaml"),
                 "--split", "dev", "--limit", "1"]) == 0
    assert "'ran': 1" in capsys.readouterr().out
    assert (project / "out" / "runs.db").exists()                         # state lands in the project, not in cwd


def test_a_relative_config_starts_in_the_root_given_with_root(project, tmp_path_factory, monkeypatch, capsys):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)
    assert main(["--root", str(project), "run", "--config", "configs/baseline.yaml", "--split", "dev", "--limit", "1"]) == 0
    assert "'ran': 1" in capsys.readouterr().out
    # a file of the same relative name in the current folder does not win over the one in the root
    write(elsewhere / "configs" / "baseline.yaml", "name: from_cwd\nsystem: no-such-system\n")
    assert main(["--root", str(project), "run", "--config", "configs/baseline.yaml", "--split", "dev", "--limit", "1"]) == 0
    assert "dev_000_baseline" in capsys.readouterr().out


def test_without_root_a_relative_config_still_starts_in_the_current_folder(project, tmp_path_factory, monkeypatch, capsys):
    """Only a --root typed on the command line moves the start: the project found from the current folder or from
    LABKIT_ROOT does not, so `configs/baseline.yaml` typed in another folder is not silently read from the project."""
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    monkeypatch.setenv("LABKIT_ROOT", str(project))
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--limit", "1"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("labkit: error: file not found:") and str(project) not in err
    assert main(["run", "--config", str(project / "configs" / "baseline.yaml"), "--split", "dev", "--limit", "1"]) == 0


def test_the_help_states_where_a_relative_path_starts(capsys):
    for argv, rule in ((["--help"], "a relative --config starts in this folder"),
                       (["run", "--help"], "a relative path starts in the folder given with --root, else in the current folder")):
        with pytest.raises(SystemExit):
            main(argv)
        assert rule in " ".join(capsys.readouterr().out.split()), argv      # argparse wraps lines to the terminal width
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "A relative --pages always starts at the project folder" in " ".join(capsys.readouterr().out.split())


def test_python_dash_m_entry_point():
    repo = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-m", "labkit", "--version"], capture_output=True, text=True, cwd=repo)
    assert done.returncode == 0 and done.stdout.startswith("labkit ")
    helped = subprocess.run([sys.executable, "-m", "labkit", "run", "--help"], capture_output=True, text=True, cwd=repo)
    assert helped.returncode == 0 and "--retrieval-only" in helped.stdout
