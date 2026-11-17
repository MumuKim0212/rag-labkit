"""Tests for the mutants the second review round found alive.

A review planted 335 one-line bugs in labkit (at commit 31fffbc) and 95 survived the suite. These tests were written
by the reviewer against that commit, each checked to pass on the real code and to fail on its own mutant; they are
adapted here to the code as it is now (the test name carries the mutant id). They pin things that were only
exercised on one happy path: error texts and line numbers, the second argument of a two-sided check, summaries
that lack a key, a second card or scope, the uncommitted-code fingerprint, and the edges of the eval helpers.
"""

import json
import os
import subprocess
import sys
import types

import pytest

from labkit import cards, steps
from labkit.autochecks import AUTOCHECKS
from labkit.cli import main
from labkit.compare import compare_experiments
from labkit.config import load_config
from labkit.cost import call_usd, calls_of, record_call
from labkit.env import TRACING_VARS, setup
from labkit.eval import paired, parse_where
from labkit.eval.headroom import headroom
from labkit.eval.metrics import (context_metrics, coverage, direction_agreement, proxy_check, ranked_metrics,
                                 squash)
from labkit.eval.runlog import RunLog
from labkit.freeze import check_run
from labkit.golden import behavior_of, load_golden
from labkit.golden_check import check_items, load_pages, run_check
from labkit.harness import (SplitLocked, _mean, _require_items, _require_unfinished, build_report, check_grade,
                            evaluate_experiment, experiment_split, grade_experiment, metric_means, run_experiment)
from labkit.index import index_key
from labkit.measures import MEASURES, register_measure
from labkit.plugins import load_modules
from labkit.project import load_project, settings_diff
from labkit.registry import Registry
from labkit.repro import git_info
from labkit.scaffold import init_project
from labkit.show import _cut, show
from test_gates import FILLED_CARD
from test_harness import (BASELINE, GOLDEN, LABKIT_YAML, PRICES, FakeSystem, fake_grade, fake_judge,  # noqa: F401
                          five_dev_questions, graded_and_evaluated, project, summary_of)
from test_harness import write
from test_steps import BASE as NOISE_BASE
from test_steps import choose_models, noise_setup, proj, write_summary  # noqa: F401


def cfg_of(project):
    return load_config(project / "configs" / "baseline.yaml")


# ---------------------------------------------------------------- config, project, harness helpers

