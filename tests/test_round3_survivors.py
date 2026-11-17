"""Tests for the mutants the third mutation review found alive (the fourth review round).

A reviewer planted 234 one-line bugs in labkit (at commit d6f3fbf, v0.1.1); 51 survived the suite, one of them
equivalent. These tests were written by the reviewer against that commit, each checked to pass on the real code and to
fail on its own mutant; they are adapted to the code as it is now (the test name carries the mutant number). They pin
what earlier tests exercised on one side only: the second spelling of a path, a cwd that differs from the project
root, the other half of a pair of checks, the edge of a limit, and bytes that only differ on Windows.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import without_small_n
import yaml

from labkit import cards, steps
from labkit.autochecks import AUTOCHECKS
from labkit.cards import edited_after, same_card
from labkit.cli import main
from labkit.compare import compare_experiments
from labkit.config import load_config, settings
from labkit.eval import load_summary, runlog
from labkit.eval.metrics import squash
from labkit.eval.runlog import RunLog
from labkit.freeze import file_hash, referenced_files
from labkit.golden_check import check_items
from labkit.harness import (SplitLocked, _finite, _require_unfinished, _selected_sets, build_report,
                            evaluate_experiment, grade_experiment, live_flags, run_experiment)
from labkit.paths import check_experiment_name
from labkit.project import load_project
from labkit.repro import TRACKED, code_folders, environment, git_info
from labkit.scaffold import check_branch_name, init_project, new_card
from test_boundaries import two_results
from test_cli import CARD, CARD_FILE, HEADER, card_use, variant
from test_freeze import BASELINE as FREEZE_BASELINE
from test_freeze import baseline as freeze_baseline
from test_freeze import make_dir_link, proj  # noqa: F401
from test_harness import BASELINE, FakeSystem, fake_judge, graded_and_evaluated, project, summary_of, write  # noqa: F401
from test_steps import IS_THE_BASELINE
from test_survivors import git_in, repo  # noqa: F401

TEMPLATE = Path(__file__).resolve().parents[1] / "labkit" / "template"


# ---------------------------------------------------------------- names, JSON safety, sets

def test_m004_a_name_of_exactly_80_characters_is_fine_and_81_is_not():
    assert check_experiment_name("a" * 80) == "a" * 80
    with pytest.raises(ValueError):
        check_experiment_name("a" * 81)


@pytest.mark.parametrize("name", ["com9", "lpt9", "COM9", "com9.txt"])
def test_m007_every_windows_device_name_is_refused(name):
    with pytest.raises(ValueError):
        check_experiment_name(name)


def test_m018_m020_finite_cleans_infinity_and_lists_and_dicts_and_tuples():
    nan, inf = float("nan"), float("inf")
    assert _finite({"a": inf, "b": [nan, {"c": -inf}], "d": (inf,)}) == {"a": None, "b": [None, {"c": None}], "d": [None]}


def test_m025_a_non_finite_number_in_a_call_record_stops_the_summary_and_writes_none(project, monkeypatch):
    # since the sixth review round the run itself turns such a result into a failed question (tests/test_round6_findings.py,
    # f03); switched off here, because this test is about the net behind it: evaluate must still refuse a NaN
    monkeypatch.setattr("labkit.harness.cost.steps_problem", lambda steps: None)

    class NanSystem(FakeSystem):
        def run(self, question, scope):
            out = super().run(question, scope)
            out["steps"]["calls"][0]["input_tokens"] = float("nan")
            return out
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=NanSystem())
    grade_experiment("dev_x")
    with pytest.raises(ValueError, match="NaN or infinity"):
        evaluate_experiment("dev_x", prices={"m": {"input": 1, "output": 1}, "j": {"input": 1, "output": 1}})
    assert not (project / "results" / "dev_x" / "summary.json").exists()


def test_m030_the_sets_of_every_run_are_united_not_only_the_first_ones():
    rows = [{"config": {"selection": {"sets": ["a"]}}}, {"config": {"selection": {"sets": ["b"]}}}]
    assert _selected_sets(rows) == {"a", "b"}


def test_m079_a_smoke_test_on_other_sets_does_not_change_what_the_summary_says_it_covers(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", sets=["base_negative"], limit=1, system=FakeSystem())
    run_experiment(cfg, "dev", experiment="dev_x", sets=["base_positive"], system=FakeSystem())
    assert graded_and_evaluated("dev_x")["sets"] == ["base_positive"]


# ---------------------------------------------------------------- the judge, the overwrite guard, cwd versus root

def test_m037_a_judge_model_with_blanks_and_capitals_is_the_answer_model(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    cfg["answer"] = {"model": " Model-A "}
    cfg["judge"] = {**cfg["judge"], "model": "model-a"}
    with pytest.raises(ValueError, match="judge and answer both use"):
        run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())


def test_m041_a_card_warning_goes_to_the_callback_or_to_the_result_never_both(project):
    variant(project, HEADER.replace("configs/004-x.yaml", "configs/001-alpha.yaml") + CARD[len(HEADER):])
    seen = []
    result = run_experiment(load_config(project / "configs" / "004-x.yaml", root=project), "dev", experiment="dev_x",
                            system=FakeSystem(), warn=seen.append, root=project)
    assert len(seen) == 1 and "warnings" not in result


def test_m050_a_judge_prompt_written_with_a_leading_dot_is_still_the_same_file(project):
    write(project / "prompts" / "judge.md", "grade strictly\n")
    write(project / "configs" / "baseline.yaml", BASELINE.replace("prompts/judge.md", "./prompts/judge.md"))
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    write(project / "prompts" / "judge.md", "grade leniently\n")
    with pytest.raises(ValueError, match="judge prompt"):
        grade_experiment("dev_x")


def test_m051_the_judge_prompt_is_read_from_the_project_not_the_current_folder(project, tmp_path_factory, monkeypatch):
    write(project / "prompts" / "judge.md", "grade strictly\n")
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert grade_experiment("dev_x", root=project)["graded_now"] == 2


def test_m067_a_summary_that_cannot_even_be_opened_as_a_file_counts_as_not_unfinished(tmp_path):
    summary = tmp_path / "summary.json"
    summary.mkdir()
    with pytest.raises(SplitLocked):
        _require_unfinished(summary, "test")


# ---------------------------------------------------------------- the card history

@pytest.mark.parametrize("a, b", [("./experiments/004-x.md", "experiments/004-x.md"),
                                  ("experiments\\004-x.md", "experiments/004-x.md"),
                                  ("Experiments/004-X.md", "experiments/004-x.md")])
def test_m120_m121_m122_one_card_under_any_spelling(a, b):
    assert same_card(a, b) and same_card(b, a)


def test_m101_a_log_record_of_the_card_under_another_spelling_is_still_the_oldest(tmp_path):
    from labkit.harness import _card_check
    RunLog(tmp_path / "runs.db").save({"status": "answered", "config": {"card_check": {
        "file": "./experiments/004-x.md", "sections_hash": "OLD", "at": "2026-01-01T08:00:00+00:00"}}},
        experiment="dev_old", qid="Q1")
    card_use("2026-01-01T10:00:00+00:00", "dev_new", "NEW", tmp_path)
    own = {"file": CARD_FILE, "sections_hash": "NEW", "at": "2026-01-01T10:00:00+00:00"}
    check = _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)
    assert check["first_run_experiment"] == "dev_old" and check["hash_at_first_run"] == "OLD"


def test_m102_a_committed_summary_under_another_spelling_is_still_the_oldest(tmp_path):
    from labkit.harness import _card_check
    card_use("2026-01-01T10:00:00+00:00", "dev_new", "NEW", tmp_path)
    check = {"file": ".\\experiments\\004-x.md", "hash_at_first_run": "OLD", "first_run_at": "2026-01-01T08:00:00+00:00"}
    write(tmp_path / "results" / "dev_old" / "summary.json", json.dumps({"experiment": "dev_old", "card_check": check}))
    own = {"file": CARD_FILE, "sections_hash": "NEW", "at": "2026-01-01T10:00:00+00:00"}
    assert _card_check(own, RunLog(tmp_path / "runs.db"), tmp_path / "results", tmp_path)["first_run_experiment"] == "dev_old"


def test_m106_a_deleted_card_is_flagged_by_the_report(tmp_path):
    assert live_flags({"card_check": {"file": "experiments/gone.md", "hash_at_first_run": "abc"}}, tmp_path) == [
        "card sections 1–4 edited since it was first used"]


def test_m107_the_card_is_read_from_the_project_not_the_current_folder(tmp_path, tmp_path_factory, monkeypatch):
    card = write(tmp_path / "experiments" / "c.md", CARD)
    summary = {"card_check": {"file": "experiments/c.md", "hash_at_first_run": cards.sections_hash(card)}}
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert live_flags(summary, tmp_path) == []


def test_m124_a_recorded_time_without_a_zone_is_read_as_local_time():
    """In a subprocess with a UTC+9 clock: in this process the answer would depend on the machine's own zone."""
    code = "from labkit.cards import _instant; print(_instant('2026-01-01T12:00:00').utcoffset())"
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                          capture_output=True, text=True, env={**os.environ, "TZ": "KST-9"})
    assert done.stdout.strip() == "9:00:00", done.stderr


