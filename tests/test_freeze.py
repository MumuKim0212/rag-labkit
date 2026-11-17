"""Frozen files: resuming an experiment on changed inputs, and editing a file an evaluated experiment used."""

import json
import os
import shutil
import subprocess

import pytest

from labkit import freeze
from labkit.cli import main
from labkit.config import config_chain, load_config
from labkit.cost import record_call
from labkit.env import TRACING_VARS
from labkit.harness import evaluate_experiment, grade_experiment, run_experiment
from labkit.project import load_project

BASELINE = """
name: 000_baseline
system: fake
run: {concurrency: 1}
answer: {prompt: prompts/answer_v1.md}
retrieval: {method: vector, k: 5}
corpus: {pages: corpus/pages.jsonl, pdf_dir: 01_data, label: vector, missing: nowhere/file.txt, outside: ../outside.txt}
"""
PRICES = {"m": {"input": 1, "output": 1}}


class Fake:
    def retrieve(self, question, scope, steps=None):
        return []

    def run(self, question, scope):
        steps = {}
        record_call(steps, node="generate", model="m", input_tokens=10, output_tokens=5)
        return {"status": "answered", "answer": "a", "context": [], "steps": steps, "n_searches": 1, "citations": []}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    return path


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    for var in TRACING_VARS:
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    root = tmp_path / "proj"
    write(tmp_path / "outside.txt", "not part of the project")
    write(root / "labkit.yaml", "golden: {files: [g.jsonl]}\n")
    write(root / "configs" / "baseline.yaml", BASELINE)
    write(root / "configs" / "pricing.yaml", "models:\n  m: {input: 1.0, output: 1.0}\n")
    write(root / "prompts" / "answer_v1.md", "Answer from the context.\n")
    write(root / "corpus" / "pages.jsonl", '{"page": 1}\n')
    (root / "01_data").mkdir()
    write(root / "golden" / "g.jsonl", "".join(
        json.dumps({"qid": f"Q{i}", "set": "s", "split": "dev", "question": f"q{i}?", "answerable": True}) + "\n"
        for i in (1, 2, 3)))
    monkeypatch.chdir(root)
    return root


def baseline(root):
    return load_config(root / "configs" / "baseline.yaml", root=root)


def run(root, name, limit=None, cfg=None):
    return run_experiment(cfg or baseline(root), "dev", experiment=name, system=Fake(), limit=limit, root=root)


# ---------------------------------------------------------------- what counts as an input file

def test_referenced_files_are_the_project_files_a_run_reads(proj):
    project = load_project(proj)
    assert freeze.referenced_files(baseline(proj), project, proj) == [
        "configs/baseline.yaml", "corpus/pages.jsonl", "golden/g.jsonl", "prompts/answer_v1.md"]
    # not files: plain words, a folder, a missing path, and a file outside the project
    write(proj / "configs" / "010-x.yaml", "base: baseline.yaml\nname: 010_x\nretrieval: {k: 8}\n")
    variant = load_config(proj / "configs" / "010-x.yaml", root=proj)
    assert "configs/010-x.yaml" in freeze.referenced_files(variant, project, proj)     # the variant and its parents
    assert [p.name for p in config_chain(proj / "configs" / "010-x.yaml")] == ["010-x.yaml", "baseline.yaml"]


def test_line_endings_do_not_change_a_hash(proj):
    before = freeze.file_hash(proj / "prompts" / "answer_v1.md")
    write(proj / "prompts" / "answer_v1.md", "Answer from the context.\r\n")
    assert freeze.file_hash(proj / "prompts" / "answer_v1.md") == before
    write(proj / "prompts" / "answer_v1.md", "Answer from the context!\n")
    assert freeze.file_hash(proj / "prompts" / "answer_v1.md") != before


# ---------------------------------------------------------------- within one experiment: always

def six_questions(root):
    """A real partial run needs more questions than a smoke test may cover (cards.SMOKE_LIMIT)."""
    write(root / "golden" / "g.jsonl", "".join(
        json.dumps({"qid": f"Q{i}", "set": "s", "split": "dev", "question": f"q{i}?", "answerable": True}) + "\n"
        for i in range(1, 7)))