def test_c03_a_config_that_is_a_list_is_a_value_error_not_an_attribute_error(project):
    write(project / "configs" / "list.yaml", "- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        load_config(project / "configs" / "list.yaml")


def test_p04_a_setting_present_in_only_one_record_is_a_difference():
    assert settings_diff({"a": 1}, {"a": 1, "b": 2}) == ["b"]


def test_p10_an_empty_index_dir_is_refused_at_load(project):
    write(project / "labkit.yaml", 'index_dir: ""\n')
    with pytest.raises(ValueError, match="index_dir"):
        load_project()


def test_h01_an_infinite_extra_judge_field_is_refused():
    with pytest.raises(ValueError, match="finite"):
        check_grade({"correctness": 1, "faithfulness": float("inf")}, "Q1", 2)


def test_h02_a_nan_correctness_is_refused():
    with pytest.raises(ValueError, match="correctness=nan"):
        check_grade({"correctness": float("nan")}, "Q1", 2)


def test_h05_means_skip_nan_and_infinity():
    assert metric_means([{"x": float("nan")}, {"x": 1.0}, {"x": float("inf")}]) == {"x": 1.0}


def test_h06_a_measure_that_returns_infinity_is_written_as_null(project):
    @register_measure("infm")
    def infm(item, run, project):
        return {"inf_score": float("inf")}
    try:
        write(project / "labkit.yaml", LABKIT_YAML + "measures: [infm]\n")
        run_experiment(cfg_of(project), "dev", experiment="dev_x", system=FakeSystem())
        graded_and_evaluated("dev_x")
    finally:
        MEASURES.pop("infm", None)
    text = (project / "results" / "dev_x" / "metrics.jsonl").read_text(encoding="utf-8")
    assert "Infinity" not in text and '"inf_score": null' in text


def test_h09_the_ellipsis_appears_only_after_five_missing_questions():
    rows = [{"qid": f"Q{i}"} for i in range(5)]
    with pytest.raises(ValueError) as five:
        _require_items("e", rows, {})
    assert "…" not in str(five.value)
    with pytest.raises(ValueError) as six:
        _require_items("e", rows + [{"qid": "Q9"}], {})
    assert "…" in str(six.value)


def settings_changed_after_a_smoke_test(project, retrieval_only=False):
    cfg, exp = cfg_of(project), "dev_x"
    run_experiment(cfg, "dev", experiment=exp, limit=1, retrieval_only=retrieval_only, system=FakeSystem())
    write(project / "labkit.yaml", LABKIT_YAML + "max_score: 4\n")
    run_experiment(cfg, "dev", experiment=exp, retrieval_only=retrieval_only, system=FakeSystem())
    return exp


def test_h10_grading_ignores_the_settings_a_smoke_test_recorded(project):
    assert grade_experiment(settings_changed_after_a_smoke_test(project))["graded_now"] == 2


def test_h11_evaluating_ignores_the_settings_a_smoke_test_recorded(project):
    exp = settings_changed_after_a_smoke_test(project, retrieval_only=True)
    assert evaluate_experiment(exp, prices=PRICES)["max_score"] == 4


def test_h25_a_summary_that_is_not_an_object_is_refused_for_test_and_ignored_for_dev(tmp_path):
    path = tmp_path / "summary.json"
    path.write_text("[]", encoding="utf-8")
    _require_unfinished(path, "dev")                                    # dev over dev: the documented, manual rule
    with pytest.raises(SplitLocked):                                    # a test result is involved: not known unfinished
        _require_unfinished(path, "test")


def test_h33_regrade_cost_is_the_cost_of_the_regrade_not_of_the_first_grading(project):
    def expensive(item, run, cfg):
        calls = []
        record_call({"calls": calls}, node="judge", model="j", input_tokens=1000, output_tokens=100)
        return {"correctness": 1, "calls": calls}
    run_experiment(cfg_of(project), "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x")
    grade_experiment("dev_x", suffix="__regrade1", grade_fn=expensive)
    cost = evaluate_experiment("dev_x", prices=PRICES)["cost"]
    assert cost["judge_usd"] == pytest.approx(2 * (500 * 0.75 + 50 * 3.75) / 1e6)
    assert cost["judge_regrade_usd"] == pytest.approx(2 * (1000 * 0.75 + 100 * 3.75) / 1e6)


def card_variant(project):
    write(project / "experiments" / "004-hybrid.md", FILLED_CARD)
    write(project / "configs" / "004-hybrid.yaml",
          "base: baseline.yaml\nname: 004_hybrid\ncard: experiments/004-hybrid.md\nretrieval: {k: 9}\n")
    run_experiment(load_config(project / "configs" / "004-hybrid.yaml"), "dev", experiment="dev_004_hybrid",
                   system=FakeSystem())
    grade_experiment("dev_004_hybrid")


def test_h43_a_teammates_summary_without_a_time_is_not_taken_as_the_oldest_card_record(project):
    card_variant(project)
    write(project / "results" / "dev_other" / "summary.json", json.dumps({
        "experiment": "dev_other", "split": "dev",
        "card_check": {"file": "experiments/004-hybrid.md", "hash_at_first_run": "deadbeef00"}}))
    check = evaluate_experiment("dev_004_hybrid", prices=PRICES)["card_check"]
    assert check["hash_at_first_run"] != "deadbeef00" and check["first_run_experiment"] == "dev_004_hybrid"
    assert check["edited_after_run"] is False


def test_h44_another_cards_record_is_not_this_cards_first_use(project):
    card_variant(project)
    write(project / "results" / "dev_other" / "summary.json", json.dumps({
        "experiment": "dev_other", "split": "dev", "created_at": "2000-01-01T00:00:00",
        "card_check": {"file": "experiments/005-other.md", "hash_at_first_run": "feedface00"}}))
    check = evaluate_experiment("dev_004_hybrid", prices=PRICES)["card_check"]
    assert check["hash_at_first_run"] != "feedface00" and check["edited_after_run"] is False


def test_h45_index_dir_is_resolved_against_the_project_root_not_the_cwd(project, tmp_path_factory, monkeypatch):
    write(project / "labkit.yaml", LABKIT_YAML + "index_keys: [corpus]\nindex_dir: out/index/{index_key}/{scope}\n")
    cfg = cfg_of(project)
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem(), root=project)
    grade_experiment("dev_x", root=project)
    calls = []
    record_call({"calls": calls}, node="embed", model="m", input_tokens=2_000_000, kind="embedding")
    write(project / "out" / "index" / index_key(cfg, load_project(project)) / "A" / "build.json",
          json.dumps({"calls": calls}))
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert evaluate_experiment("dev_x", prices=PRICES, root=project)["cost"]["index_build_usd"] == pytest.approx(1.5)


