"""Tests for what the seventh review round found (labkit at 6efba5d, v0.1.5).

Two sub-agents reviewed v0.1.5 (code and behaviour, documents), and a type-fuzz put a wrong value into every field a
user or plugin supplies. The test names carry the number the finding has in that round's list (f01 ... f12). Each fails on the code as it was at 6efba5d, except the few that pin down
behaviour that already held (the legal result shapes, a `#` heading or a tilde fence in the criteria section).
"""

import json
import re
import subprocess
import warnings
from pathlib import Path

import pytest

from labkit import scaffold, steps
from labkit.autochecks import AUTOCHECKS
from labkit.cli import main
from labkit.config import load_config
from labkit.eval.runlog import RunLog
from labkit.harness import evaluate_experiment, grade_experiment, run_experiment
from labkit.project import LabkitWarning, load_project
from labkit.show import show
from test_cli import project  # noqa: F401
from test_harness import PRICES, FakeSystem, fake_judge  # noqa: F401
from test_harness import project as hproject  # noqa: F401
from test_steps import proj as sproj  # noqa: F401


# ---------------------------------------------------------------- f08: new-card names that YAML reads as numbers

@pytest.mark.parametrize("slug", ["12", "7-7", "2024", "1e3", "8", "ab", "0-1"])
def test_f08_a_new_card_name_reads_back_as_the_text_it_was_written_as(tmp_path, slug):
    root = tmp_path / "p"
    scaffold.init_project(root)
    made = scaffold.new_card(root, slug)
    assert load_config(root / made["config"], root=root)["name"] == f"001_{slug.replace('-', '_')}"  # not 74 for 001_12


# ---------------------------------------------------------------- f06: labkit check 0

def test_f06_check_of_step_0_is_refused_not_read_as_the_current_step(tmp_path, capsys):
    root = tmp_path / "p"
    scaffold.init_project(root)
    assert main(["--root", str(root), "check", "1"]) in (0, 1)
    assert capsys.readouterr().out.startswith("Step 1:")
    assert main(["--root", str(root), "check", "0"]) == 1     # was the current step's list, as if no number were given
    assert "steps are numbered 1..8, not 0" in capsys.readouterr().err


# ---------------------------------------------------------------- f10: compare across an answer and a retrieval-only run

def answer_and_retrieval_results():
    for args in (["run", "--config", "configs/baseline.yaml", "--split", "dev"],
                 ["grade", "--experiment", "dev_000_baseline"],
                 ["evaluate", "--experiment", "dev_000_baseline"],
                 ["run", "--config", "configs/baseline.yaml", "--split", "dev", "--retrieval-only"],
                 ["evaluate", "--experiment", "dev_000_baseline_retrieval"]):
        assert main(args) == 0, args