def test_m129_a_time_nobody_can_read_is_not_passed_on_as_the_first_use(tmp_path):
    write(tmp_path / "experiments" / "c.md", CARD)
    assert edited_after("experiments/c.md", ("dev_x", "abc", "garbage"), tmp_path)["first_run_at"] is None
    got = edited_after("experiments/c.md", ("dev_x", "abc", "2026-01-01T00:00:00+00:00"), tmp_path)
    assert got["first_run_at"] == "2026-01-01T00:00:00+00:00"


def test_m141_a_bullet_needs_a_space_after_the_dash(tmp_path):
    card = write(tmp_path / "c.md", CARD.replace("- Change one variable: retrieval.k", "-Change one variable: retrieval.k"))
    assert any("section 1 (Hypothesis) has no content" in p for p in cards.problems(card))


# ---------------------------------------------------------------- report and compare

def test_m114_the_report_is_written_with_unix_line_endings(project):
    summary_of(project)
    build_report()
    assert b"\r\n" not in (project / "results" / "SUMMARY.md").read_bytes()


def test_m146_the_pricing_warning_needs_a_hash_on_both_sides(tmp_path):
    two_results(tmp_path)
    write(tmp_path / "results/a/summary.json", '{"pricing_hash": "abc"}')
    assert "pricing" not in compare_experiments("a", "b", metric="mrr", root=tmp_path)
    write(tmp_path / "results/b/summary.json", '{"pricing_hash": "xyz"}')
    assert "different configs/pricing.yaml" in compare_experiments("a", "b", metric="mrr", root=tmp_path)


