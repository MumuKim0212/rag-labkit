"""Tests for what the fourth review round found (labkit at 5e64961, v0.1.2; the fifth review round).

The reviewer tried to break the guarantees; each finding was reproduced again before it was fixed, and the test name
carries the finding's number in the report (F01 ... F13)."""

import json
import re

import pytest

from labkit import steps
from labkit.cli import main
from labkit.config import load_config
from labkit.eval.runlog import RunLog
from labkit.golden import read_golden_file
from labkit.cost import record_call
from labkit.harness import SplitLocked, build_report, evaluate_experiment, grade_experiment, run_experiment
from labkit.project import load_project
from test_steps import STEP_3_HEAD, proj  # noqa: F401
from test_harness import BASELINE, LABKIT_YAML, PRICES, FakeSystem, fake_grade, fake_judge, project, write  # noqa: F401


# ---------------------------------------------------------------- F07: labkit.yaml max_score

@pytest.mark.parametrize("value", ['"2"', "null", "0", "-1", "true", ".nan", ".inf"])
def test_f07_max_score_must_be_a_positive_finite_number_and_is_checked_before_anything_is_paid_for(project, value):
    """`"2"` and null crashed grading with a TypeError after the judge call was paid for; 0, -1 and true made every
    judge answer invalid; `.nan` made every later command refuse forever (nan != nan)."""
    write(project / "labkit.yaml", LABKIT_YAML + f"max_score: {value}\n")
    with pytest.raises(ValueError, match="`max_score` must be a positive number"):
        load_project(project)
    system = FakeSystem()
    with pytest.raises(ValueError, match="max_score"):
        run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=system)
    assert system.scopes == []                                                     # not one question ran


@pytest.mark.parametrize("value, expected", [("5", 5), ("0.5", 0.5), ("2.0", 2.0), ("1", 1)])
def test_f07_ordinary_max_scores_are_still_accepted(project, value, expected):
    write(project / "labkit.yaml", LABKIT_YAML + f"max_score: {value}\n")
    assert load_project(project)["max_score"] == expected


# ---------------------------------------------------------------- F03: the judge prompt written another way

@pytest.mark.parametrize("spelling", ["absolute", "dotdot", "dots"])
def test_f03_a_judge_prompt_written_absolutely_or_through_the_parent_folder_is_still_checked(project, spelling):
    """The recorded hash is keyed by the project-relative name, but the check compared the config's own spelling with
    it, so `judge.prompt: C:/…/prompts/judge.md` graded with an edited prompt."""
    judge_md = project / "prompts" / "judge.md"
    written = {"absolute": judge_md.as_posix(), "dotdot": f"../{project.name}/prompts/judge.md",
               "dots": "prompts/../prompts/judge.md"}[spelling]
    write(judge_md, "Grade 0 to 2.\n")
    write(project / "configs" / "baseline.yaml", BASELINE.replace("prompts/judge.md", written))
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    write(judge_md, "Grade 0 to 5, generously.\n")
    with pytest.raises(ValueError, match=r"judge prompt prompts/judge.md changed since dev_x ran"):
        grade_experiment("dev_x", grade_fn=fake_grade)
    write(judge_md, "Grade 0 to 2.\n")                                                  # restored: grades again
    assert grade_experiment("dev_x", grade_fn=fake_grade)["graded_now"] == 2
    judge_md.unlink()
    with pytest.raises(ValueError, match="was deleted since dev_x ran"):
        grade_experiment("dev_x", suffix="__regrade1", grade_fn=fake_grade)


# ---------------------------------------------------------------- F04: names that differ only by case

def test_f04_an_experiment_named_like_another_ones_regrade_file_is_refused_in_any_case(project):
    """`dev_000_baseline` with suffix `__x` writes `dev_000_baseline__x.jsonl`, which is also `DEV_000_BASELINE__x.jsonl`
    on Windows and macOS: the second experiment's judge_usd counted the other one's judge calls."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    with pytest.raises(ValueError, match="would share the grades file of dev_000_baseline"):
        run_experiment(cfg, "dev", experiment="DEV_000_BASELINE__x", system=FakeSystem())
    with pytest.raises(ValueError, match="would share the grades file of dev_000_baseline"):
        run_experiment(cfg, "dev", experiment="Dev_000_Baseline__x", system=FakeSystem())


def test_f04_the_same_clash_is_refused_from_the_grading_side(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="DEV_X__regrader", system=FakeSystem())
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    with pytest.raises(ValueError, match="is an experiment of its own"):
        grade_experiment("dev_x", suffix="__regrader", grade_fn=fake_grade)
    assert not list((project / "out" / "grades").glob("*"))                          # nothing was written


# ---------------------------------------------------------------- F08: a result the log cannot store

class OddSystem(FakeSystem):
    """Answers the first question (POS001) with `patch` merged into its normal result."""

    def __init__(self, patch):
        super().__init__()
        self.patch = patch

    def run(self, question, scope):
        out = super().run(question, scope)
        if "휴면" in question:
            out.update(self.patch)
        return out


def test_f08_a_lone_surrogate_in_an_answer_is_replaced_not_fatal(project):
    """A half emoji (a token limit cut it) made SQLite's UTF-8 encoding raise after the call was paid for: zero rows
    saved, the whole batch lost."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    r = run_experiment(cfg, "dev", experiment="dev_x", system=OddSystem({"answer": "x" + chr(0xD83D) + "y"}))
    assert r["ran"] == 2 and r["statuses"] == {"answered": 1, "abstained": 1}
    answers = {row["qid"]: row["answer"] for row in RunLog(project / "out" / "runs.db").load("dev_x")}
    assert answers["POS001"] == "x�y"


