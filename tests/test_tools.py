"""labkit golden-check and labkit show: the two commands that read the golden set and the run log."""

import json
import unicodedata

import pytest

from labkit.cli import main
from labkit.eval.runlog import RunLog
from labkit.golden_check import check_items, load_pages, run_check
from labkit.harness import SplitLocked
from labkit.show import show

PAGES = [
    {"file": "a.pdf", "page": 3, "text": "휴면성 증권계좌는 6개월 이상\n매매가 없는 계좌를 말한다."},
    {"source": "b.pdf", "page": 1, "content": "관련 없는 내용입니다. 다른 문서의 첫 장."},
]
QUOTE = "휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"
GOOD = {"qid": "Q1", "set": "s", "split": "dev", "question": "휴면 계좌는?", "answerable": True,
        "targets": [{"file": "a.pdf", "page": 3}], "evidence": [QUOTE]}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def problems(*items):
    pages = {("a.pdf", 3): PAGES[0]["text"], ("b.pdf", 1): PAGES[1]["content"]}
    return [(p["qid"], p["problem"]) for p in check_items(list(items), pages)]


# ---------------------------------------------------------------- golden-check

def test_a_consistent_item_passes_even_when_the_quote_breaks_across_lines():
    assert problems(GOOD) == []                                   # whitespace and line breaks are ignored (squash)


def test_each_inconsistency_is_named_per_question():
    wrong_quote = {**GOOD, "qid": "Q2", "evidence": ["휴면성 증권계좌는 12개월 이상 매매가 없는 계좌"]}
    wrong_page = {**GOOD, "qid": "Q3", "targets": [{"file": "a.pdf", "page": 9}]}
    no_evidence = {**GOOD, "qid": "Q4", "evidence": []}
    too_short = {**GOOD, "qid": "Q5", "evidence": ["휴면 계좌"]}
    refusal_with_evidence = {**GOOD, "qid": "Q6", "answerable": False}
    odd = {**GOOD, "qid": "Q7", "split": "train", "answerable": "yes"}
    got = dict(problems(wrong_quote, wrong_page, no_evidence, too_short, refusal_with_evidence, odd))
    assert "evidence not found on the target pages: 휴면성 증권계좌는 12개월" in got["Q2"]
    assert got["Q3"] == "target a.pdf p.9 is not in the pages file"
    assert got["Q4"] == "answerable question has no evidence quotes"
    assert "never match" in got["Q5"]                             # no line of 8+ characters: it could not be scored either
    assert "unanswerable question lists targets or evidence" in got["Q6"]
    assert len([p for p in problems(odd) if p[0] == "Q7"]) == 2   # split and answerable are both reported
    assert "Q1" not in got


def test_any_target_page_may_hold_the_quote_and_file_names_match_across_unicode_forms():
    two = {**GOOD, "targets": [{"file": "b.pdf", "page": 1}, {"file": "a.pdf", "page": 3}]}
    assert problems(two) == []                                    # the quote is on the second target only
    pages = {(unicodedata.normalize("NFC", "금융용어집.pdf"), 1): QUOTE}
    nfd = {**GOOD, "targets": [{"file": unicodedata.normalize("NFD", "금융용어집.pdf"), "page": 1}]}
    assert check_items([nfd], pages) == []
    bad_target = {**GOOD, "targets": [{"file": "a.pdf"}, "a.pdf p.3"]}
    assert all("needs a `file` and an integer `page`" in p for _, p in problems(bad_target))