def test_h49_a_summary_without_a_complete_key_is_not_shown_as_incomplete(project):
    write(project / "results" / "dev_old" / "summary.json",
          json.dumps({"experiment": "dev_old", "split": "dev", "n_questions": 3}))
    build_report()
    rows = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines()
    row = next(line for line in rows if line.startswith("| dev_old"))
    assert "incomplete" not in row and " 3 |" in row


def test_cm01_a_test_experiment_with_a_neutral_name_is_locked_in_compare(project):
    for name in ("final_a", "final_b"):
        write(project / "results" / name / "summary.json", json.dumps({"split": "test"}))
    with pytest.raises(SplitLocked):
        compare_experiments("final_a", "final_b")


def test_cm02_compare_checks_the_split_of_the_second_experiment_too(project):
    write(project / "results" / "dev_a" / "summary.json", json.dumps({"split": "dev"}))
    write(project / "results" / "final_b" / "summary.json", json.dumps({"split": "test"}))
    with pytest.raises(SplitLocked):
        compare_experiments("dev_a", "final_b")


def test_cm06_compare_divides_by_the_scale_the_summaries_recorded_not_todays(project):
    for name, score in (("dev_a", 4), ("dev_b", 2)):
        write(project / "results" / name / "summary.json", json.dumps({"split": "dev", "max_score": 4}))
        write(project / "results" / name / "grades.jsonl", json.dumps({"qid": "Q1", "correctness": score}) + "\n")
    text = compare_experiments("dev_a", "dev_b")
    assert "dev_a 1.000" in text and "dev_b 0.500" in text


def test_cm11_the_second_experiment_name_is_validated_too(project):
    with pytest.raises(ValueError, match="not a valid experiment name"):
        compare_experiments("dev_a", "dev/../x")


def test_f04_a_file_that_still_exists_but_is_no_longer_referenced_is_not_a_deleted_input(tmp_path):
    write(tmp_path / "prompts" / "a.md", "x")
    row = {"config": {"config_hash": "h", "file_hashes": {"prompts/a.md": "abc"}}}
    check_run({"config_hash": "h"}, {}, "dev_x", [row], tmp_path, None)


def test_f12_the_settings_check_looks_at_every_earlier_row_not_only_the_first(tmp_path):
    old_labkit_row = {"config": {"config_hash": "h"}}
    row = {"config": {"config_hash": "h", "project_settings": {"golden_files": ["g.jsonl"], "max_score": 2}}}
    with pytest.raises(ValueError, match="labkit.yaml changed"):
        check_run({"config_hash": "h"}, {}, "dev_x", [old_labkit_row, row], tmp_path,
                  {"golden_files": ["g.jsonl"], "max_score": 4})


# ---------------------------------------------------------------- steps and the auto-checks

def test_s06_s07_a_criterion_error_names_the_real_line(proj):
    path = steps.step_file(proj, 3)
    text = path.read_text(encoding="utf-8").replace("## Exit criteria\n", "## Exit criteria\n\n- []\n", 1)
    path.write_text(text, encoding="utf-8")
    lineno = text.splitlines().index("- []") + 1
    with pytest.raises(ValueError) as e:
        steps.criteria(proj, 3)
    assert f"{path.name}:{lineno}: " in str(e.value)


