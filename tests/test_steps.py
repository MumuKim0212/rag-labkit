"""labkit status / check / tick / approve / revoke against a copy of the template, plus the auto checks."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from labkit import steps
from labkit.autochecks import AUTOCHECKS
from labkit.cli import main
from labkit.env import TRACING_VARS
from labkit.eval.runlog import RunLog

TEMPLATE = Path(__file__).resolve().parents[1] / "labkit" / "template"
GOLDEN = [{"qid": "Q1", "set": "s", "split": "dev", "question": "q?", "answerable": True},
          {"qid": "Q2", "set": "s", "split": "test", "question": "q2?", "answerable": True}]


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_golden(root, items=GOLDEN):
    write(root / "golden" / "golden.jsonl", "".join(json.dumps(i) + "\n" for i in items))


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=tester", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    for var in TRACING_VARS:
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))   # a temp folder inside a work tree is not a repo
    root = tmp_path / "proj"
    shutil.copytree(TEMPLATE, root)
    monkeypatch.chdir(root)
    return root


def manual(root, n):
    return [i.index for i in steps.criteria(root, n) if not i.auto]


def auto(root, n):
    return [i.index for i in steps.criteria(root, n) if i.auto]


# ---------------------------------------------------------------- the template's own step files

def test_every_template_step_parses_and_its_auto_tags_are_registered():
    for n in range(1, steps.STEP_COUNT + 1):
        items = steps.criteria(TEMPLATE, n)
        assert items, f"step {n} has no exit criteria"
        assert [i.index for i in items] == list(range(1, len(items) + 1))
        assert all("<!--" not in i.text for i in items)               # the tag is stripped from the displayed text
    tags = {i.auto for n in range(1, 9) for i in steps.criteria(TEMPLATE, n) if i.auto}
    assert tags == {"split_and_test_untouched", "judge_differs_from_answer", "baseline_evaluated",
                    "baseline_metrics_saved", "baseline_config_committed", "noise_runs_exist", "judge_prompt_committed"}
    assert tags <= set(AUTOCHECKS)


def test_unknown_auto_tag_is_rejected(proj):
    path = next((proj / "docs" / "steps").glob("01-*.md"))
    path.write_text(path.read_text(encoding="utf-8") + "- [ ] extra <!-- auto: nope -->\n", encoding="utf-8")
    with pytest.raises(KeyError, match="names auto check 'nope'"):
        steps.criteria(proj, 1)


# ---------------------------------------------------------------- the flow through the CLI

def test_a_step_is_approved_only_when_its_criteria_hold(proj, capsys):
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "CURRENT" in out and "docs/steps/01-corpus-golden.md" in out and out.count("waiting") == 7

    assert main(["check"]) == 1                                       # nothing holds yet
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "golden.jsonl does not exist" in out and "[OPEN]" in out
    assert main(["approve", "1", "--by", "ana"]) == 1
    assert "cannot be approved" in capsys.readouterr().err

    write_golden(proj)
    assert main(["check", "1"]) == 1
    out = capsys.readouterr().out
    assert "[ok  (code)] dev/test split fixed" in out and "1 dev / 1 test items" in out
    first_manual = manual(proj, 1)[0]
    assert f"labkit tick 1 {first_manual}" in out                      # tells the agent how to claim it

    assert main(["tick", "1", str(auto(proj, 1)[0]), "--evidence", "x", "--by", "ana"]) == 1
    assert "checked by code" in capsys.readouterr().err
    assert main(["tick", "1", str(first_manual), "--evidence", "  ", "--by", "ana"]) == 1
    assert "--evidence is required" in capsys.readouterr().err
    assert main(["tick", "1", "99", "--evidence", "x", "--by", "ana"]) == 1
    assert "criteria 1.." in capsys.readouterr().err
    for i in manual(proj, 1):
        assert main(["tick", "1", str(i), "--evidence", f"PROCESS §1.{i}: 확인함", "--by", "agent-1"]) == 0
    capsys.readouterr()

    assert main(["check", "1"]) == 1                                   # the template's placeholder models are no choice
    assert "still a template placeholder" in capsys.readouterr().out
    choose_models(proj)
    no_judge_prompt(proj)                                              # the prompt criterion needs git; see test_round7
    assert main(["check", "1"]) == 0
    out = capsys.readouterr().out
    assert "All criteria hold" in out and "labkit approve 1" in out and "agent-1" in out and "확인함" in out
    assert main(["approve", "2", "--by", "ana"]) == 1                 # steps are approved in order
    assert "step 1 is not approved yet" in capsys.readouterr().err
    assert main(["approve", "1", "--by", "ana", "--note", "golden set reviewed"]) == 0
    assert main(["approve", "1", "--by", "ana"]) == 1                 # already approved
    capsys.readouterr()

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "approved by ana" in out and "docs/steps/02-baseline.md" in out
    assert main(["check", "1"]) == 0 and "already approved by ana" in capsys.readouterr().out

    state = (proj / "report" / "state.yaml").read_text(encoding="utf-8")
    assert "확인함" in state and "golden set reviewed" in state          # evidence and notes are kept, UTF-8
    assert [e["action"] for e in steps.load_state(proj)["log"]] == ["tick"] * len(manual(proj, 1)) + ["approve"]


def test_a_claim_is_tied_to_the_wording_of_the_criterion(proj):
    target = next(i for i in steps.criteria(proj, 1) if i.text.startswith("Extraction issues found"))
    steps.tick(proj, 1, target.index, "done", "ana")
    assert next(r for r in steps.evaluate(proj, 1) if r.criterion.index == target.index).ok
    path = next((proj / "docs" / "steps").glob("01-*.md"))
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("Extraction issues found", "Extraction issues found and reviewed"), encoding="utf-8")
    stale = next(r for r in steps.evaluate(proj, 1) if r.criterion.index == target.index)
    assert stale.status == "stale" and not stale.ok and "wording changed" in stale.detail


@pytest.mark.needs_git
def test_ticking_ahead_is_refused_and_the_actor_is_named(proj, monkeypatch):
    with pytest.raises(ValueError, match="not open yet"):
        steps.tick(proj, 3, manual(proj, 3)[0], "evidence", "ana")
    first = manual(proj, 1)[0]
    monkeypatch.setenv("HOME", str(proj))                            # no git identity anywhere
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(proj / "nogit"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(proj / "nogit"))
    with pytest.raises(ValueError, match="pass --by"):
        steps.tick(proj, 1, first, "evidence")
    git(proj, "init", "-b", "main")
    subprocess.run(["git", "config", "user.name", "from-git"], cwd=proj, check=True)
    steps.tick(proj, 1, first, "evidence")                           # falls back to git config user.name
    assert steps.load_state(proj)["steps"][1]["criteria"][first]["by"] == "from-git"


def test_optional_step_can_be_skipped_with_a_reason(proj):
    def approve_through(n):
        state = steps.load_state(proj)
        for m in range(1, n + 1):
            state["steps"][m] = {"approved": {"by": "ana", "at": "2026-11-01T00:00:00", "note": ""}}
        steps.save_state(proj, state)

    approve_through(5)
    with pytest.raises(ValueError, match="cannot be skipped"):
        steps.approve(proj, 6, "ana", "n/a", skip=True)
    with pytest.raises(ValueError, match="step 6 is not approved yet"):    # 7 cannot jump over 6
        steps.approve(proj, 7, "ana", "n/a", skip=True)
    approve_through(6)
    with pytest.raises(ValueError, match="--note is required"):
        steps.approve(proj, 7, "ana", "", skip=True)
    assert "skipped" in steps.approve(proj, 7, "ana", "triage found nothing structure could fix", skip=True)
    assert steps.current_step(steps.load_state(proj)) == 8
    assert "skipped by ana" in steps.format_status(proj)


def test_revoke_only_the_latest_approval_and_claims_are_cleared(proj):
    state = steps.load_state(proj)
    for n in (1, 2):
        state["steps"][n] = {"approved": {"by": "ana", "at": "2026-11-01T00:00:00", "note": ""}}
    steps.save_state(proj, state)
    with pytest.raises(ValueError, match="step 2 is approved and builds on step 1"):
        steps.revoke(proj, 1, "wrong", "ana")
    with pytest.raises(ValueError, match="--reason is required"):
        steps.revoke(proj, 2, " ", "ana")
    with pytest.raises(ValueError, match="not approved"):
        steps.revoke(proj, 3, "x", "ana")
    steps.revoke(proj, 2, "baseline config was edited after the run", "ana")
    state = steps.load_state(proj)
    assert steps.current_step(state) == 2 and state["steps"][2] == {}
    assert state["log"][-1]["action"] == "revoke" and "edited" in state["log"][-1]["detail"]


# ---------------------------------------------------------------- the automatic checks

def test_split_and_test_untouched(proj):
    project = {}
    ok, detail = AUTOCHECKS["split_and_test_untouched"](proj, project)
    assert not ok and "golden.jsonl does not exist" in detail
    write_golden(proj, GOLDEN[:1])
    assert "both dev and test" in AUTOCHECKS["split_and_test_untouched"](proj, project)[1]
    write_golden(proj)
    assert AUTOCHECKS["split_and_test_untouched"](proj, project)[0]
    RunLog(proj / "out" / "runs.db").save({"status": "answered"}, experiment="test_000_baseline", qid="Q2")
    ok, detail = AUTOCHECKS["split_and_test_untouched"](proj, project)
    assert not ok and "test_000_baseline" in detail                 # a test run in the local log
    (proj / "out" / "runs.db").unlink()
    (proj / "results" / "test_000_baseline").mkdir(parents=True)
    assert not AUTOCHECKS["split_and_test_untouched"](proj, project)[0]   # or committed test results


def test_step_1_does_not_pass_while_a_golden_item_has_no_valid_split(proj):
    """It used to say '5 dev / 1 test items' and ignore the sixth, whose split was misspelled."""
    write_golden(proj, [*GOLDEN, {"qid": "Q3", "set": "s", "split": "Test", "question": "q3?", "answerable": True}])
    ok, detail = AUTOCHECKS["split_and_test_untouched"](proj, {})
    assert not ok and "Q3" in detail and '"dev" or "test"' in detail


def test_a_test_run_is_found_by_its_recorded_split_not_its_name(proj):
    check = AUTOCHECKS["split_and_test_untouched"]
    write_golden(proj)
    selection = {"selection": {"split": "test", "sets": None}}
    RunLog(proj / "out" / "runs.db").save({"status": "answered", "config": selection}, experiment="final_try", qid="Q2")
    ok, detail = check(proj, {})
    assert not ok and "final_try" in detail                         # the name says nothing; the record does
    (proj / "out" / "runs.db").unlink()
    folder = proj / "results" / "scratch"
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"split": "test"}), encoding="utf-8")
    ok, detail = check(proj, {})
    assert not ok and "scratch" in detail                           # a results folder whose summary says test


def no_judge_prompt(root):
    """A judge that uses no prompt file: the judge_prompt_committed check passes without git."""
    base = root / "configs" / "baseline.yaml"
    base.write_text(base.read_text(encoding="utf-8").replace("  prompt: prompts/judge_v1.md\n", ""), encoding="utf-8")


def choose_models(root, answer="model-a-001", judge="model-j-001"):
    """Replace the template's placeholder model names, as a project does in step 1."""
    base = root / "configs" / "baseline.yaml"
    base.write_text(base.read_text(encoding="utf-8").replace("example-answer-model", answer)
                    .replace("example-judge-model", judge), encoding="utf-8")