def test_f10_compare_warns_when_an_answer_run_meets_a_retrieval_only_run_and_leaves_out_the_cost(project, capsys):
    answer_and_retrieval_results()
    capsys.readouterr()
    assert main(["compare", "dev_000_baseline", "dev_000_baseline_retrieval", "--metric", "page_hit@5"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("WARNING: dev_000_baseline is an answer run and dev_000_baseline_retrieval is retrieval-only")
    assert "USD/question" not in out and "LLM calls/question" not in out and "p90 latency" not in out


def test_f10_compare_of_a_retrieval_only_result_without_metric_says_it_has_no_grades(project, capsys):
    answer_and_retrieval_results()
    capsys.readouterr()
    assert main(["compare", "dev_000_baseline", "dev_000_baseline_retrieval"]) == 1
    err = capsys.readouterr().err
    assert "dev_000_baseline_retrieval is a retrieval-only result: it has no grades; use --metric" in err
    assert "run `labkit evaluate`" not in err                         # it was evaluated: that advice was wrong


# ---------------------------------------------------------------- f03, f04: one boundary for every system result

class Patched(FakeSystem):
    """FakeSystem whose result for the first question (POS001, `휴면`) goes through `patch(result)` first."""

    def __init__(self, patch):
        super().__init__()
        self.patch = patch

    def run(self, question, scope):
        out = super().run(question, scope)
        if "휴면" in question:
            out = self.patch(out)
        return out


def through_every_stage(cfg, system):
    """run, grade, evaluate and show, as a user would: none of them may raise."""
    r = run_experiment(cfg, "dev", experiment="dev_x", system=system)
    grade_experiment("dev_x")
    summary = evaluate_experiment("dev_x", prices=PRICES)
    show("dev_x", all_rows=True)
    rows = {row["qid"]: row for row in RunLog(Path("out") / "runs.db").load("dev_x")}
    return r, summary, rows


CALL_USD = (1000 * 0.75 + 200 * 3.75) / 1e6


def test_f03_a_numpy_token_count_in_one_question_is_stored_as_a_number_and_costed(hproject):
    np = pytest.importorskip("numpy")

    def patch(out):
        out["steps"]["calls"][0]["input_tokens"] = np.int64(1000)
        out["steps"]["calls"][0]["output_tokens"] = np.float64(200.0)
        return out
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(patch))
    assert r["statuses"] == {"answered": 1, "abstained": 1}
    call = rows["POS001"]["steps"]["calls"][0]
    assert type(call["input_tokens"]) is int and type(call["output_tokens"]) is float
    assert summary["cost"]["usd"] == pytest.approx(2 * CALL_USD)          # was TypeError in cost.call_usd


def setting(key, value):
    def patch(out):
        out[key] = value
        return out
    return patch


def unit(**change):
    def patch(out):
        out["context"] = [{**out["context"][0], **change}, *out["context"][1:]]
        return out
    return patch


BAD = {
    "source None": unit(source=None), "page text": unit(page="1"), "chunk_id list": unit(chunk_id=[]),
    "content None": unit(content=None), "page bool": unit(page=True), "pages text": unit(pages="3"),
    "unit is text": setting("context", ["a chunk"]), "context 7": setting("context", 7),
    "citations text": setting("citations", "x"), "citation without page": setting("citations", [{"chunk_id": "c1", "source": "a.pdf"}]),
    "steps list": setting("steps", []), "searches mapping": setting("searches", {}),
    "chunk_ids text": setting("searches", [{"query": "q", "chunk_ids": "x"}]),
    "search is text": setting("searches", ["q"]),
}


@pytest.mark.parametrize("name", BAD)
def test_f04_a_result_of_a_shape_labkit_cannot_read_is_a_failed_question_and_nothing_raises_later(hproject, name):
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(BAD[name]))
    assert r["ran"] == 2 and r["statuses"] == {"error": 1, "abstained": 1}           # the batch went on
    bad = rows["POS001"]
    assert "the system returned a result labkit cannot use" in bad["error"] and "fix the system" in bad["error"]
    assert summary["complete"] is False


def test_f04_a_refused_result_keeps_the_cost_of_its_calls(hproject):
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(unit(source=None)))
    assert [c["model"] for c in rows["POS001"]["steps"]["calls"]] == ["m"]
    assert summary["cost"]["wasted_usd"] == pytest.approx(CALL_USD)


def test_f04_a_refused_error_result_keeps_the_systems_own_reason(hproject):
    def patch(out):
        return {"status": "error", "error": "timeout talking to the model", "steps": out["steps"], "context": 7}
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(patch))
    assert "cannot use" in rows["POS001"]["error"] and "timeout talking to the model" in rows["POS001"]["error"]


def test_f04_labkit_counts_tokens_calls_and_latency_itself(hproject):
    def patch(out):
        out.update(latency_s="fast", input_tokens=999999, output_tokens=-5, n_llm_calls="many")
        return out
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(patch))
    pos = rows["POS001"]
    assert pos["status"] == "answered" and isinstance(pos["latency_s"], float)          # was TypeError in _quantile
    assert (pos["input_tokens"], pos["output_tokens"], pos["n_llm_calls"]) == (1000, 200, 1)


@pytest.mark.parametrize("value, stored", [("x", 1), (-1, 1), (True, 1), (2.5, 1), (3, 3), (0, 0)])
def test_f04_an_n_searches_that_is_not_a_count_becomes_the_number_of_searches(hproject, value, stored):
    def patch(out):
        out.update(n_searches=value, searches=[{"query": "q", "chunk_ids": ["c1"]}])
        return out
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(patch))
    assert rows["POS001"]["status"] == "answered" and rows["POS001"]["n_searches"] == stored


LEGAL = {
    "context null": setting("context", None), "chunk_ids null": setting("searches", [{"query": "q", "chunk_ids": None}]),
    "unit with pages": unit(pages=[3, 4]), "unit with a score": unit(score=0.9), "whole-number chunk id": unit(chunk_id=7),
    "tuple context": lambda out: {**out, "context": tuple(out["context"])},
}


@pytest.mark.parametrize("name", LEGAL)
def test_f04_the_shapes_a_system_may_return_still_pass(hproject, name):
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, Patched(LEGAL[name]))
    assert r["statuses"] == {"answered": 1, "abstained": 1}


# ---------------------------------------------------------------- f01, f09: golden items of the wrong shape

