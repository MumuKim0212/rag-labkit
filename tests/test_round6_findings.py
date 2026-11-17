"""Tests for what the sixth review round found (labkit at 7536df2, v0.1.3; the sixth review round).

Two sub-agents reviewed v0.1.3, one for code and behavior and one for the documents. The test names carry the number
the finding has in that round's list (f01 ... f10). Each test fails on the code as it was at 7536df2.
f11 is the last of the three items that list had only suspected (file_hash reading a whole file); it was measured and
fixed after v0.1.4.
"""

import hashlib
import json
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from labkit import freeze, scaffold, steps
from labkit.autochecks import AUTOCHECKS
from labkit.cli import main
from labkit.config import load_config
from labkit.eval.runlog import RunLog
from labkit.harness import SplitLocked, build_report, experiment_split, grade_experiment, guard_test, run_experiment
from labkit.golden import load_golden
from labkit.judges import JUDGES
from labkit.project import load_project
from test_cli import project as cli_project  # noqa: F401
from test_steps import proj as sproj  # noqa: F401
from test_harness import BASELINE, FakeSystem, GOLDEN, fake_judge, graded_and_evaluated, project, write  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
# A child process whose default text encoding is not UTF-8 on every platform: the Windows ANSI code page (cp949 on a
# Korean machine) with UTF-8 mode off, and ASCII under the C locale on Linux.
NOT_UTF8 = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "", "LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"}


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True)


# ---------------------------------------------------------------- f01: git text that is not ASCII

@pytest.mark.needs_git
def test_f01_a_non_ascii_git_user_name_is_read_when_the_code_page_is_not_utf8(tmp_path):
    root = tmp_path / "p"
    scaffold.init_project(root, git=True)
    subprocess.run(["git", "config", "user.name", "홍길동"], cwd=root, check=True)
    done = subprocess.run([sys.executable, "-m", "labkit", "--root", str(root), "tick", "1", "1", "--evidence", "x"],
                          cwd=REPO, env=NOT_UTF8, capture_output=True)
    text = (done.stdout + done.stderr).decode("utf-8", "replace")
    assert done.returncode == 0 and "claimed by 홍길동" in text, text      # not "UnicodeDecodeError" and a raw traceback


@pytest.mark.needs_git
def test_f01_a_non_ascii_branch_and_file_name_are_read_when_the_code_page_is_not_utf8(tmp_path):
    root = tmp_path / "p"
    scaffold.init_project(root, git=True)
    git(root, "commit", "--allow-empty", "-m", "init")
    git(root, "checkout", "-b", "실험/기준선")
    git(root, "config", "core.quotepath", "false")                          # file names are then printed as they are
    (root / "prompts" / "한글.md").write_text("x\n", encoding="utf-8")
    code = ("import sys; sys.stdout.reconfigure(encoding='utf-8'); from labkit.repro import git_info; "
            "info = git_info(sys.argv[1]); print(info['branch'], info['dirty'])")
    done = subprocess.run([sys.executable, "-c", code, str(root)], cwd=REPO, env=NOT_UTF8, capture_output=True)
    text = (done.stdout + done.stderr).decode("utf-8", "replace")
    assert done.returncode == 0 and text.strip() == "실험/기준선 True", text


# ---------------------------------------------------------------- f02: report differences across splits and kinds

def _summary(name, **fields):
    return json.dumps({"experiment": name, "split": "dev", "n_questions": 2, "complete": True, "changed": {}, **fields})


def _report_rows(project, **kwargs):
    build_report(**kwargs)
    lines = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines()
    header = [c.strip() for c in next(l for l in lines if l.startswith("| Experiment")).strip("|").split("|")]
    rows = {}
    for line in lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(header) and cells[0] not in ("Experiment", "---"):
            rows[cells[0]] = dict(zip(header, cells))
    return rows


def test_f02_a_test_result_that_names_a_dev_baseline_shows_no_difference(project):
    write(project / "results" / "dev_base" / "summary.json", _summary("dev_base", scores={"correctness_mean": 1.0}))
    write(project / "results" / "test_x" / "summary.json",
          _summary("test_x", split="test", compare_to="dev_base", scores={"correctness_mean": 0.5}))
    row = _report_rows(project, include_test=True, final=True)["test_x"]
    assert row["Score"] == "0.500"                               # not "0.500 (-0.500)": other questions, other split
    assert "different split (test vs dev)" in row["Compared to"] and "no difference shown" in row["Compared to"]


