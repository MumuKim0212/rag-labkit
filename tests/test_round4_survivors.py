"""Tests for the mutants the fourth mutation review found alive (the fifth review round).

A reviewer planted 190 one-line bugs in labkit (at commit 5e64961, v0.1.2); 53 distinct ones survived the suite, four
of them equivalent. These tests were written by the reviewer against that commit, each checked to pass on the real code
and to fail on its own mutant; they were run again on the code as it is now (the test name carries the mutant numbers
of that round, which are not the numbers of the third round). They pin what earlier tests exercised on one side only:
what a message or a report cell says and not just that it appears, which of several entries is meant (the first, the
earliest, the one in sorted order), a second spelling of a path or a pipe/angle bracket inside a value, and the exact
amount in a formula.
"""

import json
import os
import sqlite3

import pytest

from labkit import cards, freeze, steps
from labkit.compare import compare_experiments
from labkit.config import load_config, same_model
from labkit.cost import call_usd
from labkit.eval.runlog import RunLog
from labkit.golden import load_golden
from labkit.golden_check import load_pages
from labkit.harness import _card_check, build_report, grade_experiment, run_experiment
from labkit.project import load_project
from labkit.scaffold import init_project, new_card
from test_cli import CARD, CARD_FILE, card_use
from test_freeze import baseline, make_dir_link
from test_freeze import proj as fproj  # noqa: F401
from test_freeze import write as fwrite
from test_harness import BASELINE, GOLDEN, FakeSystem, fake_judge, graded_and_evaluated, project, write  # noqa: F401
from test_steps import proj as sproj  # noqa: F401
from test_survivors import repo  # noqa: F401


def _cfg(project): return load_config(project / "configs" / "baseline.yaml")
def _summary(name, **x): return json.dumps({"experiment": name, "split": "dev", "n_questions": 2, "complete": True, "changed": {}, **x})
def _report_lines(project):
    build_report()
    lines = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines()
    return next(l for l in lines if l.startswith("| Experiment")), {l.split("|")[1].strip(): l for l in lines if l.startswith("| dev_")}


def test_m8(project):                      # config message names the kind of value
    write(project/"configs"/"baseline.yaml", BASELINE + "corpus_date: 2026-01-01\n")
    with pytest.raises(ValueError, match=r"`corpus_date` is datetime.date\(2026, 1, 1\), a date\."):
        load_config(project/"configs"/"baseline.yaml")

def test_m17():
    assert not same_model("None", None) and not same_model("none", None)

@pytest.mark.parametrize("error", [OSError("denied"), ValueError("embedded null byte")])
def test_m29_m30(tmp_path, monkeypatch, error):   # (m30 only distinguishable on Windows)
    def refuse(self, strict=False): raise error
    monkeypatch.setattr(type(tmp_path), "resolve", refuse)
    assert freeze._identity(tmp_path, "Prompts/X.md") == os.path.normcase("Prompts/X.md")

def test_m31(fproj):
    make_dir_link(fproj/"alias", fproj/"prompts")
    now = freeze.file_hash(fproj/"prompts"/"answer_v1.md")
    fwrite(fproj/"results"/"dev_a"/"summary.json", json.dumps({"file_hashes": {"prompts/answer_v1.md": "0000000000"}}))
    fwrite(fproj/"results"/"dev_b"/"summary.json", json.dumps({"file_hashes": {"alias/answer_v1.md": now}}))
    cfg = baseline(fproj); hashes = freeze.file_hashes(cfg, load_project(fproj), fproj)
    with pytest.raises(ValueError, match="evaluated experiment dev_a"):
        freeze.check_run(cfg, hashes, "dev_c", [], fproj)

def test_m32(fproj):
    now = freeze.file_hash(fproj/"prompts"/"answer_v1.md")
    fwrite(fproj/"results"/"dev_a"/"summary.json", json.dumps({"file_hashes": {"prompts/answer_v1.md": now}}))
    fwrite(fproj/"results"/"dev_b"/"summary.json", json.dumps({"file_hashes": {"prompts/answer_v1.md": "0000000000"}}))
    cfg = baseline(fproj); hashes = freeze.file_hashes(cfg, load_project(fproj), fproj)
    with pytest.raises(ValueError, match="evaluated experiment dev_b"):
        freeze.check_run(cfg, hashes, "dev_c", [], fproj)