def test_m149_a_gold_at_k_metric_is_divided_by_k(tmp_path):
    for name, value in (("a", 5), ("b", 0)):
        write(tmp_path / f"results/{name}/metrics.jsonl", "".join(f'{{"qid": "{q}", "gold@5": {value}}}\n' for q in "12"))
        write(tmp_path / f"results/{name}/summary.json", "{}")
    assert "a 1.000  b 0.000" in compare_experiments("a", "b", metric="gold@5", root=tmp_path)


def test_m152_a_missing_cost_on_one_side_prints_none_without_a_ratio(tmp_path):
    two_results(tmp_path)
    write(tmp_path / "results/a/summary.json", '{"cost": {"usd_per_question": 0.5}}')
    assert "USD/question: 0.5 -> None" in compare_experiments("a", "b", metric="mrr", root=tmp_path)


def test_m156_the_changed_input_warning_reads_files_from_the_project_not_the_current_folder(tmp_path, tmp_path_factory,
                                                                                            monkeypatch):
    two_results(tmp_path)
    write(tmp_path / "prompts" / "p.md", "x\n")
    write(tmp_path / "results/a/summary.json",
          json.dumps({"file_hashes": {"prompts/p.md": file_hash(tmp_path / "prompts" / "p.md")}}))
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert "WARNING" not in without_small_n(compare_experiments("a", "b", metric="mrr", root=tmp_path))


# ---------------------------------------------------------------- the uncommitted-code fingerprint

def test_m162_a_pythonpath_that_resolves_to_the_project_root_does_not_make_everything_code(tmp_path):
    (tmp_path / "src").mkdir()
    write(tmp_path / "labkit.yaml", "pythonpath: [src/..]\n")
    assert "." not in code_folders(tmp_path)