def test_f02_a_retrieval_only_result_shows_a_difference_only_in_the_metrics_columns(project):
    answer = {"cost": {"usd_per_question": 0.0015, "tokens_per_question": 1200}, "calls": {"llm_per_question": 1.0},
              "metrics": {"gold@5": 0.5}}
    write(project / "results" / "dev_base" / "summary.json", _summary("dev_base", **answer))
    write(project / "results" / "dev_ret" / "summary.json", _summary(
        "dev_ret", retrieval_only=True, compare_to="dev_base", cost={"usd_per_question": 0.0, "tokens_per_question": 0},
        calls={"llm_per_question": 0.0}, metrics={"gold@5": 0.7}))
    write(project / "results" / "dev_ret2" / "summary.json", _summary(
        "dev_ret2", retrieval_only=True, compare_to="dev_ret", cost={"usd_per_question": 0.0, "tokens_per_question": 0},
        calls={"llm_per_question": 0.0}, metrics={"gold@5": 0.8}))
    rows = _report_rows(project)
    ret = rows["dev_ret"]
    assert ret["gold@5"] == "0.70 (+0.20)"                       # the retrieval metric is comparable
    assert ret["USD/question"] == "0.00000" and ret["Tokens/question"] == "0" and ret["LLM calls/question"] == "0.00"
    assert "only the metrics columns show a difference" in ret["Compared to"]
    assert rows["dev_ret2"]["gold@5"] == "0.80 (+0.10)" and "only the metrics" not in rows["dev_ret2"]["Compared to"]


# ---------------------------------------------------------------- f03: calls that cannot be costed

def _cfg(project):
    return load_config(project / "configs" / "baseline.yaml")


@pytest.mark.parametrize("steps, why", [
    ({"calls": None}, "`calls` is NoneType, not a list"),
    ({"calls": ["x"]}, "calls[0] is str, not a mapping"),
    ({"calls": [{"input_tokens": 5}]}, "calls[0] has no `model`"),
    ({"calls": [{"model": "m", "input_tokens": None}]}, "calls[0].input_tokens is None"),
    ({"calls": [{"model": "m", "output_tokens": "9"}]}, "calls[0].output_tokens is '9'"),
    ({"calls": [{"model": "m", "input_tokens": float("nan")}]}, "calls[0].input_tokens is nan"),
])
def test_f03_calls_that_cannot_be_costed_make_a_failed_question_and_not_a_dead_batch(project, steps, why):
    class Odd(FakeSystem):
        def run(self, question, scope):
            return {"status": "answered", "answer": "x", "steps": steps}

    out = run_experiment(_cfg(project), "dev", experiment="dev_x", system=Odd())
    assert out["statuses"] == {"error": 2}                       # both questions ran; the batch did not stop at the first
    rows = RunLog(project / "out" / "runs.db").load("dev_x")
    assert len(rows) == 2 and all(r["status"] == "error" and why in r["error"] and r["steps"] is None for r in rows)


def test_f03_a_call_that_omits_its_token_counts_is_fine(project):
    class Sparse(FakeSystem):
        def run(self, question, scope):
            return {"status": "answered", "answer": "x", "steps": {"calls": [{"model": "m"}]}}

    out = run_experiment(_cfg(project), "dev", experiment="dev_x", system=Sparse())
    assert out["statuses"] == {"answered": 2}
    assert all(r["input_tokens"] == 0 and r["n_llm_calls"] == 1 for r in RunLog(project / "out" / "runs.db").load("dev_x"))


def test_f03_a_judge_that_returns_calls_it_cannot_cost_is_refused_at_grading(project):
    run_experiment(_cfg(project), "dev", experiment="dev_x", system=FakeSystem())
    JUDGES["fake-judge"] = lambda item, run, cfg: {"correctness": 1, "reason": "x", "calls": None}   # the fixture removes it
    with pytest.raises(ValueError, match=r"judge returned calls that cannot be costed .*`calls` is NoneType"):
        grade_experiment("dev_x")


# ---------------------------------------------------------------- f06: an empty --sets

@pytest.mark.parametrize("value", ["", ",", " ", " , "])
def test_f06_an_empty_sets_value_is_an_error_and_not_the_whole_split(cli_project, capsys, value):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--sets", value]) == 1
    assert "names no golden set" in capsys.readouterr().err
    assert not (cli_project / "out" / "runs.db").exists()               # nothing was run or billed


def test_f06_run_experiment_refuses_an_empty_list_of_sets(project):
    with pytest.raises(ValueError, match="names no golden set"):
        run_experiment(_cfg(project), "dev", experiment="dev_x", sets=[], system=FakeSystem())