def test_judge_must_differ_from_the_answer_model_in_every_config(proj):
    check = AUTOCHECKS["judge_differs_from_answer"]
    ok, detail = check(proj, {})                                    # the untouched template: two placeholder words
    assert not ok and "baseline.yaml: answer.model or judge.model is still a template placeholder" in detail
    choose_models(proj)
    assert check(proj, {})[0]
    base = proj / "configs" / "baseline.yaml"
    write(proj / "configs" / "010-same.yaml", "base: baseline.yaml\nname: 010\nanswer: {model: model-j-001}\n")
    ok, detail = check(proj, {})
    assert not ok and "010-same.yaml: judge and answer both use model-j-001" in detail
    (proj / "configs" / "010-same.yaml").unlink()
    base.write_text(base.read_text(encoding="utf-8").replace("answer:\n  model: model-a-001\n", "answer:\n"),
                    encoding="utf-8")
    ok, detail = check(proj, {})
    assert not ok and "baseline.yaml: set answer.model and judge.model" in detail


def test_a_config_that_cannot_be_loaded_is_a_failed_check_not_a_crash(proj):
    choose_models(proj)
    write(proj / "configs" / "010-smoke.yaml", "base: baseline.yaml\nname: 010\nsmoke: true\n")
    write(proj / "configs" / "011-broken.yaml", "base: baseline.yaml\nname: [unclosed\n")
    ok, detail = AUTOCHECKS["judge_differs_from_answer"](proj, {})
    assert not ok and "010-smoke.yaml" in detail and "`smoke` is a key labkit writes itself" in detail
    assert "011-broken.yaml" in detail