def test_files_named_inside_lists_and_nested_values_are_frozen_too(proj):
    """`flatten` stopped at lists, so a prompt listed under a key was never hashed and could be edited freely."""
    six_questions(proj)
    for name in "abc":
        write(proj / "prompts" / f"{name}.md", f"{name}\n")
    write(proj / "configs" / "baseline.yaml",
          BASELINE + "rerank: {prompts: [prompts/a.md, prompts/b.md]}\nsteps: [{name: x, prompt: prompts/c.md}]\n")
    assert {"prompts/a.md", "prompts/b.md", "prompts/c.md"} <= set(
        freeze.referenced_files(baseline(proj), load_project(proj), proj))
    run(proj, "dev_x", limit=4)
    for name in "bc":
        write(proj / "prompts" / f"{name}.md", "edited\n")
        with pytest.raises(ValueError, match=rf"prompts/{name}.md changed since dev_x started"):
            run(proj, "dev_x")
        write(proj / "prompts" / f"{name}.md", f"{name}\n")
    assert run(proj, "dev_x")["ran"] == 2


def test_a_deleted_input_is_a_change_when_resuming(proj):
    """The check only looked at files that exist now, so a removed prompt resumed without a word."""
    six_questions(proj)
    run(proj, "dev_x", limit=4)
    (proj / "prompts" / "answer_v1.md").unlink()
    with pytest.raises(ValueError, match=r"prompts/answer_v1.md deleted since dev_x started"):
        run(proj, "dev_x")
    write(proj / "prompts" / "answer_v1.md", "Answer from the context.\n")
    assert run(proj, "dev_x")["ran"] == 2


def test_changed_since_treats_a_missing_file_as_changed_and_ignores_unrecorded_ones(tmp_path):
    f = write(tmp_path / "p.md", "x")
    digest = freeze.file_hash(f)
    assert freeze.changed_since({"p.md": digest}, tmp_path) == []
    f.unlink()
    assert freeze.changed_since({"p.md": digest}, tmp_path) == ["p.md"]
    assert freeze.changed_since(None, tmp_path) == []