# ---------------------------------------------------------------- f07: a golden set with no corpus scope

def test_f07_a_golden_set_missing_from_set_scope_is_refused_before_any_question_is_run(project):
    write(project / "configs" / "baseline.yaml",
          BASELINE.replace("set_scope: {base_positive: A, base_negative: A}", "set_scope: {base_positive: A}"))
    with pytest.raises(ValueError, match=r"no entry for the golden set\(s\) base_negative; it names base_positive"):
        run_experiment(_cfg(project), "dev", experiment="dev_x", system=FakeSystem())
    assert not (project / "out" / "runs.db").exists()                   # not one question was run, so nothing was paid for


def test_f07_a_run_of_one_set_needs_only_that_sets_scope(project):
    write(project / "configs" / "baseline.yaml",
          BASELINE.replace("set_scope: {base_positive: A, base_negative: A}", "set_scope: {base_positive: A}"))
    out = run_experiment(_cfg(project), "dev", experiment="dev_x", sets=["base_positive"], system=FakeSystem())
    assert out["ran"] == 1


# ---------------------------------------------------------------- f08: a wrong-shaped config is a one-line error

JUDGE = "judge: {name: cli-judge, model: j}\n"


def _run(config):
    return main(["run", "--config", config, "--split", "dev", "--limit", "1"])


@pytest.mark.parametrize("text, why", [
    ("name: s\nsystem: cli-sys\nanswer: gpt-x\n" + JUDGE, "`answer` must be a mapping"),
    ("name: s\nsystem: cli-sys\n" + "judge: gpt-x\n", "`judge` must be a mapping"),
    ("name: s\nsystem: cli-sys\n" + JUDGE + "run: 4\n", "`run` must be a mapping"),
    ("name: s\nsystem: cli-sys\n" + JUDGE + "run: {concurrency: '4'}\n", "`run.concurrency` (worker threads) must be a whole number"),
    ("name: s\nsystem: cli-sys\n" + JUDGE + "run: {concurrency: 0}\n", "`run.concurrency` (worker threads) must be a whole number"),
    ("name: s\nsystem: cli-sys\n" + JUDGE + "run: {concurrency: null}\n", "`run.concurrency` (worker threads) must be a whole number"),
    ("name: s\nsystem: cli-sys\n" + JUDGE + "card: [a]\n", "`card` must be text"),
    ("name: s\n" + JUDGE, "the config has no `system:`"),
])
def test_f08_a_config_of_the_wrong_shape_is_a_one_line_error(cli_project, capsys, text, why):
    write(cli_project / "configs" / "odd.yaml", text)
    assert _run("configs/odd.yaml") == 1
    err = capsys.readouterr().err
    assert why in err and err.count("\n") == 1 and "Traceback" not in err


def test_f08_a_config_argument_that_is_a_folder_is_a_one_line_error(cli_project, capsys):
    assert _run("configs") == 1
    assert "is a folder, not a config file" in capsys.readouterr().err


def test_f08_a_card_that_is_a_folder_is_a_one_line_error(cli_project, capsys):
    (cli_project / "experiments" / "dir.md").mkdir(parents=True)
    write(cli_project / "configs" / "v.yaml", "base: baseline.yaml\nname: v\ncard: experiments/dir.md\nretrieval: {k: 9}\n")
    assert main(["run", "--config", "configs/v.yaml", "--split", "dev"]) == 1
    assert "is a folder, not a card file" in capsys.readouterr().err


def test_f08_an_empty_name_is_the_file_name_and_not_none(cli_project):
    write(cli_project / "configs" / "blank.yaml", "name:\nsystem: cli-sys\n" + JUDGE)
    assert load_config(cli_project / "configs" / "blank.yaml")["name"] == "blank"


@pytest.mark.parametrize("text, why", [
    ("golden: x\n", "`golden` must be a mapping"),
    ("report: x\n", "`report` must be a mapping"),
    ("behaviors: x\n", "`behaviors` must be a mapping"),
    ("- a\n- b\n", "labkit.yaml is a mapping of settings, not list"),
    ("max_score: 1" + "0" * 400 + "\n", "`max_score` must be a positive number"),
])
def test_f08_a_labkit_yaml_of_the_wrong_shape_is_a_one_line_error(cli_project, capsys, text, why):
    write(cli_project / "labkit.yaml", text)
    assert main(["status"]) == 1
    err = capsys.readouterr().err
    assert why in err and "Traceback" not in err


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_f08_compare_refuses_a_max_score_that_is_not_a_positive_number(cli_project, capsys, value):
    assert main(["compare", "dev_a", "dev_b", "--max-score", value]) == 1
    assert "--max-score must be a positive number" in capsys.readouterr().err


