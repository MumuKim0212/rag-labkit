"""labkit.eval: run log, metrics, paired comparison, headroom. No LLM, no network."""

import json

import pytest

from labkit.eval import load_jsonl, parse_where, to_map
from labkit.eval.headroom import headroom, passes
from labkit.eval.metrics import context_metrics, direction_agreement, proxy_check, ranked_metrics
from labkit.eval.paired import compare
from labkit.eval.runlog import RunLog

GOLD = {"chunk_id": "c1", "source": "a.pdf", "page": 3, "content": "휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"}
OTHER = {"chunk_id": "c2", "source": "b.pdf", "page": 1, "content": "관련 없는 내용"}
ITEM = {"qid": "Q1", "targets": [{"file": "a.pdf", "page": 3}], "evidence": ["휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"]}


def test_runlog_save_latest_and_resume(tmp_path):
    log = RunLog(tmp_path / "runs.db")
    log.save({"status": "error", "error": "boom"}, experiment="dev_x", qid="Q1")
    assert log.done_qids("dev_x") == set()                       # a failed run does not count as done
    log.save({"status": "answered", "context": [GOLD], "n_searches": 1}, experiment="dev_x", qid="Q1")
    assert log.done_qids("dev_x") == {"Q1"}
    (run,) = log.latest("dev_x")
    assert run["status"] == "answered" and run["context"] == [GOLD]
    assert log.experiments() == [("dev_x", 2)]


def test_ranked_and_context_metrics():
    ranked = ranked_metrics(ITEM, [OTHER, GOLD])
    assert ranked["first_rank"] == 2 and ranked["mrr"] == 0.5
    assert ranked["page_hit@1"] == 0 and ranked["page_hit@3"] == 1 and ranked["gold@3"] == 1
    ctx = context_metrics(ITEM, [OTHER, GOLD])
    assert ctx["page_hit"] == 1 and ctx["evid_full"] == 1 and ctx["units"] == 2
    assert context_metrics(ITEM, [OTHER])["page_hit"] == 0


def test_file_names_match_across_unicode_forms():
    import unicodedata
    nfd = {**GOLD, "source": unicodedata.normalize("NFD", "금융용어집.pdf")}
    item = {**ITEM, "targets": [{"file": unicodedata.normalize("NFC", "금융용어집.pdf"), "page": 3}]}
    assert context_metrics(item, [nfd])["page_hit"] == 1


def test_citation_check():
    from labkit.eval.metrics import citation_check
    run = {"context": [GOLD, OTHER], "citations": [{"chunk_id": "c1", "source": "a.pdf", "page": 3}]}
    assert citation_check(ITEM, run) == {"n_citations": 1, "in_context": 1, "on_target": 1}
    off = {"context": [GOLD], "citations": [{"chunk_id": "zz", "source": "b.pdf", "page": 1}]}
    assert citation_check(ITEM, off) == {"n_citations": 1, "in_context": 0, "on_target": 0}
    spanning = {"context": [OTHER], "citations": [{"chunk_id": "c2", "source": "a.pdf", "page": 2, "pages": [2, 3]}]}
    assert citation_check(ITEM, spanning)["on_target"] == 1               # a unit running over a page break
    none = citation_check({"qid": "N"}, {"citations": []})                # unanswerable item, nothing cited
    assert none == {"n_citations": 0, "in_context": None, "on_target": None}


def test_proxy_check_and_direction_agreement():
    scores = {"a": 0, "b": 1, "c": 2}
    assert proxy_check(scores, {"m": {"a": 0, "b": 1, "c": 2}})["m"] == 1.0
    assert proxy_check(scores, {"flat": {"a": 1, "b": 1, "c": 1}})["flat"] is None
    # a: metric up, score up (same). b: metric down, score down (same). c: metric up, score down (opposite).
    agree = direction_agreement({"a": 0, "b": 1, "c": 0}, {"a": 1, "b": 0, "c": 1},
                                {"a": 0, "b": 2, "c": 2}, {"a": 2, "b": 1, "c": 1})
    assert (agree["same_direction"], agree["opposite_direction"]) == (2, 1)