def test_m37(fproj):
    cfg = baseline(fproj); cfg["answer"] = {"prompt": f"../{fproj.name}/prompts/answer_v1.md"}
    files = freeze.referenced_files(cfg, load_project(fproj), fproj)
    assert "prompts/answer_v1.md" in files and not [f for f in files if f.startswith("..")]

def test_m52(project):
    write(project/"golden"/"provided.jsonl", json.dumps({**GOLDEN[0], "qid": "a\u2029b"}) + "\n")
    with pytest.raises(ValueError, match=r"has an invisible character \(U\+2029\)"): load_golden()

def test_m54(project):
    write(project/"golden"/"provided.jsonl", json.dumps({**GOLDEN[0], "qid": "Q 1"}) + "\n")
    assert [i["qid"] for i in load_golden()] == ["Q 1"]

def test_m79(sproj):
    p = steps.step_file(sproj, 3)
    p.write_text(p.read_text(encoding="utf-8").replace("## Exit criteria\n", "## Exit criteria\n\n- [ ] x <!-- auto: no-such-check -->\n", 1), encoding="utf-8")
    with pytest.raises(KeyError, match="no-such-check"): steps.criteria(sproj, 3)

def test_m117_m375(project):
    run_experiment(_cfg(project), "dev", experiment="dev_x", system=FakeSystem())
    with pytest.raises(ValueError, match=r"share the grades file of dev_x with the suffix __regrade1:"):
        run_experiment(_cfg(project), "dev", experiment="dev_x__regrade1", system=FakeSystem())

def test_m126(project):
    class Blank(FakeSystem):
        def run(self, q, s): out = super().run(q, s); out["status"] = "   "; return out
    assert run_experiment(_cfg(project), "dev", experiment="dev_x", system=Blank())["statuses"] == {"error": 2}

def test_m129(project):
    class Odd(FakeSystem):
        def run(self, q, s): return {"answer": "x", "steps": ["not", "a", "mapping"]}
    run_experiment(_cfg(project), "dev", experiment="dev_x", system=Odd())
    rows = RunLog(project/"out"/"runs.db").load("dev_x")
    assert rows and all(r["status"] == "error" and r["steps"] is None for r in rows)

def test_m131(project):
    class Nothing(FakeSystem):
        def run(self, q, s): return None
    run_experiment(_cfg(project), "dev", experiment="dev_x", system=Nothing())
    assert all("the system returned NoneType with no `status`" in r["error"] for r in RunLog(project/"out"/"runs.db").load("dev_x"))

def test_m135(project):
    run_experiment(_cfg(project), "dev", experiment="dev_other", system=FakeSystem())
    with pytest.raises(ValueError, match="no runs logged for dev_typo; logged experiments: dev_other"):
        grade_experiment("dev_typo")