def golden_with(root, **change):
    from test_harness import GOLDEN
    items = [{**GOLDEN[0], **change}, *GOLDEN[1:]]
    (root / "golden" / "provided.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items),
                                                    encoding="utf-8")


def test_f01_evidence_null_means_no_evidence_and_evaluate_does_not_raise(hproject):
    golden_with(hproject, evidence=None)
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    r, summary, rows = through_every_stage(cfg, FakeSystem())                 # was TypeError at evaluate
    assert r["statuses"] == {"answered": 1, "abstained": 1} and summary["complete"] is True


@pytest.mark.parametrize("key, value", [("expected_behavior", ["x"]), ("expected_behavior", {"a": 1}), ("scope", []),
                                        ("scope", ["x"]), ("scope", "")])
def test_f01_a_golden_behavior_or_scope_that_is_not_text_is_refused_before_the_run(hproject, key, value):
    golden_with(hproject, **{key: value})
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    with pytest.raises(ValueError, match=f"`{key}` must be non-empty text"):
        run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    assert not (hproject / "out" / "runs.db").exists()                        # refused before anything ran


def golden_check_of(project, capsys, **change):
    from test_cli import GOLDEN
    (project / "corpus").mkdir(exist_ok=True)
    (project / "corpus" / "pages.jsonl").write_text(
        json.dumps({"file": "a.pdf", "page": 3, "text": "휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    items = [{**GOLDEN[0], **change}, *GOLDEN[1:]]
    (project / "golden" / "provided.jsonl").write_text(
        "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    code = main(["golden-check", "--pages", "corpus/pages.jsonl"])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.mark.parametrize("change, said", [({"evidence": [1]}, "`evidence` must be a list of text quotes"),
                                          ({"targets": 5}, "`targets` must be a list"),
                                          ({"question": None}, "`question` must be non-empty text")])
def test_f09_golden_check_reports_what_every_run_refuses_instead_of_crashing_or_passing_it(project, capsys, change, said):
    code, text = golden_check_of(project, capsys, **change)
    assert code == 1 and f"POS001: {said}" in text                   # was AttributeError, TypeError, or no problem


# ---------------------------------------------------------------- f05: config and labkit.yaml shapes

@pytest.mark.parametrize("text, said", [
    ("base: [baseline.yaml]\nname: v\n", "`base` must be text"),
    ("base: baseline.yaml\nname: v\ncorpus: {set_scope: [A, B]}\n", "`corpus.set_scope` must map each golden set"),
    ("base: baseline.yaml\nname: v\ncorpus: {set_scope: {base_positive: [A]}}\n", "`corpus.set_scope` must map"),
    ("base: baseline.yaml\nname: v\njudge: {name: [x]}\n", "`judge.name` must be the name a judge is registered under"),
    ("base: baseline.yaml\nname: v\njudge: {name: ''}\n", "`judge.name` must be"),
    ("base: baseline.yaml\nname: v\njudge: {prompt: 7}\n", "`judge.prompt` must be a file path"),
])
def test_f05_a_config_key_of_the_wrong_shape_is_refused_when_it_loads(hproject, text, said):
    (hproject / "configs" / "v.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(said)):
        load_config(hproject / "configs" / "v.yaml")


LABKIT = "golden: {files: [provided.jsonl]}\n"


@pytest.mark.parametrize("text, said", [
    ("behaviors: {a: {group: answer, statuses: answered}}\n", "write a list: statuses: [answered]"),
    ("behaviors: {a: {group: answer, statuses: 7}}\n", "statuses must be a list of statuses"),
    ("behaviors: {a: {group: answer, statuses: [1]}}\n", "statuses must be a list of statuses"),
    ("behaviors: {a: {group: 7, statuses: [answered]}}\n", "group must be a group name"),
    ("report: {columns: [1]}\n", "each entry of `report.columns`"),
    ("report: {columns: 7}\n", "`report.columns` must be a list"),
    ("report: {columns: [{path: scores.x, higher_is_better: 'no'}]}\n", "each entry of `report.columns`"),
    ("index_dir: 'out/{index_key.x}'\n", "`index_dir` must be a folder path"),
])
def test_f05_a_labkit_yaml_setting_of_the_wrong_shape_is_refused_when_it_loads(tmp_path, text, said):
    """`statuses: answered` was read letter by letter (status_ok 0.5, no error); `columns: [1]` and `{index_key.x}`
    were a TypeError in `labkit report` and an AttributeError in every command."""
    (tmp_path / "labkit.yaml").write_text(LABKIT + text, encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(said)):
        load_project(tmp_path)


# ---------------------------------------------------------------- f12: a misspelled labkit.yaml key

def test_f12_a_key_that_looks_like_a_known_one_is_warned_about_once(tmp_path):
    (tmp_path / "labkit.yaml").write_text("golden: {files: [g.jsonl], page: corpus/p.jsonl}\nmaxscore: 3\n"
                                          "my_plugin: {anything: 1}\n", encoding="utf-8")
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        load_project(tmp_path)
        load_project(tmp_path)
    said = sorted(str(w.message) for w in seen if issubclass(w.category, LabkitWarning))
    assert len(said) == 2                                                   # once each, not once per load
    assert "unknown key `golden.page` (did you mean `golden.pages`?)" in said[0]
    assert "unknown key `maxscore` (did you mean `max_score`?)" in said[1]   # my_plugin has no close match: quiet


def test_f12_the_cli_prints_the_typo_warning_as_one_line(project, capsys):
    with open(project / "labkit.yaml", "a", encoding="utf-8") as f:
        f.write("modulse: [x]\n")
    assert main(["status"]) in (0, 1)
    err = capsys.readouterr().err
    assert "labkit: warning:" in err and "unknown key `modulse` (did you mean `modules`?)" in err
    assert "UserWarning" not in err and "LabkitWarning" not in err


# ---------------------------------------------------------------- Q3: an unregistered judge is found before the run

def test_c6_a_judge_name_nobody_registered_is_refused_before_any_question_runs(hproject):
    path = hproject / "configs" / "baseline.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("fake-judge", "my-judge"), encoding="utf-8")
    cfg = load_config(path)
    with pytest.raises(ValueError, match="judge.name 'my-judge' is not a registered judge"):
        run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())     # was found only at `labkit grade`
    assert not (hproject / "out" / "runs.db").exists()
    r = run_experiment(cfg, "dev", experiment="dev_x_retrieval", system=FakeSystem(), retrieval_only=True)
    assert r["ran"] == 2                                                      # a search-only run needs no judge


# ---------------------------------------------------------------- f11: the judge prompt as a rule, not a sentence

@pytest.mark.parametrize("limit", [None, 1])                                  # a smoke test is refused too
def test_f11_an_answer_run_whose_judge_prompt_does_not_exist_is_refused(hproject, limit):
    (hproject / "prompts" / "judge.md").unlink()
    cfg = load_config(hproject / "configs" / "baseline.yaml")
    with pytest.raises(ValueError, match="judge.prompt names prompts/judge.md, which does not exist: create it before"):
        run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem(), limit=limit)
    assert not (hproject / "out" / "runs.db").exists()                       # was: run, grade, evaluate all fine
    r = run_experiment(cfg, "dev", experiment="dev_x_retrieval", system=FakeSystem(), retrieval_only=True)
    assert r["ran"] == 2                                                      # the search alone is not graded


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True, capture_output=True)