@pytest.mark.parametrize("patch", [{"context": [{"chunk_id": "c1", "score": object()}]}, {"answer": {"not": "text"}}])
def test_f08_a_result_the_log_cannot_store_becomes_a_failed_question_that_keeps_its_cost(project, patch):
    """A numpy number in a context unit, or an answer that is not text, raised after the call was paid for and
    aborted the batch. It is a failed question now; the batch goes on and the call is still counted."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    r = run_experiment(cfg, "dev", experiment="dev_x", system=OddSystem(patch))
    assert r["ran"] == 2 and r["statuses"] == {"error": 1, "abstained": 1}
    rows = {row["qid"]: row for row in RunLog(project / "out" / "runs.db").load("dev_x")}
    bad = rows["POS001"]
    # the unit without source/page is refused by the result boundary (labkit.contract) before saving is tried
    expected = "cannot use" if "context" in patch else "could not be saved"
    assert bad["status"] == "error" and expected in bad["error"] and "fix the system" in bad["error"]
    assert [c["model"] for c in bad["steps"]["calls"]] == ["m"]                   # what the call cost is kept
    assert bad["input_tokens"] == 1000 and bad["config"]["selection"]["split"] == "dev"
    assert rows["NEG001"]["status"] == "abstained"
    again = run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())    # the failed one is retried, not skipped
    assert again["ran"] == 1 and again["skipped_done"] == 1


def test_f08_a_lone_surrogate_in_a_judge_reason_does_not_stop_grading(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x", grade_fn=lambda item, run, c: {**fake_grade(item, run, c), "reason": "bad" + chr(0xDC00)})
    rows = [json.loads(line) for line in (project / "out" / "grades" / "dev_x.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {row["reason"] for row in rows} == {"bad�"}


# ---------------------------------------------------------------- F09: qids that print as nothing

@pytest.mark.parametrize("code", [0x3164, 0xFFA0, 0x115F, 0x1160, 0x034F, 0xFE0F, 0xFE00, 0x2800, 0x180B, 0x17B4,
                                  0xE0100, 0x200B, 0xA0])
def test_f09_a_qid_with_a_character_that_prints_as_nothing_is_refused(tmp_path, code):
    """`Q1` with U+3164 (Hangul filler) in it is a second question that looks exactly like Q1. Only the Cf, Cc, Zl, Zp
    and Zs categories were refused; the fillers, the joiner and the variation selectors are Lo, Mn and So. (In the
    middle of the qid: a blank at either end is stripped before the check.)"""
    path = write(tmp_path / "g.jsonl", json.dumps({"qid": "Q" + chr(code) + "1", "split": "dev"}) + "\n")
    with pytest.raises(ValueError, match=rf"invisible character \(U\+{code:04X}\)"):
        read_golden_file(path)


@pytest.mark.parametrize("qid", ["Q1", "휴면-001", "신규_계좌.7", "가", "D 1", "문항１"])
def test_f09_ordinary_qids_with_korean_letters_or_a_plain_space_are_still_fine(tmp_path, qid):
    path = write(tmp_path / "g.jsonl", json.dumps({"qid": qid, "split": "dev", "question": "q?"}, ensure_ascii=False) + "\n")
    assert read_golden_file(path)[0]["qid"] == qid


# ---------------------------------------------------------------- F10: a misspelled auto tag

@pytest.mark.parametrize("line", ["- [ ] x <!-- auto : baseline_evaluated -->", "- [ ] x <!-- auto: a, b -->",
                                  "- [ ] x <!-- auto: baseline_evaluated --> (note)", "- [ ] x <!-- auto baseline_evaluated -->",
                                  "- [ ] x <!-- AUTO: baseline_evaluated --> <!-- more -->", "- [ ] x <!-- auto: -->"])
def test_f10_a_malformed_auto_tag_is_an_error_not_a_manual_criterion(proj, line):
    """These parsed as manual criteria whose text happened to contain a comment, so a typo turned a criterion checked
    by code into one that `labkit tick` could claim."""
    path = steps.step_file(proj, 3)
    path.write_text(path.read_text(encoding="utf-8").replace(STEP_3_HEAD, STEP_3_HEAD + "\n" + line + "\n", 1),
                    encoding="utf-8")
    with pytest.raises(ValueError, match=r"03-.*\.md:\d+: .* has an HTML comment that is not a trailing"):
        steps.criteria(proj, 3)
    assert main(["--root", str(proj), "check", "3"]) == 1


def test_f10_a_well_formed_tag_in_any_case_and_plain_criteria_still_parse(proj):
    path = steps.step_file(proj, 3)
    lines = "- [ ] plain\n- [x] tagged <!-- auto: noise_runs_exist -->\n- [ ] upper <!--   AUTO:   noise_runs_exist   -->\n"
    path.write_text(path.read_text(encoding="utf-8").replace(STEP_3_HEAD, STEP_3_HEAD + "\n" + lines, 1), encoding="utf-8")
    found = steps.criteria(proj, 3)
    assert [c.auto for c in found[:3]] == [None, "noise_runs_exist", "noise_runs_exist"]


# ---------------------------------------------------------------- F12: the reserved status word in another spelling

@pytest.mark.parametrize("status", ["Error", "ERROR", "error ", " eRRor"])
def test_f12_error_in_another_case_is_still_a_failed_question(project, status):
    """`error` is reserved, but `Error` was an answer word of the system's own: it was graded, scored and counted as a
    finished question, and the experiment came out `complete: true`."""
    class Failing(FakeSystem):
        def run(self, question, scope):
            return {**super().run(question, scope), "status": status}

    r = run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=Failing())
    assert r["statuses"] == {"error": 2}
    rows = RunLog(project / "out" / "runs.db").load("dev_x")
    assert {row["status"] for row in rows} == {"error"} and all(status in row["error"] for row in rows)
    assert RunLog(project / "out" / "runs.db").latest("dev_x") == []                  # none counts as finished
    assert all(row["steps"]["calls"] for row in rows)                                 # the paid calls are still on record


# ---------------------------------------------------------------- F11: config keys that are not text

@pytest.mark.parametrize("extra, where, kind", [
    ("extra: {2026-01-01: x}", "extra.2026-01-01", "date"),
    ("extra: {1: a, b: c}", "extra.1", "int"),
    ("extra: {yes: 1}", "extra.True", "bool"),
    ("extra: {on: 2}", "extra.True", "bool"),
    ("extra: {nested: {2: x}}", "extra.nested.2", "int"),
    ("extra: [{3: x}]", "extra[0].3", "int")])
def test_f11_a_config_key_that_is_not_text_is_refused_by_name_at_load(project, extra, where, kind):
    """A date key died with a bare TypeError inside config_hash, mixed number and text keys did too, and `yes:` quietly
    became the key True."""
    write(project / "configs" / "baseline.yaml", BASELINE + extra + "\n")
    with pytest.raises(ValueError, match=rf"baseline.yaml: `{re.escape(where)}` is a key \(.*, a {kind}\) that is not text"):
        load_config(project / "configs" / "baseline.yaml")


def test_f11_quoted_keys_and_a_top_level_key_load_fine(project):
    write(project / "configs" / "baseline.yaml", BASELINE + "extra: {'yes': 1, '1': 2, plain: 3}\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    assert cfg["extra"] == {"yes": 1, "1": 2, "plain": 3}
    write(project / "configs" / "baseline.yaml", BASELINE + "5: top\n")
    with pytest.raises(ValueError, match="`5` is a key"):
        load_config(project / "configs" / "baseline.yaml")


# ---------------------------------------------------------------- F13: --overwrite does not change the split of a result

def test_f13_overwrite_does_not_turn_an_unfinished_test_result_into_a_dev_one(project):
    """A teammate's results/final_try held an incomplete test result; a dev run under the same name (the run log is
    local, so nothing stopped it) was then evaluated with --overwrite, and the folder said `split: dev, complete: true`."""
    items = [{"qid": f"{split[0].upper()}{i}", "set": "base_positive", "split": split, "question": "휴면성 증권계좌는?",
              "answerable": True, "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}],
              "evidence": []} for split in ("dev", "test") for i in range(1, 6)]
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "test", experiment="final_try", limit=4, final=True, system=FakeSystem())
    grade_experiment("final_try", final=True)
    s = evaluate_experiment("final_try", final=True, prices=PRICES)
    assert (s["split"], s["complete"]) == ("test", False)
    (project / "out" / "runs.db").unlink()                                          # the teammate's machine has no log
    run_experiment(cfg, "dev", experiment="final_try", system=FakeSystem())
    grade_experiment("final_try")
    with pytest.raises(SplitLocked, match="holds a test result and this run is on the dev split"):
        evaluate_experiment("final_try", prices=PRICES, overwrite=True)
    saved = json.loads((project / "results" / "final_try" / "summary.json").read_text(encoding="utf-8"))
    assert (saved["split"], saved["complete"]) == ("test", False)                      # untouched


def test_f13_an_unfinished_result_may_still_be_finished_on_its_own_split(project):
    items = [{"qid": f"T{i}", "set": "base_positive", "split": "test", "question": "휴면성 증권계좌는?", "answerable": True,
              "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}], "evidence": []}
             for i in range(1, 6)]
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "test", experiment="final_try", limit=4, final=True, system=FakeSystem())
    grade_experiment("final_try", final=True)
    assert evaluate_experiment("final_try", final=True, prices=PRICES)["complete"] is False
    run_experiment(cfg, "test", experiment="final_try", final=True, system=FakeSystem())      # the missing question
    grade_experiment("final_try", final=True)
    assert evaluate_experiment("final_try", final=True, prices=PRICES, overwrite=True)["complete"] is True


# ---------------------------------------------------------------- F06: the report across two pricing files

class Pricier(FakeSystem):
    def run(self, question, scope):
        out = super().run(question, scope)
        out["steps"] = {}
        record_call(out["steps"], node="generate", model="m", input_tokens=5000, output_tokens=200)
        return out


def _row(project, experiment):
    """{column header: cell} of one experiment's row in results/SUMMARY.md."""
    lines = [ln for ln in (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines() if ln.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    row = next(ln for ln in lines[2:] if ln.split("|")[1].strip() == experiment)
    return dict(zip(header, [c.strip() for c in row.strip("|").split("|")])), row


def test_f06_a_price_change_is_not_shown_as_a_cheaper_or_dearer_system(project):
    """`labkit compare` warned when two results were costed from different pricing.yaml files; `labkit report` showed
    `0.00280 (+0.00140)` for a system that only cost more because the price doubled."""
    write(project / "configs" / "pricing.yaml", "models:\n  m: {input: 0.75, output: 3.75}\n  j: {input: 0.75, output: 3.75}\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    for name, system in (("dev_000_baseline", FakeSystem()), ("dev_r2", Pricier())):
        run_experiment(cfg, "dev", experiment=name, system=system)
        grade_experiment(name)
        evaluate_experiment(name, prices=PRICES)
    build_report()
    cells, row = _row(project, "dev_r2")
    assert "(+" in cells["USD/question"] and "pricing.yaml" not in row                  # same file: the difference shows

    write(project / "configs" / "pricing.yaml", "models:\n  m: {input: 1.5, output: 7.5}\n  j: {input: 0.75, output: 3.75}\n")
    evaluate_experiment("dev_r2", prices={**PRICES, "m": {"input": 1.5, "output": 7.5}}, overwrite=True)
    build_report()
    cells, row = _row(project, "dev_r2")
    assert "(" not in cells["USD/question"]                                               # no difference in dollars
    assert "priced from another pricing.yaml: no USD difference shown" in row
    assert "(+" in cells["Tokens/question"]                                               # tokens do not depend on prices
    assert "priced from another pricing.yaml" in (project / "out" / "report.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- F05: the worker count and the frozen baseline file

def test_f05_the_worker_count_is_set_in_a_child_config_because_the_baseline_file_itself_is_frozen(project):
    """configs/README said changing run.concurrency never blocks resuming. It does not change the config hash, but
    configs/baseline.yaml is an input file like any other: editing even this one key in it is refused."""
    items = [{"qid": f"P{i}", "set": "base_positive", "split": "dev", "question": "휴면성 증권계좌는?", "answerable": True,
              "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}], "evidence": []}
             for i in range(1, 6)]
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", limit=4, system=FakeSystem())
    write(project / "configs" / "slower.yaml", "base: baseline.yaml\nname: slower\nrun: {concurrency: 1}\n")
    slower = load_config(project / "configs" / "slower.yaml")
    assert slower["config_hash"] == cfg["config_hash"]                              # the value is not part of the hash
    assert run_experiment(slower, "dev", experiment="dev_000_baseline", system=FakeSystem())["ran"] == 1   # the child works

    write(project / "configs" / "baseline.yaml", BASELINE.replace("concurrency: 2", "concurrency: 4"))
    edited = load_config(project / "configs" / "baseline.yaml")
    assert edited["config_hash"] == cfg["config_hash"]
    with pytest.raises(ValueError, match=r"configs/baseline.yaml changed since dev_000_baseline started"):
        run_experiment(edited, "dev", experiment="dev_000_baseline", system=FakeSystem())     # the file itself is frozen