def _golden_sets(project, dev_sets, test_sets=()):
    items = [{**GOLDEN[0], "qid": f"D{n}", "set": n, "split": "dev"} for n in dev_sets]
    items += [{**GOLDEN[0], "qid": f"T{n}", "set": n, "split": "test"} for n in test_sets]
    write(project/"golden"/"provided.jsonl", "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    write(project/"configs"/"baseline.yaml", "name: 000_baseline\nsystem: fake\njudge: {name: fake-judge, model: j, prompt: prompts/judge.md}\n")

def test_m140_m144(project):
    _golden_sets(project, [f"s{i}" for i in range(1, 9)])
    run_experiment(_cfg(project), "dev", experiment="dev_x", sets=["s7", "s2", "s5", "s1", "s8", "s3"], system=FakeSystem())
    assert graded_and_evaluated("dev_x")["sets"] == ["s1", "s2", "s3", "s5", "s7", "s8"]

def test_m385(project):
    _golden_sets(project, ["s1", "s2"], test_sets=["s3"])
    run_experiment(_cfg(project), "dev", experiment="dev_x", sets=["s1", "s2"], system=FakeSystem())
    assert graded_and_evaluated("dev_x")["sets"] is None

def test_m145_m146(project):
    run_experiment(_cfg(project), "dev", experiment="dev_000_baseline", system=FakeSystem())
    s = graded_and_evaluated("dev_000_baseline")
    assert s["config_hash"] == _cfg(project)["config_hash"] and s["config"]["system"] == "fake"
    assert not {"changed", "config_hash", "config_file", "name"} & set(s["config"])

def test_m153_m155(project):
    write(project/"results"/"dev_odd"/"summary.json", _summary("dev_odd", index_key="k|1", compare_to="dev_a|b"))
    header, rows = _report_lines(project); row = rows["dev_odd"]
    assert row.replace("\\|", "").count("|") == header.count("|") and "k\\|1" in row and "dev_a\\|b" in row

def test_m156_m158_m159_m163_m378(project):
    write(project/"results"/"dev_base"/"summary.json", _summary("dev_base", sets=None, project_settings={"golden_files": ["g"], "max_score": 2}))
    write(project/"results"/"dev_odd"/"summary.json", _summary("dev_odd", compare_to="dev_base", sets=["a", "b|c"],
          project_settings={"golden_files": ["h"], "max_score": 4}))
    header, rows = _report_lines(project); row = rows["dev_odd"]
    assert row.replace("\\|", "").count("|") == header.count("|")
    assert ("different labkit.yaml settings (golden_files, max_score); different sets (a, b\\|c vs the whole split): "
            "no difference shown") in row

def test_m167_m168_m169(project):
    write(project/"results"/"base"/"summary.json", _summary("dev_&base", sets=None))
    write(project/"results"/"odd"/"summary.json", _summary("dev_odd", compare_to="dev_&base", sets=["a<b"]))
    build_report()
    html = (project/"out"/"report.html").read_text(encoding="utf-8")
    assert "(vs dev_&amp;base)" in html and "different sets (a&lt;b vs the whole split)" in html

def test_m380_m381(project):
    write(project/"results"/"dev_base1"/"summary.json", _summary("dev_base1", sets=["a"]))
    write(project/"results"/"dev_old1"/"summary.json", _summary("dev_old1", compare_to="dev_base1"))
    write(project/"results"/"dev_base2"/"summary.json", _summary("dev_base2"))
    write(project/"results"/"dev_new2"/"summary.json", _summary("dev_new2", compare_to="dev_base2", sets=["a"]))
    _, rows = _report_lines(project)
    assert "different sets (the whole split vs a)" in rows["dev_old1"] and "different sets (a vs the whole split)" in rows["dev_new2"]

def test_m382_m383(project):
    write(project/"results"/"dev_c"/"summary.json", _summary("dev_c", cost={"usd": 1.0, "total_usd": 2.5}))
    assert "| 2.5000 |" in _report_lines(project)[1]["dev_c"]

def test_m331_m332(tmp_path):
    init_project(tmp_path/"p"); made = new_card(tmp_path/"p", "some-slug")
    assert "# 001: some slug" in (tmp_path/"p"/made["card"]).read_text(encoding="utf-8").splitlines()
    assert "description: some slug" in (tmp_path/"p"/made["config"]).read_text(encoding="utf-8").splitlines()
    made = new_card(tmp_path/"p", "other", title="A   B\tC\r\nD")
    assert "# 002: A B C D" in (tmp_path/"p"/made["card"]).read_text(encoding="utf-8").splitlines()

def test_m336(tmp_path):
    init_project(tmp_path/"p"); made = new_card(tmp_path/"p", "x", title="Hybrid: a [b]")
    lines = (tmp_path/"p"/made["config"]).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("base: baseline.yaml ") and "# change to the last adopted config" in lines[0]
    assert lines[3] == "description: 'Hybrid: a [b]'" and lines[4].startswith("# compare_to")

def test_m370():
    assert call_usd({"model": "e", "input_tokens": 1_000_000}, {"e": {"input": 0.02}}) == pytest.approx(0.02)

def test_m221(tmp_path):
    card_use("2026-01-01T10:00:00+00:00", "dev_new", "NEW", tmp_path)
    write(tmp_path/"results"/"dev_old"/"summary.json", json.dumps({"experiment": "dev_old",
          "card_check": {"file": CARD_FILE, "first_run_at": "2026-01-01T08:00:00+00:00"}}))
    own = {"file": CARD_FILE, "sections_hash": "NEW", "at": "2026-01-01T10:00:00+00:00"}
    c = _card_check(own, RunLog(tmp_path/"runs.db"), tmp_path/"results", tmp_path)
    assert c["first_run_experiment"] == "dev_new" and c["hash_at_first_run"] == "NEW"

def _grades(root, a, b):
    for name, scale in (("a", a), ("b", b)):
        s = {} if scale is None else {"max_score": scale}
        if name == "b": s.update(complete=False, incomplete=["x"])
        write(root/f"results/{name}/summary.json", json.dumps(s))
        write(root/f"results/{name}/grades.jsonl", "".join(json.dumps({"qid": q, "correctness": 1}) + "\n" for q in ("q1", "q2")))

def test_m245(tmp_path):
    _grades(tmp_path, 2, None); assert "a 0.500  b 0.500" in compare_experiments("a", "b", root=tmp_path)

def test_m253(tmp_path):
    _grades(tmp_path, 2, 2)
    assert compare_experiments("a", "b", root=tmp_path).splitlines()[0].startswith("WARNING: b is incomplete")

def test_m260(tmp_path):
    write(tmp_path/"real"/"experiments"/"004-x.md", CARD); link = tmp_path/"link"; make_dir_link(link, tmp_path/"real")
    cfg = {"changed": {"k": {"from": 1, "to": 2}}, "card": "experiments/004-x.md", "config_file": "configs/004-x.yaml"}
    assert cards.gate(cfg, link)["file"] == "experiments/004-x.md"

def test_m271():
    a = ("2026-01-01T10:00:00+00:00", "dev_a", "h1"); b = ("2026-01-01T10:00:00+00:00", "dev_b", "h2")
    assert cards.earliest([a, b])[:2] == ("dev_a", "h1")

def test_m281(tmp_path):
    from labkit.repro import TRACKED, code_folders
    write(tmp_path/"labkit.yaml", "pythonpath: ['../elsewhere']\n")
    assert code_folders(tmp_path) == list(TRACKED)

@pytest.mark.needs_git
def test_m289(repo, monkeypatch):
    from labkit.repro import git_info
    monkeypatch.setattr("labkit.repro.MAX_HASHED_BYTES", 8)
    (repo/"src"/"blob.bin").write_bytes(b"a" * 8); first = git_info(repo)["changes"]
    (repo/"src"/"blob.bin").write_bytes(b"b" * 8)
    assert first and git_info(repo)["changes"] != first

def test_m305(tmp_path):
    log = RunLog(tmp_path/"runs.db")
    log.save({"status": "answered"}, experiment="dev_z", qid="Q1"); log.save({"status": "answered"}, experiment="dev_a", qid="Q1")
    with sqlite3.connect(tmp_path/"runs.db") as c:
        c.execute("UPDATE runs SET created_at='2026-01-01T00:00:00' WHERE experiment='dev_z'")
        c.execute("UPDATE runs SET created_at='2026-01-02T00:00:00' WHERE experiment='dev_a'")
    assert [n for n, _ in log.experiments()] == ["dev_z", "dev_a"]

def test_m316(tmp_path):
    write(tmp_path/"pages.jsonl", '{"file": "a.pdf", "page": 1, "text": "x"}\n\n   \n{"file": "a.pdf", "page": 2, "text": "y"}\n')
    assert set(load_pages(tmp_path/"pages.jsonl")) == {("a.pdf", 1), ("a.pdf", 2)}

def test_m368(fproj, tmp_path_factory, monkeypatch):      # cwd differs from the project root
    make_dir_link(fproj/"alias", fproj/"prompts")
    fwrite(fproj/"results"/"dev_a"/"summary.json", json.dumps({"file_hashes": {"prompts/answer_v1.md": "0000000000"}}))
    cfg = baseline(fproj); monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    with pytest.raises(ValueError, match="evaluated experiment dev_a"):
        freeze.check_run(cfg, {"alias/answer_v1.md": freeze.file_hash(fproj/"prompts"/"answer_v1.md")}, "dev_c", [], fproj)