def test_m163_an_unreadable_labkit_yaml_falls_back_to_the_default_folders(tmp_path):
    write(tmp_path / "labkit.yaml", "modules: 5\n")
    assert code_folders(tmp_path) == list(TRACKED)


@pytest.mark.needs_git
def test_m172_a_very_large_new_file_is_fingerprinted_by_size_not_read_whole(repo):
    big = repo / "src" / "big.bin"
    big.write_bytes(b"a" * (50 * 1024 * 1024 + 1))
    first = git_info(repo)["changes"]
    big.write_bytes(b"b" * (50 * 1024 * 1024 + 1))
    assert first and git_info(repo)["changes"] == first


@pytest.mark.needs_git
def test_m175_the_fingerprint_is_ten_characters_long(repo):
    write(repo / "src" / "plug.py", "V = 2\n")
    assert len(git_info(repo)["changes"]) == 10


@pytest.mark.needs_git
def test_m178_the_git_state_is_that_of_the_project_not_of_the_current_folder(repo, tmp_path_factory, monkeypatch):
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert environment(repo)["git"]["commit"]


# ---------------------------------------------------------------- freeze keys, summaries, the baseline check

def test_m180_an_absolute_path_through_a_link_is_keyed_by_the_path_as_written(proj):
    write(proj / "prompts" / "v1" / "answer.md", "one\n")
    make_dir_link(proj / "prompts" / "cur", proj / "prompts" / "v1")
    absolute = (proj.resolve() / "prompts" / "cur" / "answer.md").as_posix()
    write(proj / "configs" / "baseline.yaml", FREEZE_BASELINE.replace("prompts/answer_v1.md", f"'{absolute}'"))
    files = referenced_files(freeze_baseline(proj), load_project(proj), proj)
    assert "prompts/cur/answer.md" in files and "prompts/v1/answer.md" not in files


def test_m181_a_path_written_with_dots_is_one_normalized_name(proj):
    write(proj / "configs" / "baseline.yaml", FREEZE_BASELINE.replace("prompts/answer_v1.md", "prompts/../prompts/answer_v1.md"))
    files = referenced_files(freeze_baseline(proj), load_project(proj), proj)
    assert [f for f in files if "answer_v1" in f] == ["prompts/answer_v1.md"]