def test_a09_a_rerun_summary_without_a_complete_key_is_not_a_noise_run(proj):
    noise_setup(proj)
    write(proj / "results" / "dev_000_baseline_r2" / "summary.json",
          json.dumps({k: v for k, v in NOISE_BASE.items() if k != "complete"}))
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", '{"qid": "1"}\n' * 4)
    ok, detail = AUTOCHECKS["noise_runs_exist"](proj, {})
    assert not ok and "(a)" in detail


def test_a10_a_complete_rerun_of_fewer_questions_is_not_a_noise_run(proj):
    noise_setup(proj)
    write_summary(proj, "dev_000_baseline_r2", n_questions=3)
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", '{"qid": "1"}\n' * 4)
    ok, detail = AUTOCHECKS["noise_runs_exist"](proj, {})
    assert not ok and "(a)" in detail


def test_a11_a_retrieval_only_summary_under_a_plain_rerun_name_is_not_a_noise_run(proj):
    noise_setup(proj)
    write_summary(proj, "dev_000_baseline_r2", retrieval_only=True)
    write(proj / "out" / "grades" / "dev_000_baseline__regrade1.jsonl", '{"qid": "1"}\n' * 4)
    ok, detail = AUTOCHECKS["noise_runs_exist"](proj, {})
    assert not ok and "(a)" in detail


def test_a14_a_corrupt_rerun_summary_is_skipped_not_a_crash(proj):
    noise_setup(proj)
    write(proj / "results" / "dev_000_baseline_r2" / "summary.json", "{ not json")
    ok, detail = AUTOCHECKS["noise_runs_exist"](proj, {})
    assert not ok and "(a)" in detail


# ---------------------------------------------------------------- the uncommitted-code fingerprint