# what a summary records about WHAT was run: the baseline config on the whole dev split, answers and all
IS_THE_BASELINE = {"split": "dev", "changed": {}, "config_file": "configs/baseline.yaml", "sets": None,
                   "retrieval_only": False}


@pytest.mark.parametrize("fields, words", [
    ({"sets": ["a"]}, "only the sets"),                                      # --sets a --name dev_000_baseline: half of dev
    ({"changed": {"retrieval.k": {"from": 5, "to": 7}}}, "changes retrieval.k"),   # a variant under the baseline's name
    ({"config_file": "configs/010-x.yaml"}, "configs/010-x.yaml"),
    ({"split": "test"}, "split"),
    ({"retrieval_only": True}, "retrieval-only"),
])
def test_a_summary_named_dev_000_baseline_that_is_not_the_baseline_does_not_pass(proj, fields, words):
    """The check passed for a --sets subset (complete: true, 3 of 3) and for a variant run under the baseline's name."""
    write(proj / "results" / "dev_000_baseline" / "summary.json", json.dumps(
        {"n_questions": 3, "n_expected": 3, "n_graded": 3, "complete": True, "incomplete": [], **{**IS_THE_BASELINE, **fields}}))
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, {})
    assert not ok and words in detail


def test_a_summary_with_no_record_of_what_was_run_must_be_evaluated_again(proj):
    write(proj / "results" / "dev_000_baseline" / "summary.json", json.dumps(
        {"n_questions": 3, "n_expected": 3, "n_graded": 3, "complete": True, "incomplete": []}))
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, {})
    assert not ok and "no record of which config" in detail and "--overwrite" in detail