def make_dir_link(link, target):
    """A directory symlink, or a junction on Windows (no privilege needed); the test is skipped where neither works."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)  # bytes: the
        # console prints in the OS code page (Korean here), and decoding it as text made a reader thread fail
        if done.returncode == 0:
            return
    pytest.skip("cannot create a directory link here")


def drop_dir_link(link):
    try:
        os.rmdir(link)            # a junction, or a directory symlink on Windows: removes the link, not the target
    except OSError:
        os.unlink(link)


def test_a_directory_link_retargeted_between_two_parts_of_a_batch_is_noticed(proj):
    """Hash keys were the resolved targets, so pointing `prompts/cur` from v1 to v2 renamed the key instead of
    changing a hash: two questions ran on v1 and four on v2 with `file_check: []`."""
    six_questions(proj)
    write(proj / "prompts" / "v1" / "answer.md", "version one\n")
    write(proj / "prompts" / "v2" / "answer.md", "version two\n")
    make_dir_link(proj / "prompts" / "cur", proj / "prompts" / "v1")
    write(proj / "configs" / "baseline.yaml", BASELINE.replace("prompts/answer_v1.md", "prompts/cur/answer.md"))
    files = freeze.referenced_files(baseline(proj), load_project(proj), proj)
    assert "prompts/cur/answer.md" in files and "prompts/v1/answer.md" not in files       # the path as written
    run(proj, "dev_x", limit=4)
    drop_dir_link(proj / "prompts" / "cur")
    make_dir_link(proj / "prompts" / "cur", proj / "prompts" / "v2")
    with pytest.raises(ValueError, match=r"prompts/cur/answer.md changed since dev_x started"):
        run(proj, "dev_x")


def edited_after_a_baseline_was_evaluated(proj):
    run(proj, "dev_000_baseline")
    evaluate_experiment("dev_000_baseline", prices=PRICES)
    write(proj / "prompts" / "answer_v1.md", "EDITED after the baseline was evaluated.\n")


def test_a_second_name_for_a_frozen_file_is_still_frozen(proj):
    """Frozen files were compared by the name the config writes, so `alias/answer_v1.md` (a link to prompts/) ran on the
    edited prompt that `prompts/answer_v1.md` was refused for."""
    edited_after_a_baseline_was_evaluated(proj)
    with pytest.raises(ValueError, match="prompts/answer_v1.md was used by the evaluated experiment"):
        run(proj, "dev_r2", limit=3)                                                  # the control: the real path
    make_dir_link(proj / "alias", proj / "prompts")
    write(proj / "configs" / "alt.yaml", "base: baseline.yaml\nname: alt\nanswer: {prompt: alias/answer_v1.md}\n")
    alt = load_config(proj / "configs" / "alt.yaml", root=proj)
    assert "alias/answer_v1.md" in freeze.referenced_files(alt, load_project(proj), proj)
    with pytest.raises(ValueError, match=r"alias/answer_v1.md was used by the evaluated experiment dev_000_baseline"):
        run(proj, "dev_alt", limit=3, cfg=alt)


def test_a_different_case_of_a_frozen_file_is_still_frozen_on_a_case_insensitive_file_system(proj):
    if not (proj / "PROMPTS").exists():
        pytest.skip("a case-sensitive file system: PROMPTS is another folder")
    edited_after_a_baseline_was_evaluated(proj)
    write(proj / "configs" / "alt.yaml", "base: baseline.yaml\nname: alt\nanswer: {prompt: Prompts/Answer_v1.md}\n")
    alt = load_config(proj / "configs" / "alt.yaml", root=proj)
    with pytest.raises(ValueError, match="was used by the evaluated experiment dev_000_baseline"):
        run(proj, "dev_alt", limit=3, cfg=alt)


def test_a_second_name_for_an_unedited_frozen_file_is_fine(proj):
    run(proj, "dev_000_baseline")
    evaluate_experiment("dev_000_baseline", prices=PRICES)
    make_dir_link(proj / "alias", proj / "prompts")
    write(proj / "configs" / "alt.yaml", "base: baseline.yaml\nname: alt\nanswer: {prompt: alias/answer_v1.md}\n")
    alt = load_config(proj / "configs" / "alt.yaml", root=proj)
    assert run(proj, "dev_alt", limit=3, cfg=alt)["ran"] == 3                          # same content: nothing to refuse


def test_a_link_to_a_file_outside_the_project_is_still_not_an_input(proj, tmp_path):
    outside = tmp_path / "elsewhere"
    write(outside / "answer.md", "not ours\n")
    make_dir_link(proj / "prompts" / "out", outside)
    write(proj / "configs" / "baseline.yaml", BASELINE.replace("prompts/answer_v1.md", "prompts/out/answer.md"))
    assert not [f for f in freeze.referenced_files(baseline(proj), load_project(proj), proj) if "out/answer" in f]


def test_a_path_written_with_dots_or_absolutely_is_one_name(proj):
    write(proj / "configs" / "baseline.yaml",
          BASELINE.replace("prompts/answer_v1.md", "prompts/../prompts/answer_v1.md")
          + f"extra: {{abs: {(proj / 'prompts' / 'answer_v1.md').as_posix()!r}}}\n")
    files = freeze.referenced_files(baseline(proj), load_project(proj), proj)
    assert files.count("prompts/answer_v1.md") == 1                                       # not three spellings


# ---------------------------------------------------------------- labkit.yaml settings that decide what a result means

def test_resuming_after_the_golden_list_or_max_score_changed_is_refused(proj):
    """Questions 5 and 6 were once scored against golden_v2 in the same experiment, with no warning."""
    six_questions(proj)
    run(proj, "dev_x", limit=4)
    shutil.copy(proj / "golden" / "g.jsonl", proj / "golden" / "g2.jsonl")
    write(proj / "labkit.yaml", "golden: {files: [g2.jsonl]}\n")
    with pytest.raises(ValueError, match=r"labkit.yaml.*golden_files: \['g.jsonl'\] -> \['g2.jsonl'\]"):
        run(proj, "dev_x")
    write(proj / "labkit.yaml", "golden: {files: [g.jsonl]}\nmax_score: 4\n")
    with pytest.raises(ValueError, match=r"max_score: 2 -> 4"):
        run(proj, "dev_x")
    write(proj / "labkit.yaml", "golden: {files: [g.jsonl]}\nreport: {columns: [scores.correctness_mean]}\n")
    assert run(proj, "dev_x")["ran"] == 2                              # what the report shows is not part of a result


def test_grade_and_evaluate_refuse_a_changed_max_score_or_golden_list(proj):
    """Grades were given on one scale and the questions read from one file: neither may change before scoring."""
    run(proj, "dev_x")
    judge = lambda item, run_, cfg: {"correctness": 1}  # noqa: E731
    write(proj / "labkit.yaml", "golden: {files: [g.jsonl]}\nmax_score: 4\n")
    with pytest.raises(ValueError, match=r"max_score: 2 -> 4"):
        grade_experiment("dev_x", grade_fn=judge, root=proj)
    with pytest.raises(ValueError, match=r"max_score: 2 -> 4"):
        evaluate_experiment("dev_x", prices=PRICES, root=proj)
    write(proj / "labkit.yaml", "golden: {files: [g.jsonl]}\n")
    grade_experiment("dev_x", grade_fn=judge, root=proj)
    assert evaluate_experiment("dev_x", prices=PRICES, root=proj)["max_score"] == 2


def test_the_summary_records_the_settings_it_was_scored_under(proj):
    write(proj / "labkit.yaml", "golden: {files: [g.jsonl]}\nanswerable_default: a\n"
                                "behaviors:\n  a: {group: answer, statuses: [answered]}\n")
    run(proj, "dev_x")
    s = evaluate_experiment("dev_x", prices=PRICES, root=proj)
    assert s["project_settings"] == {"golden_files": ["g.jsonl"], "max_score": 2, "answerable_default": "a",
                                     "behaviors": {"a": {"group": "answer", "statuses": ["answered"]}}}
    assert s["metrics"]["status_ok"] == 1.0
    # behaviors only read the stored runs, so correcting the table and re-evaluating (no LLM) is allowed ...
    write(proj / "labkit.yaml", "golden: {files: [g.jsonl]}\nanswerable_default: a\n"
                                "behaviors:\n  a: {group: answer, statuses: [abstained]}\n")
    s = evaluate_experiment("dev_x", prices=PRICES, root=proj, overwrite=True)
    assert s["metrics"]["status_ok"] == 0.0 and s["project_settings"]["behaviors"]["a"]["statuses"] == ["abstained"]


def test_resuming_on_a_changed_config_is_refused(proj):
    six_questions(proj)
    run(proj, "dev_x", limit=4)                                                       # 4 of 6: an interrupted batch
    write(proj / "configs" / "baseline.yaml", BASELINE.replace("k: 5", "k: 6"))
    with pytest.raises(ValueError) as e:
        run(proj, "dev_x")
    assert "dev_x was started with config" in str(e.value) and "--name" in str(e.value)
    assert "configs/baseline.yaml changed since dev_x started" in str(e.value)       # the edited file is named too


def test_resuming_after_a_prompt_edit_is_refused_even_before_evaluation(proj):
    six_questions(proj)
    run(proj, "dev_x", limit=4)
    write(proj / "prompts" / "answer_v1.md", "A different instruction.\n")
    with pytest.raises(ValueError, match=r"prompts/answer_v1.md changed since dev_x started"):
        run(proj, "dev_x")
    write(proj / "prompts" / "answer_v1.md", "Answer from the context.\n")           # restoring it makes resuming fine again
    assert run(proj, "dev_x")["ran"] == 2


def test_a_smoke_test_is_no_part_of_the_experiment_so_editing_after_it_is_fine(proj):
    """Read the smoke output, find the prompt wrong, fix it, run for real under the same name."""
    first = run(proj, "dev_x", limit=1)
    assert first["smoke"] is True and first["ran"] == 1
    write(proj / "prompts" / "answer_v1.md", "A better instruction.\n")
    again = run(proj, "dev_x")
    assert again["smoke"] is False and again["ran"] == 3 and again["skipped_done"] == 0   # Q1 is run again, for real
    s = evaluate_experiment("dev_x", prices=PRICES, root=proj)
    assert s["n_questions"] == 3 and s["file_check"]["changed_since_run"] == []         # hashes come from the real run
    assert s["file_hashes"]["prompts/answer_v1.md"] == freeze.file_hash(proj / "prompts" / "answer_v1.md")
    assert s["cost"]["wasted_usd"] > 0                                                  # the smoke call was paid for


# ---------------------------------------------------------------- across experiments: once evaluated

def test_unevaluated_experiments_freeze_nothing(proj):
    run(proj, "dev_a", limit=1)                                                       # a smoke test, never evaluated
    write(proj / "prompts" / "answer_v1.md", "A fixed instruction.\n")
    assert run(proj, "dev_b")["ran"] == 3                                             # a new experiment may use the edit


def test_an_evaluated_experiment_freezes_its_inputs(proj):
    run(proj, "dev_000_baseline")
    evaluate_experiment("dev_000_baseline", prices=PRICES)
    assert run(proj, "dev_000_baseline_r2")["ran"] == 3                               # a noise rerun on unchanged files is fine

    write(proj / "prompts" / "answer_v1.md", "A different instruction.\n")
    with pytest.raises(ValueError) as e:
        run(proj, "dev_001_other")
    message = str(e.value)
    assert "prompts/answer_v1.md was used by the evaluated experiment dev_000_baseline" in message
    assert "copy it to a new version" in message and "remove results/dev_000_baseline/" in message

    shutil.rmtree(proj / "results" / "dev_000_baseline")                              # declared invalid: no longer freezes
    assert run(proj, "dev_001_other")["ran"] == 3


def test_golden_and_baseline_config_are_frozen_too(proj):
    run(proj, "dev_000_baseline")
    evaluate_experiment("dev_000_baseline", prices=PRICES)
    gold = proj / "golden" / "g.jsonl"
    original = gold.read_text(encoding="utf-8")
    write(gold, original + json.dumps({"qid": "Q4", "set": "s", "split": "dev", "question": "q4?"}) + "\n")
    with pytest.raises(ValueError, match=r"golden/g.jsonl was used by the evaluated experiment dev_000_baseline"):
        run(proj, "dev_002_new")
    write(gold, original)
    write(proj / "configs" / "baseline.yaml", BASELINE + "# a comment is an edit too\n")
    with pytest.raises(ValueError, match=r"configs/baseline.yaml was used by the evaluated experiment"):
        run(proj, "dev_002_new")


def test_a_teammates_evaluated_experiment_freezes_files_here(proj):
    """results/ is committed, so someone else's summary.json counts even though this machine never ran it."""
    write(proj / "results" / "dev_007_theirs" / "summary.json",
          json.dumps({"experiment": "dev_007_theirs", "file_hashes": {"prompts/answer_v1.md": "deadbeef00"}}))
    with pytest.raises(ValueError, match="dev_007_theirs"):
        run(proj, "dev_000_baseline")
    write(proj / "results" / "dev_008_old" / "summary.json", json.dumps({"experiment": "dev_008_old"}))   # no hashes: ignored
    shutil.rmtree(proj / "results" / "dev_007_theirs")
    assert run(proj, "dev_000_baseline")["ran"] == 3