def test_f08_a_ready_that_returns_the_wrong_shape_is_a_one_line_error(project):
    class Odd(FakeSystem):
        def ready(self):
            return True

    with pytest.raises(ValueError, match=r"ready\(\) must return \(ok: bool, detail: str\)"):
        run_experiment(_cfg(project), "dev", experiment="dev_x", system=Odd())


def test_f08_a_retrieval_only_run_of_a_system_without_retrieve_is_refused_before_any_question(project):
    class AnswerOnly:
        def run(self, question, scope):
            return {"status": "answered", "answer": "x"}

    with pytest.raises(ValueError, match=r"AnswerOnly has no retrieve\(question, scope, steps=None\)"):
        run_experiment(_cfg(project), "dev", experiment="dev_x_retrieval", retrieval_only=True, system=AnswerOnly())
    assert RunLog(project / "out" / "runs.db").load("dev_x_retrieval") == []        # not one question was attempted


# ---------------------------------------------------------------- f09: the test split by recorded split, and case

def _untouched(root):
    return AUTOCHECKS["split_and_test_untouched"](root, load_project(root))


def test_f09_a_dev_run_named_test_something_is_not_a_test_run(cli_project):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--name", "test_noise_check",
                 "--limit", "1"]) == 0
    ok, detail = _untouched(cli_project)
    assert ok, detail                                           # the recorded split is dev: the name proves nothing


def test_f09_a_test_run_under_another_name_is_still_found(cli_project):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "test", "--final", "--name", "final_try",
                 "--limit", "1"]) == 0
    ok, detail = _untouched(cli_project)
    assert not ok and "final_try" in detail


def test_f09_the_name_fallback_for_rows_without_a_recorded_split_ignores_case():
    assert experiment_split("Test_legacy", [{"config": {}}]) == "test"
    assert experiment_split("TEST_legacy", [{"config": {}}]) == "test"
    with pytest.raises(SplitLocked):
        guard_test("Test", False)