def test_step_1_and_labkit_run_agree_about_the_same_model(proj):
    """`" Ans-Model "` against `ans-model`: the step-1 check said they differ while labkit run refused them as equal."""
    choose_models(proj, answer="Model-A-001", judge='" model-a-001 "')
    ok, detail = AUTOCHECKS["judge_differs_from_answer"](proj, {})
    assert not ok and "judge and answer both use" in detail


def test_the_auto_tag_of_a_criterion_is_not_case_sensitive(proj):
    """`<!-- AUTO: x -->` quietly turned a code-checked criterion into one that could be ticked with any evidence."""
    path = steps.step_file(proj, 1)
    before = [(c.index, c.auto) for c in steps.criteria(proj, 1)]
    assert any(auto for _, auto in before)
    path.write_text(path.read_text(encoding="utf-8").replace("<!-- auto:", "<!-- AUTO:"), encoding="utf-8")
    assert [(c.index, c.auto) for c in steps.criteria(proj, 1)] == before


def test_baseline_checks(proj):
    project = {}
    assert not AUTOCHECKS["baseline_evaluated"](proj, project)[0]
    folder = proj / "results" / "dev_000_baseline"
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"n_questions": 2, "n_graded": 0}), encoding="utf-8")
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, project)
    assert not ok and "no graded answers" in detail
    partial = {"n_questions": 3, "n_expected": 10, "n_graded": 3, "complete": False,
               "incomplete": ["7 of 10 expected questions never ran"]}   # 3 questions scoring 1.0 is not a baseline
    (folder / "summary.json").write_text(json.dumps(partial), encoding="utf-8")
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, project)
    assert not ok and "incomplete" in detail and "7 of 10 expected questions never ran" in detail
    (folder / "summary.json").write_text(json.dumps({"n_questions": 3, "n_graded": 3}), encoding="utf-8")
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, project)   # a summary from before the completeness record
    assert not ok and "no completeness record" in detail
    done = {"n_questions": 2, "n_expected": 2, "n_graded": 2, "complete": True, "incomplete": [], **IS_THE_BASELINE}
    (folder / "summary.json").write_text(json.dumps(done), encoding="utf-8")
    assert AUTOCHECKS["baseline_evaluated"](proj, project) == (True, "dev_000_baseline: 2 of 2 questions, 2 graded")
    # the files the baseline ran on must still be what they were: checked now, not as the summary saw them
    from labkit.freeze import file_hash
    right = {"configs/baseline.yaml": file_hash(proj / "configs" / "baseline.yaml")}
    (folder / "summary.json").write_text(json.dumps({**done, "file_hashes": right}), encoding="utf-8")
    assert AUTOCHECKS["baseline_evaluated"](proj, project)[0]
    (folder / "summary.json").write_text(json.dumps({**done, "file_hashes": {"configs/baseline.yaml": "deadbeef00"}}),
                                         encoding="utf-8")
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, project)
    assert not ok and "configs/baseline.yaml changed since" in detail
    (folder / "summary.json").write_text(json.dumps(done), encoding="utf-8")
    # and the labkit.yaml settings it was scored under (max_score, golden list, behaviors) must still be today's
    from labkit.project import load_project, scoring_settings
    now = scoring_settings(load_project(proj))
    (folder / "summary.json").write_text(json.dumps({**done, "project_settings": now}), encoding="utf-8")
    assert AUTOCHECKS["baseline_evaluated"](proj, project)[0]
    (folder / "summary.json").write_text(json.dumps({**done, "project_settings": {**now, "max_score": 99}}),
                                         encoding="utf-8")
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, project)
    assert not ok and "max_score: 99 -> 2" in detail and "evaluate" in detail
    (folder / "summary.json").write_text(json.dumps(done), encoding="utf-8")

    assert not AUTOCHECKS["baseline_metrics_saved"](proj, project)[0]
    (folder / "metrics.jsonl").write_text('{"qid": "Q1"}\n\n{"qid": "Q2"}\n', encoding="utf-8")
    assert AUTOCHECKS["baseline_metrics_saved"](proj, project) == (True, "2 per-question rows")