@pytest.mark.needs_git
def test_f11_the_judge_prompt_criterion_of_step_1_is_checked_by_code(sproj):
    check = AUTOCHECKS["judge_prompt_committed"]
    prompt = sproj / "prompts" / "judge_v1.md"                                # what the template's baseline.yaml names
    git(sproj, "init")
    ok, detail = check(sproj, {})
    assert not ok and "prompts/judge_v1.md (judge.prompt of baseline.yaml) does not exist" in detail
    prompt.write_text("Grade 0 to 2.\n", encoding="utf-8")
    ok, detail = check(sproj, {})
    assert not ok and "prompts/judge_v1.md is not tracked by git" in detail
    git(sproj, "add", ".")
    git(sproj, "commit", "-m", "judge prompt")
    ok, detail = check(sproj, {})
    assert ok and detail.startswith("committed: prompts/judge_v1.md at ")
    prompt.write_text("Grade 0 to 5.\n", encoding="utf-8")
    ok, detail = check(sproj, {})
    assert not ok and "prompts/judge_v1.md has uncommitted changes" in detail


def test_f11_a_project_whose_judge_uses_no_prompt_file_passes_the_check(sproj):
    base = sproj / "configs" / "baseline.yaml"
    base.write_text(base.read_text(encoding="utf-8").replace("  prompt: prompts/judge_v1.md\n", ""), encoding="utf-8")
    assert AUTOCHECKS["judge_prompt_committed"](sproj, {}) == (True, "no config sets judge.prompt (the judge uses no "
                                                                     "prompt file)")


# ---------------------------------------------------------------- f02: reading the exit criteria by CommonMark's rules

def criteria_of(root, body, before=""):
    """Step 3's criteria when everything from its `## Exit criteria` heading on is `body` (and `before` precedes it)."""
    path = steps.step_file(root, 3)
    head = path.read_text(encoding="utf-8").split("## Exit criteria", 1)[0]
    path.write_text(head + before + body, encoding="utf-8")
    return [c.text for c in steps.criteria(root, 3)]