def test_a_committed_summary_freezes_a_rerun_of_the_same_experiment_name_on_a_fresh_clone(proj):
    """results/ is committed and out/ is not: on a fresh clone the run log is empty, but the summary is there."""
    run(proj, "dev_x")
    evaluate_experiment("dev_x", prices=PRICES, root=proj)
    (proj / "out" / "runs.db").unlink()
    write(proj / "prompts" / "answer_v1.md", "A different instruction.\n")
    with pytest.raises(ValueError, match="evaluated experiment dev_x"):
        run(proj, "dev_x")


def test_baseline_yaml_is_an_input_even_for_a_config_with_no_base(proj):
    write(proj / "configs" / "020-solo.yaml", "name: 020_solo\nsystem: fake\n")
    solo = load_config(proj / "configs" / "020-solo.yaml", root=proj)
    assert "configs/baseline.yaml" in freeze.referenced_files(solo, load_project(proj), proj)


def test_every_config_in_a_base_chain_is_an_input(proj):
    write(proj / "configs" / "010-a.yaml", "base: baseline.yaml\nname: 010_a\nretrieval: {k: 8}\n")
    write(proj / "configs" / "011-b.yaml", "base: 010-a.yaml\nname: 011_b\nretrieval: {k: 9}\n")
    cfg = load_config(proj / "configs" / "011-b.yaml", root=proj)
    assert "configs/010-a.yaml" in freeze.referenced_files(cfg, load_project(proj), proj)       # the middle one too