def git_in(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    root = tmp_path / "repo"
    root.mkdir()
    git_in(root, "init")
    write(root / "src" / "plug.py", "V = 1\n")
    git_in(root, "add", ".")
    git_in(root, "commit", "-m", "init")
    return root


@pytest.mark.needs_git
def test_r02_a_non_pyc_file_in_pycache_is_not_code(repo):
    write(repo / "src" / "__pycache__" / "n.txt", "x")
    assert git_info(repo)["changes"] is None


@pytest.mark.needs_git
def test_r03_crlf_and_lf_copies_of_a_new_file_have_one_fingerprint(repo):
    (repo / "src" / "new.py").write_bytes(b"x\r\ny\r\n")
    crlf = git_info(repo)["changes"]
    (repo / "src" / "new.py").write_bytes(b"x\ny\n")
    assert crlf and git_info(repo)["changes"] == crlf


@pytest.mark.needs_git
def test_r04_the_same_content_under_another_file_name_is_another_fingerprint(repo):
    write(repo / "src" / "a.py", "x\n")
    first = git_info(repo)["changes"]
    (repo / "src" / "a.py").unlink()
    write(repo / "src" / "b.py", "x\n")
    assert first and git_info(repo)["changes"] not in (None, first)


@pytest.mark.needs_git
def test_r05_without_run_defining_folders_there_is_no_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    root = tmp_path / "plain"
    write(root / "notes.md", "a\n")
    for args in (["init"], ["add", "."], ["commit", "-m", "i"]):
        git_in(root, *args)
    write(root / "notes.md", "b\n")
    info = git_info(root)
    assert info["dirty"] is True and info["changes"] is None


@pytest.mark.needs_git
def test_r06_a_staged_edit_is_part_of_the_fingerprint(repo):
    write(repo / "src" / "plug.py", "V = 2\n")
    git_in(repo, "add", "src/plug.py")
    assert git_info(repo)["changes"] is not None


@pytest.mark.needs_git
def test_r07_a_git_ignored_file_does_not_change_the_fingerprint(repo):
    write(repo / ".gitignore", "prompts/*.tmp\n")
    git_in(repo, "add", ".")
    git_in(repo, "commit", "-m", "ignore")
    write(repo / "src" / "plug.py", "V = 2\n")
    first = git_info(repo)["changes"]
    write(repo / "prompts" / "scratch.tmp", "a")
    assert first and git_info(repo)["changes"] == first


@pytest.mark.needs_git
def test_r10_a_repository_without_a_commit_has_no_fingerprint_instead_of_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    root = tmp_path / "fresh"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    write(root / "src" / "x.py", "V = 1\n")
    assert git_info(root)["changes"] is None


# ---------------------------------------------------------------- cards, run log, show, scaffold, cli

def test_cd02_a_card_header_with_backslash_paths_names_the_config(tmp_path):
    card = write(tmp_path / "c.md", "Branch: `u/001-x` · Config: `configs\\001-x.yaml`\n")
    assert cards.names_config(card, "configs/001-x.yaml")


def test_cd03_a_config_without_a_file_name_is_not_accused_of_a_missing_header(project):
    write(project / "experiments" / "004-hybrid.md", FILLED_CARD)
    cfg = {"changed": {"k": 1}, "card": "experiments/004-hybrid.md", "name": "x"}
    assert cards.gate(cfg, project)["config_named"] is True


def test_cd12_edited_after_without_a_first_record_is_none(tmp_path):
    assert cards.edited_after("experiments/x.md", None, tmp_path) is None


def test_cd06_a_deleted_card_counts_as_edited(tmp_path):
    assert cards.edited_after("experiments/x.md", ("dev_x", "abc"), tmp_path)["edited_after_run"] is True


def test_cd10_trailing_spaces_do_not_change_the_sections_hash(tmp_path):
    card = write(tmp_path / "c.md", FILLED_CARD)
    first = cards.sections_hash(card)
    write(card, FILLED_CARD.replace("\n", "   \n"))
    assert cards.sections_hash(card) == first


def test_rl02_card_records_hold_only_the_asked_card(tmp_path):
    log = RunLog(tmp_path / "runs.db")
    for card, digest in (("experiments/a.md", "h1"), ("experiments/b.md", "h2")):
        log.save({"status": "answered", "config": {"card_check": {"file": card, "sections_hash": digest}}},
                 experiment="dev_x", qid="Q1")
    assert [r[2] for r in log.card_records("experiments/a.md")] == ["h1"]


def test_rl05_latest_is_the_last_run_of_a_question(tmp_path):
    log = RunLog(tmp_path / "runs.db")
    log.save({"status": "answered", "answer": "first"}, experiment="dev_x", qid="Q1")
    log.save({"status": "answered", "answer": "second"}, experiment="dev_x", qid="Q1")
    assert [r["answer"] for r in log.latest("dev_x")] == ["second"]


def test_sh05_show_names_the_bad_experiment_name_not_a_missing_log(project):
    with pytest.raises(ValueError, match="not a valid experiment name"):
        show("../x")


def test_sh03_chars_zero_means_no_cut():
    assert _cut("a" * 400, 0) == "a" * 400


def test_sc04_init_refuses_a_folder_holding_only_a_dotfile(tmp_path):
    target = tmp_path / "p"
    write(target / ".env", "KEY=1\n")
    with pytest.raises(FileExistsError, match="not empty"):
        init_project(target)


def test_cl04_init_does_not_need_a_loadable_project_in_the_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    here = tmp_path / "here"
    write(here / "labkit.yaml", "main_branch: 3\n")
    monkeypatch.chdir(here)
    assert main(["init", str(tmp_path / "new")]) == 0


def test_cl12_new_card_names_the_projects_main_branch(proj, capsys):
    y = proj / "labkit.yaml"
    y.write_text(y.read_text(encoding="utf-8").replace("main_branch: main", "main_branch: trunk"), encoding="utf-8")
    assert main(["--root", str(proj), "new-card", "demo"]) == 0
    assert "on trunk (commit that alone)" in capsys.readouterr().out


# ---------------------------------------------------------------- older code, edge assertions

def test_g10_blank_lines_in_a_golden_file_are_ignored(project):
    lines = [json.dumps(g, ensure_ascii=False) for g in GOLDEN]
    write(project / "golden" / "provided.jsonl", lines[0] + "\n\n" + lines[1] + "\n   \n" + lines[2] + "\n")
    assert len(load_golden()) == 3


def test_g11_an_unanswerable_item_does_not_inherit_the_answerable_default():
    project_ = {"behaviors": {"a": {"group": "g", "statuses": ["answered"]}}, "answerable_default": "a"}
    with pytest.raises(KeyError):
        behavior_of({"qid": "Q", "answerable": False}, project_)


@pytest.mark.parametrize("value", ["3", '""'])
def test_p07_main_branch_must_be_a_non_empty_string(project, value):
    write(project / "labkit.yaml", f"main_branch: {value}\n")
    with pytest.raises(ValueError, match="main_branch"):
        load_project()


def test_p11_golden_files_must_be_a_list(project):
    write(project / "labkit.yaml", "golden: {files: golden.jsonl}\n")
    with pytest.raises(ValueError, match="golden.files"):
        load_project()


def test_p12_golden_pages_must_not_be_empty(project):
    write(project / "labkit.yaml", 'golden: {pages: ""}\n')
    with pytest.raises(ValueError, match="golden.pages"):
        load_project()


def test_h32_a_judge_field_cannot_overwrite_a_measure_column(project):
    def judge(item, run, cfg):
        return {"correctness": 2, "status_ok": 0}
    run_experiment(cfg_of(project), "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x", grade_fn=judge)
    assert evaluate_experiment("dev_x", prices=PRICES)["metrics"]["status_ok"] == 1.0


def test_h52_report_include_test_lists_the_test_experiments(project):
    run_experiment(cfg_of(project), "test", experiment="test_000_baseline", final=True, system=FakeSystem())
    grade_experiment("test_000_baseline", grade_fn=fake_grade, final=True)
    evaluate_experiment("test_000_baseline", prices=PRICES, final=True)
    assert build_report()["experiments"] == 0
    assert build_report(include_test=True, final=True)["experiments"] == 1


def test_h56_rows_from_a_labkit_without_the_selection_record_fall_back_to_the_name():
    assert experiment_split("dev_old", [{"config": {"system": "x"}}]) == "dev"


def test_h57_limit_counts_unfinished_questions_not_the_first_questions(project):
    five_dev_questions(project)
    cfg = cfg_of(project)
    assert run_experiment(cfg, "dev", experiment="dev_x", limit=4, system=FakeSystem())["ran"] == 4
    again = run_experiment(cfg, "dev", experiment="dev_x", limit=4, system=FakeSystem())
    assert again["ran"] == 1 and again["skipped_done"] == 4


def test_h60_a_system_reporting_an_index_key_is_fine_when_the_config_declares_no_index_keys(project):
    class Keyed(FakeSystem):
        def run(self, question, scope):
            result = super().run(question, scope)
            result["steps"]["index_key"] = "deadbeef00"
            return result
    assert run_experiment(cfg_of(project), "dev", experiment="dev_x", system=Keyed())["statuses"] == {
        "answered": 1, "abstained": 1}


def test_h62_n_llm_calls_of_a_row_does_not_count_embedding_calls(project):
    class Embeds(FakeSystem):
        def run(self, question, scope):
            result = super().run(question, scope)
            for _ in range(2):
                record_call(result["steps"], node="e", model="m", kind="embedding", input_tokens=10)
            return result
    run_experiment(cfg_of(project), "dev", experiment="dev_x", system=Embeds())
    assert {r["n_llm_calls"] for r in RunLog(project / "out" / "runs.db").latest("dev_x")} == {1}


def test_h65_usd_per_question_divides_by_the_scored_questions_only(project):
    cfg = cfg_of(project)
    run_experiment(cfg, "dev", experiment="dev_x", limit=1, system=FakeSystem())
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    assert graded_and_evaluated("dev_x")["cost"]["usd_per_question"] == pytest.approx((1000 * 0.75 + 200 * 3.75) / 1e6)


def test_h69_means_keep_four_decimals():
    assert _mean([1, 1, 0]) == 0.6667


def test_h71_the_summary_records_the_changes_from_the_base_not_a_copy_of_changed(project):
    cfg = cfg_of(project)
    cfg["changed_from_base"] = {"retrieval.k": {"from": 5, "to": 9}}
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    s = graded_and_evaluated("dev_x")
    assert s["changed"] == {} and s["changed_from_base"] == {"retrieval.k": {"from": 5, "to": 9}}


def test_h75_cost_input_tokens_is_input_not_output(project):
    s = summary_of(project)
    assert s["cost"]["input_tokens"] == 2000 and s["cost"]["output_tokens"] == 400


def test_s13_revoke_sees_an_approved_step_8(proj):
    done = {"approved": {"by": "o", "at": "2026-01-01T00:00:00", "note": ""}}
    steps.save_state(proj, {"version": 1, "steps": {n: dict(done) for n in range(1, 9)}, "log": []})
    with pytest.raises(ValueError, match="step 8 is approved"):
        steps.revoke(proj, 7, "why", by="o")


def test_s22_a_checked_box_is_a_criterion(proj):
    path = steps.step_file(proj, 3)
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Exit criteria\n", "## Exit criteria\n\n- [x] already ticked\n", 1), encoding="utf-8")
    assert steps.criteria(proj, 3)[0].text == "already ticked"


def test_a17_one_placeholder_model_is_enough_to_fail_the_check(proj):
    choose_models(proj, answer="example-answer", judge="model-j-001")
    ok, detail = AUTOCHECKS["judge_differs_from_answer"](proj, {})
    assert not ok and "template placeholder" in detail


def test_gc06_a_non_boolean_answerable_is_reported_once():
    problems = check_items([{"qid": "Q1", "split": "dev", "question": "q", "answerable": "yes"}], {})
    assert [p["problem"] for p in problems] == ["answerable must be true or false (got 'yes')"]


def test_gc09_a_60_character_quote_is_shown_whole():
    item = {"qid": "Q1", "split": "dev", "question": "q", "answerable": True, "targets": [{"file": "a.pdf", "page": 1}],
            "evidence": ["x" * 60]}
    (problem,) = check_items([item], {("a.pdf", 1): "something else"})
    assert problem["problem"].endswith("x" * 60)


def test_gc11_the_summary_line_counts_items_and_problems_in_the_right_order(project):
    write(project / "golden" / "provided.jsonl",
          json.dumps({"qid": "Q1", "split": "dev", "answerable": True, "question": "q"}) + "\n")
    write(project / "corpus" / "pages.jsonl", json.dumps({"file": "a.pdf", "page": 1, "text": "x"}) + "\n")
    text, ok = run_check("corpus/pages.jsonl", None, project)
    assert not ok and "1 with problems, 2 problems" in text


def test_gc14_a_page_row_without_text_is_refused(tmp_path):
    path = write(tmp_path / "pages.jsonl", json.dumps({"file": "a.pdf", "page": 1}) + "\n")
    with pytest.raises(ValueError, match="needs `file`"):
        load_pages(path)


def test_gc19_an_answerable_item_without_targets_is_reported():
    problems = check_items([{"qid": "Q1", "split": "dev", "question": "q", "answerable": True,
                             "evidence": ["a quote long enough"]}], {})
    assert "answerable question has no targets" in [p["problem"] for p in problems]


# ---------------------------------------------------------------- the eval helpers' edges

ITEM = {"qid": "Q1", "targets": [{"file": "a.pdf", "page": 1}], "evidence": ["the quote that matters here"]}
UNIT = {"chunk_id": "c", "source": "b.pdf", "page": 9, "content": "zzzz"}


def test_me02_squash_drops_zero_width_characters():
    assert squash("a​b") == "ab"


def test_me06_coverage_without_evidence_is_none_not_zero():
    assert coverage([], [{"content": "x"}]) is None


def test_me11_evid_at_5_looks_at_five_units_only():
    sixth = {"chunk_id": "c6", "source": "b.pdf", "page": 9, "content": "the quote that matters here"}
    row = ranked_metrics(ITEM, [UNIT] * 5 + [sixth])
    assert row["evid@5"] == 0.0 and row["evid@10"] == 1.0


def test_me13_chars_is_the_text_size_not_the_unit_count():
    assert context_metrics(ITEM, [{**UNIT, "content": "abcd"}])["chars"] == 4


def test_me16_proxy_check_skips_missing_metric_values():
    assert proxy_check({"a": 1, "b": 0, "c": 2}, {"m": {"a": 1, "b": 0, "c": None}})["m"] == 1.0


def test_me19_direction_agreement_counts_only_questions_whose_score_changed():
    r = direction_agreement({"q1": 0, "q2": 0}, {"q1": 1, "q2": 1}, {"q1": 0, "q2": 1}, {"q1": 1, "q2": 1})
    assert r["score_changed"] == 1


def test_me20_mrr_is_zero_when_no_gold_unit_is_ranked():
    assert ranked_metrics(ITEM, [UNIT])["mrr"] == 0.0


def test_me23_proxy_correlation_keeps_three_decimals():
    assert proxy_check(dict(enumerate([0, 2, 1, 4, 4])), {"m": dict(enumerate([0, 1, 2, 3, 5]))})["m"] == 0.857


def test_pd01_pd02_the_bootstrap_interval_is_the_2_5_and_97_5_percentile(monkeypatch):
    class Counter:
        def __init__(self, seed):
            self.i = -1

        def choices(self, population, k):
            self.i += 1
            return [self.i] * k
    monkeypatch.setattr(paired, "random", types.SimpleNamespace(Random=Counter))
    assert paired.bootstrap_ci([0.0, 1.0], n_boot=1000) == (25.0, 975.0)


def test_pd07_the_mean_covers_the_questions_both_runs_have():
    r = paired.compare({"q1": 1, "q2": 1, "q3": 0}, {"q1": 1, "q2": 0})
    assert r["mean_a"] == 1.0 and r["only_in_a"] == 1


def test_pd08_the_bootstrap_is_reproducible():
    d = [i / 100 for i in range(100)]
    assert paired.bootstrap_ci(d) == paired.bootstrap_ci(d)


def test_hr02_an_unchanged_metric_is_not_an_improvement():
    r = headroom({"q": 1}, {"q": 5}, {"q": 5}, max_score=2)
    assert r["improved"] == 0 and r["upside"] == []


def test_hr08_lower_is_better_reverses_the_direction():
    r = headroom({"q": 1}, {"q": 5}, {"q": 3}, max_score=2, higher_is_better=False)
    assert r["improved"] == 1 and r["upside"] == ["q"]


def test_ev03_a_where_value_may_contain_an_equals_sign():
    assert parse_where(["k=a=b"]) == {"k": "a=b"}


def test_co04_an_unpriced_model_is_named():
    with pytest.raises(KeyError, match="no price for 'x'"):
        call_usd({"model": "x"}, {})


def test_co06_calls_of_anything_that_is_not_a_steps_dict_is_empty():
    assert calls_of(["x"]) == [] and calls_of("abc") == [] and calls_of(None) == []


def test_en03_the_real_environment_wins_over_the_env_file(project, monkeypatch):
    pytest.importorskip("dotenv")
    write(project / ".env", "MY_KEY=from-file\n")
    for var in TRACING_VARS:
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    monkeypatch.setenv("MY_KEY", "from-shell")
    setup(project)
    assert os.environ["MY_KEY"] == "from-shell"


def test_rg01_registering_the_same_function_twice_is_fine():
    registry = Registry("thing")

    def f():
        pass
    registry.register("a")(f)
    registry.register("a")(f)
    with pytest.raises(ValueError):
        registry.register("a")(lambda: None)


def test_pl01_the_project_folder_is_put_on_sys_path_once(tmp_path):
    folder = str(tmp_path.resolve())
    try:
        load_modules(tmp_path, {"pythonpath": ["."], "modules": []})
        load_modules(tmp_path, {"pythonpath": ["."], "modules": []})
        assert sys.path.count(folder) == 1
    finally:
        while folder in sys.path:
            sys.path.remove(folder)