def test_pages_file_errors_name_the_line(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_pages(tmp_path / "nope.jsonl")
    write(tmp_path / "p.jsonl", jsonl(PAGES) + '{"file": "c.pdf", "page": "x", "text": "t"}\n')
    with pytest.raises(ValueError, match=r"p.jsonl:3: a page needs"):
        load_pages(tmp_path / "p.jsonl")
    write(tmp_path / "p.jsonl", jsonl([PAGES[0], {"file": "a.pdf", "page": 3, "text": "그리고 이어지는 내용"}]))
    assert "이어지는" in load_pages(tmp_path / "p.jsonl")[("a.pdf", 3)]     # a page in two pieces is joined


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    write(tmp_path / "labkit.yaml", "golden: {files: [g.jsonl]}\n")
    write(tmp_path / "golden" / "g.jsonl", jsonl([GOOD, {**GOOD, "qid": "Q2", "split": "test", "evidence": ["이 문장은 어디에도 없다 정말로"]}]))
    write(tmp_path / "corpus" / "pages.jsonl", jsonl(PAGES))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_golden_check_command(project, capsys):
    assert main(["golden-check"]) == 1
    assert "no pages file" in capsys.readouterr().err                          # nothing configured, nothing passed
    assert main(["golden-check", "--pages", "corpus/pages.jsonl", "--split", "dev"]) == 0
    assert "1 items (dev), pages from pages.jsonl: 0 with problems, 0 problems" in capsys.readouterr().out
    assert main(["golden-check", "--pages", "corpus/pages.jsonl"]) == 1        # the test item has a made-up quote
    out = capsys.readouterr().out
    assert "1 with problems, 1 problems" in out and "Q2: evidence not found" in out
    write(project / "labkit.yaml", "golden: {files: [g.jsonl], pages: corpus/pages.jsonl}\n")
    text, ok = run_check(split="dev", root=project)                            # now the default comes from labkit.yaml
    assert ok and "1 items (dev)" in text and "0 with problems" in text
    write(project / "labkit.yaml", "golden: {files: [g.jsonl], pages: 3}\n")
    with pytest.raises(ValueError, match="golden.pages"):
        run_check(root=project)


def test_golden_check_boundaries():
    """An evidence line of exactly 8 characters counts; an unanswerable item with targets is a problem even without
    evidence; the evidence quote must be on a target page."""
    assert problems({**GOOD, "evidence": ["12345678"]}) == [("Q1", "evidence not found on the target pages: 12345678")]
    assert problems({**GOOD, "answerable": False, "evidence": []})


def test_pages_are_keyed_by_nfc_names_and_a_page_in_pieces_is_joined(tmp_path):
    nfd, nfc = (unicodedata.normalize(f, "한글.pdf") for f in ("NFD", "NFC"))
    assert list(load_pages(write(tmp_path / "p.jsonl", jsonl([{"file": nfd, "page": 1, "text": "x"}])))) == [(nfc, 1)]
    two = write(tmp_path / "q.jsonl", jsonl([{"file": "a.pdf", "page": 1, "text": "first"},
                                             {"file": "a.pdf", "page": 1, "text": "second"}]))
    assert load_pages(two)[("a.pdf", 1)] == "first\nsecond"                    # joined in order, not the last one only


def test_a_boolean_is_not_a_page_number(tmp_path):
    with pytest.raises(ValueError, match="integer `page`"):
        load_pages(write(tmp_path / "p.jsonl", '{"file":"a.pdf","page":true,"text":"x"}\n'))


def pages_project(root):
    write(root / "labkit.yaml", "golden: {files: [g.jsonl], pages: corpus/wrong.jsonl}\n")
    write(root / "golden/g.jsonl", jsonl([GOOD]))
    write(root / "corpus/right.jsonl", jsonl(PAGES))
    write(root / "corpus/wrong.jsonl", jsonl([{"file": "z.pdf", "page": 1, "text": "zzz"}]))


def test_pages_on_the_command_line_beat_golden_pages_in_labkit_yaml(tmp_path):
    pages_project(tmp_path)
    assert run_check("corpus/right.jsonl", root=tmp_path)[1]


def test_a_relative_pages_path_is_resolved_against_the_root_even_from_another_folder(tmp_path, monkeypatch):
    pages_project(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    assert run_check("corpus/right.jsonl", root=tmp_path)[1]


def test_a_relative_pages_path_is_relative_to_the_project_root_not_the_current_folder(project, monkeypatch, capsys):
    """Run from a subfolder, `--pages corpus/pages.jsonl` still means <project>/corpus/pages.jsonl."""
    (project / "sub").mkdir()
    monkeypatch.chdir(project / "sub")
    assert main(["golden-check", "--pages", "corpus/pages.jsonl", "--split", "dev"]) == 0
    assert "pages from pages.jsonl" in capsys.readouterr().out
    assert main(["golden-check", "--pages", "pages.jsonl", "--split", "dev"]) == 1       # not looked for next to the cwd
    err = capsys.readouterr().err
    assert "does not exist" in err and str(project / "pages.jsonl") in err                # the error shows the root path
    assert "relative to the project root" in main_help("golden-check")


def main_help(command):
    from labkit.cli import build_parser
    return " ".join(build_parser()._subparsers._group_actions[0].choices[command].format_help().split())


def test_golden_check_reports_a_bad_split_per_question_instead_of_refusing_to_load(project, capsys):
    """Every other command refuses a golden file with a bad split; golden-check is the one that says which items."""
    write(project / "golden" / "g.jsonl", jsonl([GOOD, {**GOOD, "qid": "Q2", "split": "Test"}, {**GOOD, "qid": "Q3", "split": None}]))
    assert main(["golden-check", "--pages", "corpus/pages.jsonl"]) == 1
    out = capsys.readouterr().out
    assert "Q2: split must be dev or test (got 'Test')" in out and "Q3: split must be dev or test (got None)" in out


def test_the_proxy_snippet_in_step_4_runs_as_written(project, capsys):
    """Run the code block of docs/steps/04-proxy.md itself, so the documented example cannot rot."""
    import re
    import textwrap
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "labkit" / "template" / "docs" / "steps" / "04-proxy.md").read_text(encoding="utf-8")
    code = textwrap.dedent(re.search(r"```python\n(.*?)```", text, re.S).group(1))   # the block sits inside a list item
    items = [{**GOOD, "qid": f"Q{i}"} for i in (1, 2, 3)] + [{**GOOD, "qid": "N1", "answerable": False, "targets": [], "evidence": []}]
    write(project / "golden" / "g.jsonl", jsonl(items))
    folder = project / "results" / "dev_000_baseline"
    write(folder / "grades.jsonl", jsonl([{"qid": q, "correctness": s} for q, s in (("Q1", 0), ("Q2", 1), ("Q3", 2), ("N1", 2))]))
    write(folder / "metrics.jsonl", jsonl([{"qid": q, "gold@5": g, "page_hit@5": 1, "evid@5": e, "mrr": m}
                                           for q, g, e, m in (("Q1", 0, 0.0, 0.2), ("Q2", 1, 0.5, 0.5), ("Q3", 2, 1.0, 1.0), ("N1", 0, None, 0.0))]))
    exec(compile(code, "04-proxy.md", "exec"), {"__name__": "snippet"})
    printed = eval(capsys.readouterr().out)
    assert printed["gold@5"] == 1.0 and printed["page_hit@5"] is None        # constant on every question: no correlation
    assert set(printed) == {"page_hit@5", "gold@5", "evid@5", "mrr"}          # answer-side columns are not offered


def test_the_direction_agreement_snippet_in_step_4_runs_as_written(project, capsys):
    """The second code block of docs/steps/04-proxy.md (item 3), run as written against a baseline and a variant."""
    import re
    import textwrap
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "labkit" / "template" / "docs" / "steps" / "04-proxy.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    assert len(blocks) == 2
    code = textwrap.dedent(blocks[1])
    rows = {"dev_000_baseline": (("Q1", 0, 1), ("Q2", 1, 1), ("Q3", 2, 0)),
            "dev_NNN_slug": (("Q1", 2, 2), ("Q2", 0, 0), ("Q3", 2, 0))}       # Q1 both up, Q2 both down, Q3 unchanged
    for experiment, values in rows.items():
        folder = project / "results" / experiment
        write(folder / "grades.jsonl", jsonl([{"qid": q, "correctness": s} for q, s, _ in values]))
        write(folder / "metrics.jsonl", jsonl([{"qid": q, "gold@5": g} for q, _, g in values]))
    exec(compile(code, "04-proxy.md", "exec"), {"__name__": "snippet"})
    printed = eval(capsys.readouterr().out)
    assert printed == {"score_changed": 2, "same_direction": 2, "opposite_direction": 0, "metric_unchanged": 0}


# ---------------------------------------------------------------- show

def log_rows(root):
    log = RunLog(root / "out" / "runs.db")
    config = {"selection": {"split": "dev", "sets": None}}
    log.save({"status": "answered", "question": "휴면 계좌는?", "answer": "6개월 [1]", "config": {**config, "smoke": True},
              "context": [{"chunk_id": "c1", "source": "a.pdf", "page": 3, "content": QUOTE}],
              "citations": [{"chunk_id": "c1", "source": "a.pdf", "page": 3}], "searches": [{"query": "휴면", "chunk_ids": ["c1"]}],
              "steps": {"calls": [{"model": "m"}], "index_key": "k"}, "latency_s": 1.5, "n_llm_calls": 1, "n_searches": 1},
             experiment="dev_x", qid="Q1")
    log.save({"status": "error", "question": "오늘 금리는?", "error": "RuntimeError: boom", "config": config},
             experiment="dev_x", qid="Q2")
    log.save({"status": "answered", "question": "휴면 계좌는?", "answer": "6개월 이상 [1]", "config": config},
             experiment="dev_x", qid="Q1")
    return log


def test_show_prints_the_latest_row_per_question_with_errors_and_smoke_marked(project):
    log_rows(project)
    text = show("dev_x", root=project)
    assert text.count("== Q1") == 1 and "6개월 이상 [1]" in text and "6개월 [1]\n" not in text   # the newer Q1 row
    assert "== Q2" in text and "status=error" in text and "error:     RuntimeError: boom" in text
    everything = show("dev_x", all_rows=True, root=project)
    assert everything.count("== Q1") == 2 and "SMOKE (belongs to no result)" in everything
    assert "context (1 units given to the answer step)" in everything and "a.pdf p.3 (c1)" in everything
    assert "search 1:  휴면 -> c1" in everything and "steps:     1 model calls; also index_key" in everything


def test_show_filters_cuts_and_dumps_json(project):
    log_rows(project)
    only = show("dev_x", qids=["Q2"], root=project)
    assert "== Q1" not in only and "== Q2" in only
    with pytest.raises(ValueError, match="no logged run for Q9"):
        show("dev_x", qids=["Q9"], root=project)
    with pytest.raises(ValueError, match="no runs logged for dev_nope; logged experiments: dev_x"):
        show("dev_nope", root=project)
    short = show("dev_x", qids=["Q1"], chars=4, all_rows=True, root=project)
    assert "… (+" in short
    rows = [json.loads(line) for line in show("dev_x", as_json=True, all_rows=True, root=project).splitlines()]
    assert [r["qid"] for r in rows] == ["Q1", "Q2", "Q1"] and rows[0]["config"]["smoke"] is True


def test_show_adds_the_grade_of_that_exact_run_and_guards_the_test_split(project, capsys):
    log = log_rows(project)
    latest = {r["qid"]: r for r in log.load("dev_x")}["Q1"]
    write(project / "out" / "grades" / "dev_x.jsonl",
          jsonl([{"qid": "Q1", "run_id": latest["run_id"], "correctness": 2, "judge_model": "j", "reason": "정확함"}]))
    text = show("dev_x", root=project)
    assert "grade:     2 (j): 정확함" in text and text.count("grade:") == 1
    RunLog(project / "out" / "runs.db").save(
        {"status": "answered", "question": "q", "answer": "a", "config": {"selection": {"split": "test", "sets": None}}},
        experiment="final_try", qid="Q2")
    with pytest.raises(SplitLocked):                                           # reading a test answer is looking at test
        show("final_try", root=project)
    assert "== Q2" in show("final_try", final=True, root=project)
    assert main(["show", "dev_x", "--qid", "Q1"]) == 0 and "== Q1" in capsys.readouterr().out
    assert main(["show", "final_try"]) == 1 and "locked" in capsys.readouterr().err