def test_the_config_hash_ignores_what_a_config_says_about_its_own_parent(proj):
    """`changed_from_base` is meta: a variant that changes nothing hashes like the config it builds on."""
    write(proj / "configs" / "010-a.yaml", "base: baseline.yaml\nname: 010_a\nretrieval: {k: 8}\n")
    write(proj / "configs" / "011-b.yaml", "base: 010-a.yaml\nname: 011_b\n")
    a, b = (load_config(proj / "configs" / f"{n}.yaml", root=proj) for n in ("010-a", "011-b"))
    assert a["config_hash"] == b["config_hash"]


# ---------------------------------------------------------------- evaluate and the CLI

def test_evaluate_records_the_hashes_and_flags_edits_made_after_the_run(proj, capsys):
    run(proj, "dev_x")
    write(proj / "prompts" / "answer_v1.md", "Edited after the run.\n")
    assert main(["--root", str(proj), "evaluate", "--experiment", "dev_x"]) == 0
    err = capsys.readouterr().err
    assert "prompts/answer_v1.md changed since dev_x ran" in err
    summary = json.loads((proj / "results" / "dev_x" / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["file_hashes"]) == {"configs/baseline.yaml", "corpus/pages.jsonl", "golden/g.jsonl",
                                           "prompts/answer_v1.md"}   # not the folder, the missing path, or the outside file
    assert summary["file_check"] == {"changed_since_run": ["prompts/answer_v1.md"]}
    # the recorded hash is the content the run used, not the edited one
    assert summary["file_hashes"]["prompts/answer_v1.md"] != freeze.file_hash(proj / "prompts" / "answer_v1.md")


def test_an_unedited_run_evaluates_without_a_warning(proj, capsys):
    run(proj, "dev_x")
    grade_experiment("dev_x", grade_fn=lambda item, run_, cfg: {"correctness": 1}, root=proj)  # ungraded is a warning too
    assert main(["--root", str(proj), "evaluate", "--experiment", "dev_x"]) == 0
    assert "warning" not in capsys.readouterr().err
    summary = json.loads((proj / "results" / "dev_x" / "summary.json").read_text(encoding="utf-8"))
    assert summary["file_check"] == {"changed_since_run": []}


def test_the_cli_reports_a_frozen_file_as_one_line(proj, capsys):
    run(proj, "dev_000_baseline")
    evaluate_experiment("dev_000_baseline", prices=PRICES)
    write(proj / "prompts" / "answer_v1.md", "A different instruction.\n")
    assert main(["--root", str(proj), "run", "--config", "configs/baseline.yaml", "--split", "dev",
                 "--name", "dev_000_baseline_r2"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("labkit: error: frozen files:") and "Traceback" not in err