def test_f09_exporting_the_runs_of_a_legacy_test_experiment_needs_final_whatever_the_case(tmp_path):
    db = tmp_path / "runs.db"
    RunLog(db).save({"status": "answered"}, experiment="Test_legacy", qid="Q1")      # no recorded selection
    done = subprocess.run([sys.executable, "-m", "labkit.eval.runlog", "export", str(db), "Test_legacy",
                           str(tmp_path / "out.json")], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 1 and "needs --final" in done.stderr and not (tmp_path / "out.json").exists()


# ---------------------------------------------------------------- f05: a grades suffix the cost totals do not read

@pytest.mark.parametrize("suffix", ["__noise1", "__x", "__r2", "__"])
def test_f05_a_suffix_that_is_not_a_regrade_is_refused_so_its_cost_is_never_left_out(project, suffix):
    run_experiment(_cfg(project), "dev", experiment="dev_000_baseline", system=FakeSystem())
    with pytest.raises(ValueError, match="starts with __regrade"):
        grade_experiment("dev_000_baseline", suffix=suffix)
    assert not (project / "out" / "grades" / f"dev_000_baseline{suffix}.jsonl").exists()


@pytest.mark.parametrize("suffix", ["__regrade1", "__regrade", "__regrade_b.2"])
def test_f05_every_regrade_suffix_is_still_accepted_and_its_judge_cost_is_counted(project, suffix):
    run_experiment(_cfg(project), "dev", experiment="dev_000_baseline", system=FakeSystem())
    grade_experiment("dev_000_baseline")
    grade_experiment("dev_000_baseline", suffix=suffix)
    assert graded_and_evaluated("dev_000_baseline")["cost"]["judge_regrade_usd"] > 0


# ---------------------------------------------------------------- f10: what follows the exit criteria

def _append_to_step(root, n, text):
    path = steps.step_file(root, n)
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_f10_a_section_after_the_exit_criteria_is_not_read_as_criteria(sproj):
    before = [(c.text, c.auto) for c in steps.criteria(sproj, 3)]
    _append_to_step(sproj, 3, "\n## See also\n\n- [WORKFLOW](../WORKFLOW.md)\n- [ ] a note, not a criterion\n")
    assert [(c.text, c.auto) for c in steps.criteria(sproj, 3)] == before


def test_f10_a_checkbox_shown_in_a_code_block_is_not_a_criterion(sproj):
    before = [(c.text, c.auto) for c in steps.criteria(sproj, 3)]
    _append_to_step(sproj, 3, "\nWrite each criterion like this:\n\n```\n- [ ] an example\n```\n")
    assert [(c.text, c.auto) for c in steps.criteria(sproj, 3)] == before


def test_f10_a_checkbox_line_inside_the_section_that_does_not_parse_is_still_an_error(sproj):
    path = steps.step_file(sproj, 3)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("## Exit criteria\n", "## Exit criteria\n\n- []broken\n", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="does not parse as a criterion"):
        steps.criteria(sproj, 3)


# ---------------------------------------------------------------- f11: a large input file is not held in memory

def _whole_file_hash(data: bytes) -> str:
    """What file_hash computed before it read in blocks: the whole content, CRLF read as LF."""
    return hashlib.sha1(data.replace(b"\r\n", b"\n")).hexdigest()[:10]


CONTENTS = [b"", b"\r", b"\n", b"\r\n", b"a", b"a\r\nb\r\nc", b"\r\r\n", b"\r\r\r\n\n", b"x\r", b"\r\n\r\n", b"a\rb\nc\r\n",
            bytes(range(256)) * 3, b"\xff\xfe" + "휴면".encode("utf-16-le") + b"\r\n"]


@pytest.mark.parametrize("block", [1, 2, 3, 4, 7, 64])
def test_f11_the_hash_is_the_same_as_for_the_whole_content_at_every_block_boundary(tmp_path, monkeypatch, block):
    monkeypatch.setattr(freeze, "HASH_BLOCK", block)     # small blocks put every CR/LF pair across a boundary somewhere
    for number, data in enumerate(CONTENTS):
        path = tmp_path / f"f{number}.bin"
        path.write_bytes(data)
        assert freeze.file_hash(path) == _whole_file_hash(data), (block, data)


def test_f11_hashes_recorded_before_the_change_still_match(tmp_path):
    path = tmp_path / "prompt.md"
    path.write_bytes(b"Answer from the context.\r\nSay not found otherwise.\r\n")
    assert freeze.file_hash(path) == _whole_file_hash(b"Answer from the context.\nSay not found otherwise.\n")


def test_f11_a_large_file_is_not_held_in_memory(tmp_path):
    path = tmp_path / "pages.jsonl"
    line = b'{"page": 1, "text": "lorem ipsum dolor sit amet"}\r\n'
    with path.open("wb") as f:
        for _ in range(40 * 1024 * 1024 // len(line)):                  # about 40 MB, with a CRLF every 50 bytes
            f.write(line)
    tracemalloc.start()
    try:
        digest = freeze.file_hash(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f"file_hash held {peak / 1e6:.0f} MB of a 40 MB file"
    assert digest == _whole_file_hash(path.read_bytes())


# ---------------------------------------------------------------- f04: golden fields of the wrong shape

@pytest.mark.parametrize("field, value, why", [
    ("question", None, "`question` must be non-empty text"),
    ("question", "  ", "`question` must be non-empty text"),
    ("set", 5, "`set` must be non-empty text"),
    ("set", "", "`set` must be non-empty text"),
    ("targets", "a.pdf", "`targets` must be a list"),
    ("targets", [{"file": "a.pdf"}], "`targets` must be a list"),
    ("targets", [{"file": "a.pdf", "page": "3"}], "`targets` must be a list"),
    ("evidence", "one quote", "`evidence` must be a list of text quotes"),
])
def test_f04_a_golden_item_with_a_field_of_the_wrong_shape_is_refused_at_load(project, field, value, why):
    write(project / "golden" / "provided.jsonl", json.dumps({**GOLDEN[0], field: value}, ensure_ascii=False) + "\n")
    with pytest.raises(ValueError) as error:
        load_golden()
    assert str(error.value).startswith("provided.jsonl:1: POS001: ") and why in str(error.value)


def test_f04_a_null_set_and_null_targets_mean_none_and_do_not_break_evaluate(project):
    write(project / "configs" / "baseline.yaml", "name: 000_baseline\nsystem: fake\njudge: {name: fake-judge, model: j}\n")
    items = [{**GOLDEN[0], "set": None, "targets": None}, GOLDEN[1]]       # one set is text, one is null
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    run_experiment(_cfg(project), "dev", experiment="dev_x", system=FakeSystem())
    summary = graded_and_evaluated("dev_x")
    assert sorted(summary["by_set"]) == ["all", "base_negative"]