@pytest.fixture
def tproj(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    root = tmp_path / "tproj"
    shutil.copytree(TEMPLATE, root)
    monkeypatch.chdir(root)
    return root


def test_m194_a_json_list_is_not_a_summary(tmp_path):
    with pytest.raises(ValueError, match="not a readable summary"):
        load_summary(write(tmp_path / "summary.json", "[1, 2]"))


@pytest.mark.parametrize("missing", ["changed", "config_file", "sets"])
def test_m203_each_of_the_three_selection_keys_is_required_by_the_baseline_check(tproj, missing):
    done = {"n_questions": 2, "n_expected": 2, "n_graded": 2, "complete": True, "incomplete": [], **IS_THE_BASELINE}
    del done[missing]
    write(tproj / "results" / "dev_000_baseline" / "summary.json", json.dumps(done))
    ok, detail = AUTOCHECKS["baseline_evaluated"](tproj, {})
    assert not ok and "no record of which config" in detail


def test_m231_a_checked_capital_x_box_is_a_criterion(tproj):
    path = next((tproj / "docs" / "steps").glob("03-*.md"))
    before = len(steps.criteria(tproj, 3))
    head, tail = path.read_text(encoding="utf-8").split("## Exit criteria", 1)
    path.write_text(head + "## Exit criteria" + tail.replace("- [ ] ", "- [X] ", 1), encoding="utf-8")
    assert len(steps.criteria(tproj, 3)) == before


def test_m242_criterion_zero_cannot_be_ticked(tproj):
    with pytest.raises(ValueError, match=r"criteria 1\.\.\d+, not 0"):
        steps.tick(tproj, 1, 0, "evidence", "ana")


# ---------------------------------------------------------------- scaffold and the run log CLI

def test_m269_a_non_ascii_title_is_written_as_it_is_not_escaped(tproj):
    made = new_card(tproj, "kor", title="하이브리드 검색")
    assert "description: 하이브리드 검색\n" in (tproj / made["config"]).read_text(encoding="utf-8")


def test_m270_a_long_title_stays_on_one_line_in_the_config(tproj):
    title = " ".join(["word"] * 60)
    made = new_card(tproj, "long", title=title)
    assert f"description: {title}" in (tproj / made["config"]).read_text(encoding="utf-8").splitlines()


@pytest.mark.needs_git
def test_m272_a_reference_to_the_previous_branch_is_not_a_branch_name(tmp_path, monkeypatch):
    repo_dir = tmp_path / "r"
    repo_dir.mkdir()
    for args in (["init"], ["commit", "--allow-empty", "-m", "i"], ["switch", "-c", "other"], ["switch", "-"]):
        git_in(repo_dir, *args)
    monkeypatch.chdir(repo_dir)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(ValueError, match="not a valid branch name"):
        check_branch_name("@{-1}")


def test_m276_a_blank_branch_name_gets_the_blank_message():
    with pytest.raises(ValueError, match="must be a branch name"):
        check_branch_name("  ")


def test_m280_a_branch_name_that_is_a_yaml_keyword_survives_the_round_trip(tmp_path):
    init_project(tmp_path / "p", main_branch="yes")
    assert load_project(tmp_path / "p")["main_branch"] == "yes"


def test_m283_a_base_config_with_a_hash_in_its_name_is_quoted(tproj):
    shutil.copy(tproj / "configs" / "baseline.yaml", tproj / "configs" / "#odd.yaml")
    made = new_card(tproj, "odd", base="#odd.yaml")
    assert yaml.safe_load((tproj / made["config"]).read_text(encoding="utf-8"))["base"] == "#odd.yaml"


def test_m293_rows_with_no_recorded_split_fall_back_to_the_name_in_the_export_guard(tmp_path, monkeypatch):
    db, out = tmp_path / "runs.db", tmp_path / "o.json"
    RunLog(db).save({"status": "answered", "config": {}}, experiment="test_x", qid="Q1")
    monkeypatch.setattr(sys, "argv", ["runlog", "export", str(db), "test_x", str(out)])
    with pytest.raises(SystemExit) as stopped:
        runlog.main()
    assert stopped.value.code == 1 and not out.exists()


def test_m295_the_final_flag_may_come_first_on_the_command_line(tmp_path, monkeypatch, capsys):
    db, out = tmp_path / "runs.db", tmp_path / "o.json"
    RunLog(db).save({"status": "answered", "config": {}}, experiment="dev_x", qid="Q1")
    monkeypatch.setattr(sys, "argv", ["runlog", "export", "--final", str(db), "dev_x", str(out)])
    runlog.main()
    assert out.exists()
    monkeypatch.setattr(sys, "argv", ["runlog", "list", str(db), "--final"])
    runlog.main()
    assert "dev_x" in capsys.readouterr().out


def test_m296_an_exported_jsonl_ends_with_one_unix_newline(tmp_path, monkeypatch):
    db, out = tmp_path / "runs.db", tmp_path / "o.jsonl"
    RunLog(db).save({"status": "answered", "config": {}}, experiment="dev_x", qid="Q1")
    monkeypatch.setattr(sys, "argv", ["runlog", "export", str(db), "dev_x", str(out)])
    runlog.main()
    assert out.read_bytes().endswith(b"}\n") and b"\r\n" not in out.read_bytes()


def test_m306_a_run_value_that_is_not_a_mapping_is_left_alone():
    assert settings({"run": 4, "k": 1}) == {"run": 4, "k": 1}


def test_m328_a_yaml_error_is_one_line_that_says_so(tproj, capsys):
    write(tproj / "configs" / "bad.yaml", "a: [unclosed\nb: c: d\n")
    assert main(["--root", str(tproj), "run", "--config", "configs/bad.yaml", "--split", "dev"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("labkit: error: not valid YAML") and err.count("\n") == 1


def test_m342_control_characters_are_dropped_by_squash():
    assert squash("a\x00\x07\x1bb") == "ab"


def test_m378_a_boolean_page_in_a_target_is_reported():
    item = {"qid": "Q1", "split": "dev", "question": "q", "answerable": True, "targets": [{"file": "a.pdf", "page": True}],
            "evidence": ["a quote that is long enough"]}
    problems = check_items([item], {("a.pdf", 1): "a quote that is long enough"})
    assert [p["problem"] for p in problems if "a target needs" in p["problem"]]