@pytest.mark.needs_git
def test_baseline_config_must_be_committed_and_clean(proj, monkeypatch):
    check = AUTOCHECKS["baseline_config_committed"]
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(proj.parent))   # a temp folder inside a work tree is not a repo
    assert "not a git repository" in check(proj, {})[1]
    git(proj, "init")
    assert "not tracked" in check(proj, {})[1]
    git(proj, "add", ".")
    git(proj, "commit", "-m", "baseline")
    assert check(proj, {})[0]
    base = proj / "configs" / "baseline.yaml"
    base.write_text(base.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    ok, detail = check(proj, {})
    assert not ok and "uncommitted changes" in detail


def approve_through(root, n):
    """Mark steps 1..n approved without going through the checks (these tests are about the gates after that)."""
    state = steps.load_state(root)
    for m in range(1, n + 1):
        state["steps"][m] = {"approved": {"by": "ana", "at": "2026-11-01T00:00:00", "note": ""}}
    steps.save_state(root, state)


def test_step_8_cannot_be_skipped(proj):
    approve_through(proj, 7)
    with pytest.raises(ValueError, match="cannot be skipped"):
        steps.approve(proj, 8, "ana", note="why", skip=True)


def test_the_step_after_the_current_one_is_not_open_for_ticking(proj):
    with pytest.raises(ValueError, match="not open yet"):
        steps.tick(proj, 2, manual(proj, 2)[0], "evidence", "ana")


def test_revoking_withdraws_the_claims_and_a_skipped_step_is_no_longer_done(proj):
    first = manual(proj, 1)[0]
    steps.tick(proj, 1, first, "evidence", "ana")
    state = steps.load_state(proj)
    state["steps"][1]["approved"] = {"by": "ana", "at": "2026-11-01T00:00:00", "note": ""}
    steps.save_state(proj, state)
    steps.revoke(proj, 1, "wrong", "ana")
    assert [r.status for r in steps.evaluate(proj, 1) if r.criterion.index == first] == ["open"]   # the claim went too
    approve_through(proj, 6)
    steps.approve(proj, 7, "ana", note="no change", skip=True)
    steps.revoke(proj, 7, "needed", "ana")
    assert not steps.is_done(steps.load_state(proj), 7)


def test_a_step_number_outside_1_to_8_is_a_clear_error(proj):
    with pytest.raises(ValueError, match="steps are numbered 1..8"):
        steps.step_file(proj, 9)


BASE = {"split": "dev", "config_hash": "abc123def0", "n_questions": 4, "n_expected": 4, "n_graded": 4, "complete": True,
        "incomplete": [], "changed": {}, "config_file": "configs/baseline.yaml", "sets": None, "retrieval_only": False,
        "file_hashes": {"configs/baseline.yaml": "aaaaaaaaaa", "prompts/answer_v1.md": "bbbbbbbbbb"}}


def noise_setup(proj, baseline=BASE):
    write(proj / "results" / "dev_000_baseline" / "summary.json", json.dumps(baseline))


def write_summary(proj, name, **fields):
    write(proj / "results" / name / "summary.json", json.dumps({**BASE, **fields}))


def test_noise_needs_both_a_rerun_and_a_regrade(proj):
    check = AUTOCHECKS["noise_runs_exist"]
    ok, detail = check(proj, {})
    assert not ok and "baseline" in detail and "evaluated" in detail         # nothing to repeat yet
    noise_setup(proj)
    ok, detail = check(proj, {})
    assert not ok and "(a)" in detail and "(b)" in detail
    write_summary(proj, "dev_000_baseline_r2")
    ok, detail = check(proj, {})
    assert not ok and "(a)" not in detail and "(b)" in detail
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", '{"qid": "1"}\n' * 4)
    assert check(proj, {}) == (True, "rerun dev_000_baseline_r2, regrade dev_000_baseline__regrade1.jsonl")


@pytest.mark.parametrize("fields, why", [
    ({"config_hash": "ffffffffff"}, "another config"),                       # a different setup is not the baseline's noise
    ({"n_questions": 1, "n_graded": 1, "n_expected": 4, "complete": False,
      "incomplete": ["3 of 4 expected questions never ran"]}, "a subset rerun"),
    ({"complete": False, "incomplete": ["1 questions failed on every attempt: Q3"]}, "a rerun with a failure"),
    ({"split": "test"}, "another split"),
    ({"sets": ["b"]}, "another subset of questions"),                       # --sets b against a baseline on --sets a
    ({"file_hashes": {"configs/baseline.yaml": "aaaaaaaaaa", "prompts/answer_v1.md": "cccccccccc"}},
     "a prompt that differed during the rerun, edited back before evaluating"),
    ({"config_file": "configs/010-x.yaml"}, "another config file with the same hash"),
])
def test_a_rerun_that_does_not_repeat_the_baseline_is_not_noise(proj, fields, why):
    noise_setup(proj)
    write_summary(proj, "dev_000_baseline_r2", **fields)
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", '{"qid": "1"}\n' * 4)
    ok, detail = AUTOCHECKS["noise_runs_exist"](proj, {})
    assert not ok and "(a)" in detail, why


def test_a_retrieval_only_run_or_a_stray_name_is_not_a_rerun(proj):
    noise_setup(proj)
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", '{"qid": "1"}\n' * 4)
    write_summary(proj, "dev_000_baseline_retrieval")
    write_summary(proj, "dev_000_baseline_r2_retrieval")
    assert not AUTOCHECKS["noise_runs_exist"](proj, {})[0]


@pytest.mark.parametrize("rows, ok", [("", False), ("\n\n", False), ('{"qid": "1"}\n' * 3, False),
                                      ('{"qid": "1"}\n' * 4, True)])
def test_a_regrade_must_grade_every_answer_the_baseline_graded(proj, rows, ok):
    """A judge that raised on its first call left a 0-byte file, and that satisfied the check."""
    noise_setup(proj)
    write_summary(proj, "dev_000_baseline_r2")
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", rows)
    assert AUTOCHECKS["noise_runs_exist"](proj, {})[0] is ok


# ---------------------------------------------------------------- a step file whose criteria do not parse

STEP_3_HEAD = "## Exit criteria\n"


@pytest.mark.parametrize("line", ["- []", "- [ ]Both parts", "* [ ] a star bullet", "+ [x] a plus bullet",
                                  "- [x]no space", "-[ ] no space before", "1. [ ] numbered", "2) [x] numbered, paren",
                                  "> - [ ] quoted", "  > * [ ] quoted and starred"])
def test_a_criterion_line_that_does_not_parse_is_an_error_not_a_dropped_criterion(proj, line):
    """Three malformed lines once printed 'All criteria hold' and the step was approved with nothing checked."""
    path = steps.step_file(proj, 3)
    path.write_text(path.read_text(encoding="utf-8").replace(STEP_3_HEAD, STEP_3_HEAD + "\n" + line + "\n", 1),
                    encoding="utf-8")
    with pytest.raises(ValueError, match=r"03-.*\.md:\d+: .* does not parse as a criterion"):
        steps.criteria(proj, 3)
    assert main(["--root", str(proj), "check", "3"]) == 1                   # a one-line error, not "all criteria hold"


def test_an_unreadable_summary_fails_a_check_it_does_not_crash_it(proj):
    write(proj / "results" / "dev_000_baseline" / "summary.json", "")
    ok, detail = AUTOCHECKS["baseline_evaluated"](proj, {})
    assert not ok and "not a readable summary" in detail
    (proj / "results" / "dev_000_baseline" / "summary.json").unlink()
    write_golden(proj)
    write(proj / "results" / "scratch" / "summary.json", "{ cut")
    ok, detail = AUTOCHECKS["split_and_test_untouched"](proj, {})
    assert not ok and "scratch" in detail and "not a readable summary" in detail


def test_a_step_with_no_criteria_cannot_be_approved_on_nothing(proj):
    path = steps.step_file(proj, 4)
    head = path.read_text(encoding="utf-8").split(STEP_3_HEAD, 1)[0]
    path.write_text(head + STEP_3_HEAD + "\nNothing is listed here.\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"has no criteria under '## Exit criteria'"):
        steps.criteria(proj, 4)