def test_paired_compare():
    r = compare({"a": 0, "b": 1, "c": 2}, {"a": 2, "b": 1, "c": 2}, max_score=2)
    assert r["b_better"] == ["a"] and r["a_better"] == [] and r["same"] == 2
    assert r["diff"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        compare({"a": 1}, {"z": 1})


def test_headroom_gate():
    scores = {"a": 0, "b": 2, "c": 1}
    r = headroom(scores, before={"a": 0, "b": 0, "c": 1}, after={"a": 1, "b": 1, "c": 0}, max_score=2)
    assert r["upside"] == ["a"] and r["spent"] == ["b"] and r["risk"] == ["c"]
    assert not passes(r, noise=0)                                # upside 1 - risk 1 = 0, not larger than noise
    assert passes(headroom(scores, {"a": 0}, {"a": 1}, 2), noise=0)


def test_runlog_export_of_a_test_experiment_needs_final(tmp_path, monkeypatch, capsys):
    """The module's own command printed test answers without the --final every labkit command asks for."""
    from labkit.eval import runlog
    log = RunLog(tmp_path / "runs.db")
    log.save({"status": "answered", "answer": "secret", "config": {"selection": {"split": "test"}}},
             experiment="final_try", qid="T1")                                  # a test run under a neutral name
    log.save({"status": "answered", "answer": "plain", "config": {"selection": {"split": "dev"}}},
             experiment="dev_x", qid="D1")
    out = tmp_path / "out.jsonl"
    monkeypatch.setattr("sys.argv", ["runlog", "export", str(tmp_path / "runs.db"), "final_try", str(out)])
    with pytest.raises(SystemExit) as stopped:
        runlog.main()
    assert stopped.value.code == 1 and not out.exists() and "needs --final" in capsys.readouterr().err
    monkeypatch.setattr("sys.argv", ["runlog", "export", str(tmp_path / "runs.db"), "final_try", str(out), "--final"])
    runlog.main()
    assert "secret" in out.read_text(encoding="utf-8")
    dev = tmp_path / "dev.jsonl"
    monkeypatch.setattr("sys.argv", ["runlog", "export", str(tmp_path / "runs.db"), "dev_x", str(dev)])
    runlog.main()                                                                 # dev needs no flag
    assert "plain" in dev.read_text(encoding="utf-8")


def test_runlog_export_creates_the_folder_it_writes_to(tmp_path, monkeypatch):
    from labkit.eval import runlog
    RunLog(tmp_path / "runs.db").save({"status": "answered"}, experiment="dev_x", qid="Q1")
    target = tmp_path / "scratch" / "deep" / "runs.jsonl"                     # a new project has no scratch/ yet
    monkeypatch.setattr("sys.argv", ["runlog", "export", str(tmp_path / "runs.db"), "dev_x", str(target)])
    runlog.main()
    assert json.loads(target.read_text(encoding="utf-8").splitlines()[0])["qid"] == "Q1"


def test_headroom_refuses_scores_above_the_stated_maximum():
    with pytest.raises(ValueError, match=r"1 baseline scores are above max_score=1 .*--max-score"):
        headroom({"a": 2, "b": 1}, {"a": 0, "b": 0}, {"a": 1, "b": 1}, max_score=1)   # a 0/1/2 judge read as 0/1


def test_headroom_command_reads_max_score_from_the_project(tmp_path, monkeypatch, capsys):
    """The old default of 1.0 counted a score of 1 as full marks on a 0/1/2 judge: no upside, 'skip the answer run'."""
    from labkit.eval import headroom as module
    (tmp_path / "labkit.yaml").write_text("max_score: 2\n", encoding="utf-8")
    rows = lambda name, data: (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in data), encoding="utf-8")
    rows("grades.jsonl", [{"qid": "a", "correctness": 1}, {"qid": "b", "correctness": 2}])
    rows("before.jsonl", [{"qid": "a", "gold@5": 0}, {"qid": "b", "gold@5": 0}])
    rows("after.jsonl", [{"qid": "a", "gold@5": 1}, {"qid": "b", "gold@5": 1}])
    monkeypatch.chdir(tmp_path)
    args = ["headroom", "--scores", "grades.jsonl", "--before", "before.jsonl", "--after", "after.jsonl",
            "--metric", "gold@5", "--noise", "0"]
    monkeypatch.setattr("sys.argv", args)
    module.main()
    out = capsys.readouterr().out
    assert "upside 1" in out and "spent  1" in out and "run the answer evaluation" in out   # a=1 is partial credit
    monkeypatch.setattr("sys.argv", args + ["--max-score", "1"])
    with pytest.raises(SystemExit, match="above max_score=1"):
        module.main()


def test_jsonl_helpers(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in [
        {"qid": "a", "s": 1, "config": "x"}, {"qid": "a", "s": 2, "config": "x"}, {"qid": "b", "s": 3, "config": "y"}]),
        encoding="utf-8")
    assert to_map(load_jsonl(path), "s") == {"a": 2, "b": 3}     # later rows win (re-grades)
    assert to_map(load_jsonl(path, parse_where(["config=x"])), "s") == {"a": 2}


# ---------------------------------------------------------------- N2: few paired questions

def test_a_comparison_of_fewer_than_ten_questions_starts_with_a_warning():
    from labkit.eval.paired import SMALL_N, compare, report
    few = {f"Q{i}": 1 for i in range(SMALL_N - 1)}
    text = report(compare(few, {**few, "Q0": 0}))
    assert text.startswith(f"WARNING: only {SMALL_N - 1} questions are compared")
    enough = {f"Q{i}": 1 for i in range(SMALL_N)}
    assert "WARNING" not in report(compare(enough, {**enough, "Q0": 0}))