FENCE3 = "`" * 3


@pytest.mark.parametrize("name, body", [
    ("a heading inside a comment", "## Exit criteria\n- [ ] A\n<!--\n## old\n-->\n- [ ] B\n- [ ] C\n"),
    ("three backticks inside a line", f"## Exit criteria\n- [ ] A\n\n{FENCE3}inline{FENCE3} is how it is spelled.\n\n- [ ] B\n- [ ] C\n"),
    ("a comment that hides a box", "## Exit criteria\n- [ ] A\n- [ ] B\n<!-- an old one:\n- [ ] gone\n-->\n- [ ] C\n"),
])
def test_f02_nothing_hides_a_criterion_the_file_shows(sproj, name, body):
    assert criteria_of(sproj, body) == ["A", "B", "C"]                     # was ['A'] for the first two


def test_f02_the_section_is_the_first_real_heading_not_an_earlier_mention_of_it(sproj):
    before = f"Write the section like this:\n\n{FENCE3}\n## Exit criteria\n- [ ] sample\n{FENCE3}\n\n"
    assert criteria_of(sproj, "## Exit criteria\n- [ ] A\n- [ ] B\n", before) == ["A", "B"]   # was ['sample']


@pytest.mark.parametrize("name, body", [
    ("a three-backtick fence inside a four-backtick one",
     f"## Exit criteria\n- [ ] A\n\n````\n{FENCE3}\n- [ ] example\n{FENCE3}\n````\n\n- [ ] B\n"),
    ("a tilde fence", "## Exit criteria\n- [ ] A\n\n~~~\n- [ ] example\n~~~\n\n- [ ] B\n"),
    ("a fence closed by a longer one", f"## Exit criteria\n- [ ] A\n\n{FENCE3}\n- [ ] example\n````\n\n- [ ] B\n"),
])
def test_f02_a_box_in_any_code_block_is_not_a_criterion(sproj, name, body):
    assert criteria_of(sproj, body) == ["A", "B"]


@pytest.mark.parametrize("heading", ["# Appendix", "  ## Notes", "   # Indented three"])
def test_f02_a_first_or_second_level_heading_indented_up_to_three_spaces_ends_the_section(sproj, heading):
    assert criteria_of(sproj, f"## Exit criteria\n- [ ] A\n\n{heading}\n\n- [ ] not a criterion\n") == ["A"]


def test_f02_a_third_level_heading_inside_the_section_does_not_end_it(sproj):
    assert criteria_of(sproj, "## Exit criteria\n- [ ] A\n\n### Later\n\n- [ ] B\n") == ["A", "B"]


def test_f02_a_bullet_that_starts_with_a_link_is_not_read_as_a_broken_box(sproj):
    body = "## Exit criteria\n- [ ] A\n- [Docs](x.md) explain how to claim it\n"
    assert criteria_of(sproj, body) == ["A"]                                # was: does not parse as a criterion
    with pytest.raises(ValueError, match="does not parse as a criterion"):
        criteria_of(sproj, "## Exit criteria\n- [ ] A\n- []broken\n")        # a broken box is still an error


# ---------------------------------------------------------------- f07: writers and readers of one file at once

def test_f07_concurrent_writers_and_readers_of_one_summary_see_no_error_and_no_half_file(tmp_path):
    """Four writers and three readers on one summary.json (two labkit commands at once, say evaluate and report).
    With one shared `<file>.tmp` and no retry, Windows gave about 750 PermissionError and dozens of FileNotFoundError
    per run; elsewhere the test passes either way. A half-written file must never be read."""
    import collections
    import threading
    import time
    from labkit.eval import load_summary
    from labkit.harness import _atomic_write
    target = tmp_path / "summary.json"
    _atomic_write(target, json.dumps({"n": 0}))
    errors, stop = collections.Counter(), threading.Event()

    def writer(k):
        for i in range(100):
            try:
                _atomic_write(target, json.dumps({"writer": k, "i": i, "pad": "x" * 2000}))
            except Exception as e:  # noqa: BLE001
                errors[f"writer {type(e).__name__}"] += 1

    def reader():
        while not stop.is_set():
            try:
                load_summary(target)                      # a half-written file would be a ValueError here
            except Exception as e:  # noqa: BLE001
                errors[f"reader {type(e).__name__}"] += 1
            time.sleep(0.002)

    writers = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()
    assert not errors, dict(errors)
    assert [p.name for p in tmp_path.iterdir()] == ["summary.json"]             # no temporary file left behind
