"""End-to-end run -> grade -> evaluate -> report with a fake system, in a temp project (no LLM, no network)."""

import json

import pytest

from conftest import without_small_n

from labkit.config import load_config
from labkit.cost import record_call
from labkit.golden import behavior_of, load_golden, scope_of
from labkit.compare import compare_experiments
from labkit.harness import (COLUMNS, SplitLocked, _quantile, build_report, check_grade, evaluate_experiment,
                            grade_experiment, report_columns, run_experiment)
from labkit.eval.runlog import RunLog
from labkit.judges import JUDGES, judge_for
from labkit.measures import MEASURES, register_measure
from labkit.plugins import load_modules
from labkit.project import load_project
from labkit.systems import SYSTEMS, build_system, register_system
from test_gates import FILLED_CARD

PAGE = {"chunk_id": "c1", "source": "a.pdf", "page": 3, "content": "휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"}
OTHER = {"chunk_id": "c2", "source": "b.pdf", "page": 1, "content": "관련 없는 내용"}
GOLDEN = [
    {"qid": "POS001", "set": "base_positive", "split": "dev", "question": "휴면성 증권계좌는?", "answerable": True,
     "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}],
     "evidence": ["휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"]},
    {"qid": "NEG001", "set": "base_negative", "split": "dev", "question": "오늘 최고금리는?", "answerable": False,
     "expected_behavior": "abstain_and_redirect", "targets": [], "evidence": []},
    {"qid": "POS002", "set": "base_positive", "split": "test", "question": "test question", "answerable": True,
     "expected_behavior": "answer_with_citation", "targets": [{"file": "a.pdf", "page": 3}], "evidence": []},
]
LABKIT_YAML = """
golden: {files: [provided.jsonl]}
behaviors:
  answer_with_citation: {group: answer, statuses: [answered]}
  abstain_and_redirect: {group: abstain, statuses: [abstained]}
"""
BASELINE = """
name: 000_baseline
system: fake
run: {concurrency: 2}
corpus:
  set_scope: {base_positive: A, base_negative: A}
judge: {name: fake-judge, model: j, prompt: prompts/judge.md}
"""
PRICES = {"m": {"input": 0.75, "output": 3.75}, "j": {"input": 0.75, "output": 3.75}}


class FakeSystem:
    def __init__(self):
        self.scopes = []

    def retrieve(self, question, scope, steps=None):
        self.scopes.append(scope)
        return [PAGE, OTHER] if "휴면" in question else [OTHER]

    def run(self, question, scope):
        steps = {}
        record_call(steps, node="generate", model="m", input_tokens=1000, output_tokens=200)
        context = self.retrieve(question, scope)
        if "휴면" in question:
            return {"status": "answered", "answer": "6개월 [1]", "context": context, "steps": steps, "n_searches": 1,
                    "citations": [{"chunk_id": "c1", "source": "a.pdf", "page": 3}]}
        return {"status": "abstained", "answer": "자료 없음", "context": context, "steps": steps, "n_searches": 1,
                "citations": []}


def fake_grade(item, run, cfg):
    calls = []
    record_call({"calls": calls}, node="judge", model="j", input_tokens=500, output_tokens=50)
    return {"correctness": 2 if run["status"] == "answered" else 1, "reason": "fake", "calls": calls}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def fake_judge():
    JUDGES["fake-judge"] = fake_grade
    yield
    JUDGES.pop("fake-judge", None)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    write(tmp_path / "labkit.yaml", LABKIT_YAML)
    write(tmp_path / "configs" / "baseline.yaml", BASELINE)
    write(tmp_path / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in GOLDEN))
    write(tmp_path / "prompts" / "judge.md", "Grade 0 to 2.\n")   # BASELINE's judge.prompt: a run refuses without it
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_full_loop(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    exp, system = "dev_000_baseline", FakeSystem()
    r = run_experiment(cfg, "dev", experiment=exp, system=system)
    assert r["ran"] == 2 and r["statuses"] == {"answered": 1, "abstained": 1}
    assert set(system.scopes) == {"A"}                                    # scope comes from corpus.set_scope
    again = run_experiment(cfg, "dev", experiment=exp, system=system)
    assert again["ran"] == 0 and again["skipped_done"] == 2               # resume skips finished questions

    g = grade_experiment(exp)                                             # judge resolved from cfg["judge"]["name"]
    assert g["graded_now"] == 2
    assert grade_experiment(exp)["graded_now"] == 0                       # grading resumes too

    s = evaluate_experiment(exp, with_runs=True, prices=PRICES)
    assert s["metrics"]["status_ok"] == 1.0
    assert s["scores"]["correctness_mean"] == pytest.approx((1.0 + 0.5) / 2)
    assert s["metrics"]["page_hit@5"] == 1 and s["metrics"]["evid@5"] == 1.0
    assert s["metrics"]["citation_on_target"] == 1.0
    assert s["by_set"]["base_positive"]["metrics"]["page_hit@5"] == 1
    assert s["cost"]["usd"] == pytest.approx(2 * (1000 * 0.75 + 200 * 3.75) / 1e6)
    assert s["cost"]["judge_usd"] > 0 and s["calls"]["llm_per_question"] == 1
    assert s["compare_to"] == "dev_000_baseline" and s["by_set"]["base_negative"]["n"] == 1
    out = project / "results" / exp
    assert {p.name for p in out.iterdir()} == {"summary.json", "metrics.jsonl", "grades.jsonl", "runs.jsonl"}
    grades = [json.loads(line) for line in (out / "grades.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {g["judge_model"] for g in grades} == {"j"}                    # judge setup recorded from cfg["judge"]
    with pytest.raises(FileExistsError):                                  # results are not overwritten silently
        evaluate_experiment(exp, prices=PRICES)

    ret = "dev_000_baseline_retrieval"
    run_experiment(cfg, "dev", experiment=ret, retrieval_only=True, system=system)
    sr = evaluate_experiment(ret, prices=PRICES)
    assert sr["n_graded"] == 0 and sr["calls"]["llm"] == 0 and sr["metrics"]["gold@5"] == 1
    assert "status_ok" not in sr["metrics"]                               # retrieval-only runs have no answer checks

    rep = build_report()
    assert rep["experiments"] == 2
    md = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "dev_000_baseline_retrieval" in md and "Experiment results" in md
    assert "<table" in (project / "out" / "report.html").read_text(encoding="utf-8")


def test_test_split_is_locked_everywhere(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    with pytest.raises(SplitLocked):
        run_experiment(cfg, "test", experiment="test_000_baseline", system=FakeSystem())
    with pytest.raises(SplitLocked):
        grade_experiment("test_000_baseline", grade_fn=fake_grade)
    with pytest.raises(SplitLocked):
        evaluate_experiment("test_000_baseline", prices=PRICES)
    with pytest.raises(SplitLocked):
        build_report(include_test=True)
    r = run_experiment(cfg, "test", experiment="test_000_baseline", final=True, system=FakeSystem())
    assert r["ran"] == 1


def test_failing_question_is_recorded_and_not_counted_done(project):
    class Flaky(FakeSystem):
        def run(self, question, scope):
            if "휴면" in question:
                raise RuntimeError("boom")
            return super().run(question, scope)

    cfg = load_config(project / "configs" / "baseline.yaml")
    r = run_experiment(cfg, "dev", experiment="dev_flaky", system=Flaky())
    assert r["statuses"] == {"error": 1, "abstained": 1}
    again = run_experiment(cfg, "dev", experiment="dev_flaky", system=FakeSystem())
    assert again["skipped_done"] == 1 and again["ran"] == 1               # only the failed question is retried

    class Stub(FakeSystem):
        def run(self, question, scope):
            raise NotImplementedError("stage not built")
    with pytest.raises(NotImplementedError):                              # a stub is a bug, not a failed question
        run_experiment(cfg, "dev", experiment="dev_stub", system=Stub())


def test_without_behaviors_the_status_check_is_skipped(project):
    write(project / "labkit.yaml", "golden: {files: [provided.jsonl]}\nmax_score: 1\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x", grade_fn=lambda i, r, c: {"correctness": 1 if r["status"] == "answered" else 0, "calls": []})
    s = evaluate_experiment("dev_x", prices=PRICES)
    assert "status_ok" not in s["metrics"] and s["max_score"] == 1
    assert s["scores"]["correctness_mean"] == 0.5 and s["scores"]["fully_correct"] == 0.5


def test_scope_and_behavior_rules(project):
    item = {"qid": "q", "set": "s", "answerable": True}
    assert scope_of(item, {}) == "default"
    assert scope_of({**item, "scope": "B"}, {}) == "B"
    assert scope_of(item, {"corpus": {"set_scope": {"s": "A"}}}) == "A"
    with pytest.raises(KeyError):                                         # a set missing from set_scope is a mistake
        scope_of({**item, "set": "other"}, {"corpus": {"set_scope": {"s": "A"}}})

    project_cfg = load_project()
    assert behavior_of(GOLDEN[1], project_cfg) == ("abstain", {"abstained"})
    with pytest.raises(KeyError, match="no `behaviors` entry"):
        behavior_of({"qid": "q", "expected_behavior": "unknown"}, project_cfg)
    with pytest.raises(KeyError):                                         # answerable without a behavior and no default
        behavior_of(item, project_cfg)
    assert behavior_of(item, {**project_cfg, "answerable_default": "answer_with_citation"})[0] == "answer"
    assert behavior_of(item, {"behaviors": {}}) == (None, None)


def test_project_rejects_malformed_behaviors(project):
    write(project / "labkit.yaml", "behaviors:\n  x: {group: answer}\n")
    with pytest.raises(ValueError, match="statuses"):
        load_project()


def test_golden_loads_only_the_listed_files(project):
    assert [i["qid"] for i in load_golden(split="dev")] == ["POS001", "NEG001"]
    assert [i["qid"] for i in load_golden(sets=["base_negative"])] == ["NEG001"]
    # a stray file in golden/ (same qids, or not golden at all) must not add, duplicate, or break anything
    write(project / "golden" / "extra.jsonl", json.dumps({**GOLDEN[0], "split": "test"}) + "\n")
    write(project / "golden" / "evidence.jsonl", json.dumps({"page": 3, "text": "no qid here"}) + "\n")
    assert len(load_golden()) == 3


def test_golden_errors_name_the_problem(project):
    write(project / "labkit.yaml", "golden: {files: [provided.jsonl, extra.jsonl]}\n")
    write(project / "golden" / "extra.jsonl", json.dumps(GOLDEN[0]) + "\n")
    with pytest.raises(ValueError, match=r"duplicate qid 'POS001' in provided.jsonl and extra.jsonl"):
        load_golden()
    write(project / "labkit.yaml", "golden: {files: [provided.jsonl, missing.jsonl]}\n")
    with pytest.raises(FileNotFoundError, match="missing.jsonl"):         # a typo must not silently drop questions
        load_golden()
    write(project / "labkit.yaml", "golden: {files: [evidence_no_qid.jsonl]}\n")
    write(project / "golden" / "evidence_no_qid.jsonl", '{"page": 3}\n')
    with pytest.raises(ValueError, match=r"evidence_no_qid.jsonl:1: every golden item needs a `qid`"):
        load_golden()
    write(project / "golden" / "evidence_no_qid.jsonl", "{broken\n")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_golden()
    write(project / "labkit.yaml", "")                                    # default file name is golden.jsonl
    with pytest.raises(FileNotFoundError, match="golden.jsonl"):
        load_golden()


@pytest.mark.parametrize("split", ["Test", " test", "dev ", "train", None])
def test_a_golden_item_with_a_missing_or_misspelled_split_is_an_error_not_a_dropped_question(project, split):
    """Such an item matches neither --split dev nor --split test, so it used to vanish from every run and the
    experiment still said complete."""
    item = {**GOLDEN[0], "qid": "BAD1"}
    item.pop("split") if split is None else item.update(split=split)
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in [*GOLDEN, item]))
    with pytest.raises(ValueError, match=r'provided.jsonl:4: BAD1: `split` must be exactly "dev" or "test"'):
        load_golden()
    assert len(load_golden(check_split=False)) == 4                       # golden-check reads them to report each one


def test_an_unknown_set_in_sets_is_an_error_not_half_a_dev_run(project):
    with pytest.raises(ValueError, match=r"unknown set\(s\) typo for the dev split.*base_negative, base_positive"):
        load_golden(split="dev", sets=["base_positive", "typo"])
    with pytest.raises(ValueError, match="unknown set"):                  # a set that exists only in the other split
        load_golden(split="test", sets=["base_negative"])
    assert [i["qid"] for i in load_golden(split="dev", sets=["base_negative"])] == ["NEG001"]


def test_custom_measure_judge_field_and_report_columns(project):
    """The extension path: a project adds a measure, a judge field, and a report column without touching labkit."""
    @register_measure("answer_length")
    def answer_length(item, run, project):
        return {"answer_chars": len(run.get("answer") or "")}

    def judge_with_extra(item, run, cfg):
        return {**fake_grade(item, run, cfg), "faithfulness": 1.0 if run["status"] == "answered" else 0.5,
                "grounded": run["status"] == "answered", "reason_text": "not a number"}

    JUDGES["fake-judge"] = judge_with_extra
    write(project / "labkit.yaml", LABKIT_YAML + """
measures: [retrieval, answer_length]
report:
  columns:
    - scores.correctness_mean
    - metrics.answer_chars
    - {header: Faithfulness, path: metrics.faithfulness, format: "{:.2f}"}
    - {header: Grounded, path: metrics.grounded, format: "{:.0%}"}
    - {header: Cost, path: cost.usd_per_question, format: "{:.5f}", higher_is_better: false}
""")
    try:
        cfg = load_config(project / "configs" / "baseline.yaml")
        run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
        grade_experiment("dev_000_baseline")
        s = evaluate_experiment("dev_000_baseline", prices=PRICES)
        assert s["metrics"]["answer_chars"] == pytest.approx((len("6개월 [1]") + len("자료 없음")) / 2)
        assert s["metrics"]["faithfulness"] == 0.75 and s["metrics"]["grounded"] == 0.5
        assert "status_ok" not in s["metrics"] and "reason_text" not in s["metrics"]   # status not requested; strings skipped
        rows = [json.loads(line) for line in
                (project / "results" / "dev_000_baseline" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
        assert {r["qid"]: r["faithfulness"] for r in rows} == {"POS001": 1.0, "NEG001": 0.5}   # per-question columns
        assert [c[0] for c in report_columns(load_project())] == ["correctness_mean", "answer_chars", "Faithfulness",
                                                                  "Grounded", "Cost"]
        build_report()
        md = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")
        assert "| Faithfulness |" in md and "0.75" in md
    finally:
        MEASURES.pop("answer_length", None)


def test_unknown_measure_and_default_columns(project):
    write(project / "labkit.yaml", LABKIT_YAML + "measures: [retrieval, nope]\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    with pytest.raises(KeyError, match="unknown measure 'nope'"):
        evaluate_experiment("dev_x", prices=PRICES)
    assert report_columns(load_project()) is COLUMNS
    write(project / "labkit.yaml", "report: {columns: [{header: no path}]}\n")
    with pytest.raises(ValueError, match="needs a `path`"):
        report_columns(load_project())


def test_plugins_load_project_modules(project):
    import sys
    write(project / "src" / "lk_plugin_pkg" / "__init__.py", "")
    write(project / "src" / "lk_plugin_pkg" / "systems.py", """
from labkit.cost import record_call
from labkit.judges import register_judge
from labkit.measures import register_measure
from labkit.systems import register_system


@register_system("plugin-sys")
def make(cfg):
    return object()


@register_judge("plugin-judge")
def grade(item, run, cfg):
    return {"correctness": 2}


@register_measure("plugin-measure")
def measure(item, run, project):
    return {"plugin_value": 1}
""")
    write(project / "labkit.yaml", "pythonpath: [src]\nmodules: [lk_plugin_pkg.systems]\n")
    try:
        assert load_modules() == ["lk_plugin_pkg.systems"]
        assert "plugin-sys" in SYSTEMS and "plugin-judge" in JUDGES and "plugin-measure" in MEASURES
        load_modules()                                                    # importing again is a no-op
        write(project / "labkit.yaml", "pythonpath: [src]\nmodules: [lk_plugin_pkg.missing]\n")
        with pytest.raises(ImportError, match=r"`modules` lists 'lk_plugin_pkg.missing'"):
            load_modules()
    finally:
        for name, registry in (("plugin-sys", SYSTEMS), ("plugin-judge", JUDGES), ("plugin-measure", MEASURES)):
            registry.pop(name, None)
        for module in [m for m in sys.modules if m.startswith("lk_plugin_pkg")]:
            del sys.modules[module]
        sys.path[:] = [p for p in sys.path if not p.endswith("src")]


def test_system_registry():
    @register_system("t-sys")
    def make(cfg):
        return FakeSystem()
    try:
        assert isinstance(build_system({"system": "t-sys"}), FakeSystem)
        with pytest.raises(ValueError, match="already registered"):
            register_system("t-sys")(lambda cfg: None)
        with pytest.raises(KeyError, match="unknown system"):
            build_system({"system": "nope"})
    finally:
        SYSTEMS.pop("t-sys", None)


def test_judge_resolution():
    JUDGES.clear()
    cfg = {"judge": {"name": "j1"}}
    with pytest.raises(KeyError, match="unknown judge"):
        judge_for(cfg)
    JUDGES["j1"] = lambda item, run, cfg: {"correctness": 1}
    try:
        assert judge_for(cfg)(None, None, None) == {"correctness": 1}
        assert judge_for({})(None, None, None) == {"correctness": 1}      # a lone registered judge needs no name
        JUDGES["j2"] = lambda item, run, cfg: {"correctness": 2}
        with pytest.raises(KeyError, match="no judge.name"):              # ambiguous: refuse to guess
            judge_for({})
    finally:
        JUDGES.pop("j1", None)
        JUDGES.pop("j2", None)


# ---------------------------------------------------------------- results must not look better than they are

def graded_and_evaluated(experiment, **kwargs):
    grade_experiment(experiment)
    return evaluate_experiment(experiment, prices=PRICES, **kwargs)


def five_dev_questions(project):
    """More dev questions than a smoke test may cover, so `limit=4` is a real partial run."""
    items = [{**GOLDEN[0], "qid": f"P{i}"} for i in range(1, 6)]
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))


def test_a_partial_run_is_marked_incomplete_until_it_is_finished(project):
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")
    exp = "dev_000_baseline"
    run_experiment(cfg, "dev", experiment=exp, limit=4, system=FakeSystem())     # 4 of the 5 dev questions
    s = graded_and_evaluated(exp)
    assert s["n_expected"] == 5 and s["n_questions"] == 4 and s["complete"] is False
    assert s["incomplete"] == ["1 of 5 expected questions never ran"]
    assert s["scores"]["correctness_mean"] == 1.0                                 # a partial score looks perfect: hence the flag
    build_report()
    assert "4/5 incomplete" in (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")

    assert run_experiment(cfg, "dev", experiment=exp, system=FakeSystem())["ran"] == 1   # resumes: only the missing one
    s = graded_and_evaluated(exp, overwrite=True)
    assert s["complete"] is True and s["incomplete"] == [] and s["n_questions"] == s["n_expected"] == 5


def test_smoke_rows_belong_to_no_result(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    exp = "dev_000_baseline"
    smoke = run_experiment(cfg, "dev", experiment=exp, limit=1, system=FakeSystem())
    assert smoke["smoke"] is True and smoke["ran"] == 1
    assert grade_experiment(exp)["graded_now"] == 0                              # nothing to grade: smoke is not a run
    with pytest.raises(ValueError, match="only smoke-test runs"):
        evaluate_experiment(exp, prices=PRICES)                                  # and a result can never be made of it

    real = run_experiment(cfg, "dev", experiment=exp, system=FakeSystem())
    assert real["smoke"] is False and real["ran"] == 2 and real["skipped_done"] == 0   # the smoke question runs again
    s = graded_and_evaluated(exp)
    assert s["complete"] is True and s["n_questions"] == 2
    one_call = (1000 * 0.75 + 200 * 3.75) / 1e6
    assert s["cost"]["wasted_usd"] == pytest.approx(one_call)                    # the smoke call was still paid for
    assert s["cost"]["usd"] == pytest.approx(2 * one_call)


def test_changing_the_worker_count_does_not_block_resuming(project):
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")                     # run: {concurrency: 2}
    run_experiment(cfg, "dev", experiment="dev_000_baseline", limit=4, system=FakeSystem())
    write(project / "configs" / "slower.yaml", "base: baseline.yaml\nname: slower\nrun: {concurrency: 1}\n")
    slower = load_config(project / "configs" / "slower.yaml")
    assert slower["changed"] == {} and slower["config_hash"] == cfg["config_hash"]
    again = run_experiment(slower, "dev", experiment="dev_000_baseline", system=FakeSystem())
    assert again["ran"] == 1 and again["skipped_done"] == 4                      # same setup, other thread count
    s = graded_and_evaluated("dev_000_baseline")
    assert s["complete"] is True and s["changed"] == {} and s["changed_from_base"] == {}


def test_a_failure_seen_only_in_a_smoke_test_is_not_a_failed_question(project):
    class FailsOnPositive(FakeSystem):
        def run(self, question, scope):
            if "휴면" in question:
                return {"status": "error", "error": "smoke-test failure"}
            return super().run(question, scope)

    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_neg", limit=1, system=FailsOnPositive())      # POS001 fails in the smoke test
    run_experiment(cfg, "dev", experiment="dev_neg", sets=["base_negative"], system=FakeSystem())
    s = graded_and_evaluated("dev_neg")                                                      # the real run never covered POS001
    assert s["complete"] is True and s["n_errors"] == 0 and s["n_expected"] == 1


def test_repeating_a_smoke_test_cannot_run_a_whole_variant_without_its_card(project):
    five_dev_questions(project)
    write(project / "configs" / "010-variant.yaml", "base: baseline.yaml\nname: 010\nretrieval: {k: 9}\n")
    variant = load_config(project / "configs" / "010-variant.yaml")
    first = run_experiment(variant, "dev", experiment="dev_010", limit=3, system=FakeSystem())
    second = run_experiment(variant, "dev", experiment="dev_010", limit=3, system=FakeSystem())
    assert first["ran"] == second["ran"] == 3 and second["skipped_done"] == 0    # the same three questions, not the next three
    with pytest.raises(ValueError, match="only smoke-test runs"):
        evaluate_experiment("dev_010", prices=PRICES)
    with pytest.raises(ValueError, match="names no `card:`"):                    # four questions is no smoke test
        run_experiment(variant, "dev", experiment="dev_010", limit=4, system=FakeSystem())


def test_the_environment_comes_from_real_runs_and_mixed_commits_are_flagged(project, monkeypatch):
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")

    def at(commit):
        monkeypatch.setattr("labkit.harness.environment", lambda root: {"git": {"commit": commit, "dirty": False}})

    at("smoke00")
    run_experiment(cfg, "dev", experiment="dev_x", limit=1, system=FakeSystem())     # smoke, from another commit
    at("aaaaaaa")
    run_experiment(cfg, "dev", experiment="dev_x", limit=4, system=FakeSystem())
    s = graded_and_evaluated("dev_x")
    assert s["environment"]["git"]["commit"] == "aaaaaaa"
    assert s["environment_check"] == {"git_commits": ["aaaaaaa"], "code_versions": ["aaaaaaa"], "mixed_commits": False}

    at("bbbbbbb")                                                                    # the batch was finished after a commit
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    s = graded_and_evaluated("dev_x", overwrite=True)
    assert s["environment_check"] == {"git_commits": ["aaaaaaa", "bbbbbbb"], "code_versions": ["aaaaaaa", "bbbbbbb"],
                                      "mixed_commits": True}


def test_an_uncommitted_code_edit_between_two_parts_of_a_batch_is_a_mixed_result(project, monkeypatch, capsys):
    """Questions 5 and 6 ran on code edited since question 4, on the same commit, and nothing said so."""
    from labkit.cli import main
    five_dev_questions(project)
    write(project / "configs" / "pricing.yaml", "models:\n  m: {input: 0.75, output: 3.75}\n  j: {input: 0.75, output: 3.75}\n")
    cfg = load_config(project / "configs" / "baseline.yaml")

    def at(changes):
        monkeypatch.setattr("labkit.harness.environment",
                            lambda root: {"git": {"commit": "aaaaaaa", "dirty": changes is not None, "changes": changes}})

    at("1111111111")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", limit=4, system=FakeSystem())
    at("2222222222")                                                                 # the plugin was fixed, not committed
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    grade_experiment("dev_000_baseline")
    assert main(["evaluate", "--experiment", "dev_000_baseline"]) == 0
    s = json.loads((project / "results" / "dev_000_baseline" / "summary.json").read_text(encoding="utf-8"))
    assert s["environment_check"] == {"git_commits": ["aaaaaaa"], "mixed_commits": True,
                                      "code_versions": ["aaaaaaa+1111111111", "aaaaaaa+2222222222"]}
    err = capsys.readouterr().err
    assert "different versions of the code" in err and "aaaaaaa+2222222222" in err
    at("1111111111")                                                                 # edited back: one version again
    run_experiment(cfg, "dev", experiment="dev_clean", system=FakeSystem())
    assert graded_and_evaluated("dev_clean")["environment_check"]["mixed_commits"] is False


def test_unfinished_grading_and_a_restricted_set_are_visible(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_a", system=FakeSystem())
    s = evaluate_experiment("dev_a", prices=PRICES)                              # run, but never graded
    assert s["complete"] is False and s["incomplete"] == ["2 of 2 answers are not graded"]

    run_experiment(cfg, "dev", experiment="dev_pos", sets=["base_positive"], system=FakeSystem())
    s = graded_and_evaluated("dev_pos")                                          # --sets A: only A is expected
    assert s["n_expected"] == 1 and s["complete"] is True

    retrieval = run_experiment(cfg, "dev", experiment="dev_ret", retrieval_only=True, system=FakeSystem())
    assert retrieval["ran"] == 2
    assert evaluate_experiment("dev_ret", prices=PRICES)["complete"] is True      # nothing to grade in a retrieval run


def test_a_retrieval_only_run_and_an_answer_run_never_share_an_experiment(project):
    """Resuming a --retrieval-only run as an answer run once scored 2 of 6 questions and said complete."""
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", limit=4, retrieval_only=True, system=FakeSystem())
    with pytest.raises(ValueError, match=r"dev_x already holds retrieval-only runs.*another --name"):
        run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    run_experiment(cfg, "dev", experiment="dev_y", system=FakeSystem())
    with pytest.raises(ValueError, match=r"dev_y already holds answer runs"):
        run_experiment(cfg, "dev", experiment="dev_y", retrieval_only=True, system=FakeSystem())
    # a retrieval-only smoke test is no part of the experiment (labkit.eval.runlog.is_smoke), so it blocks nothing
    run_experiment(cfg, "dev", experiment="dev_z", limit=1, retrieval_only=True, system=FakeSystem())
    assert run_experiment(cfg, "dev", experiment="dev_z", system=FakeSystem())["ran"] == 5


def test_evaluate_refuses_rows_of_both_kinds_in_one_experiment(project):
    """Rows written by an older labkit (or by hand) may already mix; the first row must not decide for all."""
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")
    log = RunLog(project / "out" / "runs.db")
    selection = {"split": "dev", "sets": None}
    for qid, retrieval_only in (("P1", True), ("P2", True), ("P3", False)):
        log.save({"status": "retrieval_only" if retrieval_only else "answered", "system": "fake", "question": "q",
                  "config": {**cfg, "retrieval_only": retrieval_only, "selection": selection}}, experiment="dev_m", qid=qid)
    with pytest.raises(ValueError, match=r"dev_m mixes retrieval-only and answer runs"):
        evaluate_experiment("dev_m", prices=PRICES)


def test_golden_changed_after_the_run_makes_the_result_incomplete_not_complete(project):
    """d6 moved from dev to test after the run: the summary said n_expected 5, n_questions 6, complete true."""
    items = [{**GOLDEN[0], "qid": f"P{i}"} for i in range(1, 7)]
    gold = project / "golden" / "provided.jsonl"
    write(gold, "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    items[5]["split"] = "test"
    write(gold, "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))
    s = graded_and_evaluated("dev_x")
    assert s["n_expected"] == 5 and s["n_questions"] == 6
    assert s["complete"] is False and "P6" in s["incomplete"][0] and "not in the dev split" in s["incomplete"][0]


def test_a_question_that_left_the_golden_files_is_named_not_a_bare_keyerror(project):
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    gold = project / "golden" / "provided.jsonl"
    lines = gold.read_text(encoding="utf-8").splitlines(keepends=True)
    write(gold, "".join(lines[:4]))                                                   # P5 is gone
    for step in (lambda: grade_experiment("dev_x"), lambda: evaluate_experiment("dev_x", prices=PRICES)):
        with pytest.raises(ValueError, match=r"dev_x has runs of questions that are no longer in the golden files: P5"):
            step()


def test_the_report_marks_an_experiment_whose_inputs_changed_after_it_ran(project):
    write(project / "prompts" / "judge.md", "Grade 0 to 2.\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    graded_and_evaluated("dev_x")
    build_report()
    assert "inputs changed" not in (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")
    write(project / "prompts" / "judge.md", "Grade 0 to 5, generously.\n")
    build_report()
    md = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")
    assert "inputs changed since the run" in md and "inputs changed" in (project / "out" / "report.html").read_text(encoding="utf-8")


BAD_NAMES = ["dev/x", "dev_z/../../../escape", "../x", "dev\\x", "dev x", ".hidden", "-dev", "dev_x.", "dev:x", "",
             "a..b", "nul", "CON", "com1", "Lpt9.txt", "x" * 81]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_an_experiment_name_cannot_point_outside_results(project, name):
    """A name becomes results/<name>/ and out/grades/<name>.jsonl: `dev_z/../../..` once wrote summary.json,
    metrics.jsonl and grades.jsonl outside the project, and `dev/x` hid from the freeze and the report."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    with pytest.raises(ValueError, match="not a valid experiment name"):
        run_experiment(cfg, "dev", experiment=name, system=FakeSystem())
    with pytest.raises(ValueError, match="not a valid experiment name"):
        grade_experiment(name, grade_fn=fake_grade)
    with pytest.raises(ValueError, match="not a valid experiment name"):
        evaluate_experiment(name, prices=PRICES)
    with pytest.raises(ValueError, match="not a valid experiment name"):
        compare_experiments(name, "dev_000_baseline")
    assert not (project.parent / "escape").exists() and not (project / "results").exists()


def test_a_grades_suffix_cannot_point_outside_either(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    for suffix in ("/../x", "\\x", " x", "_r2", "regrade1", "__"):      # a suffix starts with two underscores
        with pytest.raises(ValueError, match="not a valid grades suffix"):
            grade_experiment("dev_000_baseline", grade_fn=fake_grade, suffix=suffix)
    assert grade_experiment("dev_000_baseline", grade_fn=fake_grade, suffix="__regrade1")["graded_now"] == 2


def test_a_grade_counts_only_for_the_run_it_graded(project):
    """Grades were matched by qid. After the run log was lost and the experiment rerun with other answers, the new
    answers were reported complete, graded with the old grades."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x", grade_fn=lambda item, run, c: {"correctness": 1, "calls": fake_grade(item, run, c)["calls"]})
    (project / "out" / "runs.db").unlink()                                      # the log is local: a fresh clone has none
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    s = evaluate_experiment("dev_x", prices=PRICES)
    assert s["complete"] is False and s["n_graded"] == 0 and s["incomplete"] == ["2 of 2 answers are not graded"]
    assert not (project / "results" / "dev_x" / "grades.jsonl").exists()        # nothing of the old grades is exported

    again = grade_experiment("dev_x", grade_fn=lambda item, run, c: {"correctness": 2, "calls": fake_grade(item, run, c)["calls"]})
    assert again["graded_now"] == 2 and again["already_graded"] == 0            # the new runs are graded, not skipped
    assert grade_experiment("dev_x", grade_fn=fake_grade)["graded_now"] == 0    # and now they count as done
    s = evaluate_experiment("dev_x", prices=PRICES, overwrite=True)
    assert s["complete"] is True and s["n_graded"] == 2 and s["scores"]["correctness_mean"] == 1.0   # the new grades
    exported = [json.loads(line) for line in (project / "results" / "dev_x" / "grades.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {g["run_id"] for g in exported} == {r["run_id"] for r in RunLog(project / "out" / "runs.db").latest("dev_x")}
    one_pass = 2 * (500 * 0.75 + 50 * 3.75) / 1e6
    assert s["cost"]["judge_usd"] == pytest.approx(2 * one_pass)                # the replaced grades were paid for too


def test_grading_refuses_a_judge_prompt_that_changed_since_the_run(project):
    """The judge prompt is frozen at step 1, but a grade row recorded only its path: editing the file between two
    grading sessions (or before a regrade, which compares two gradings) put two standards in one result."""
    judge_md = project / "prompts" / "judge.md"
    write(judge_md, "Grade 0 to 2.\n")
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    write(judge_md, "Grade 0 to 5, generously.\n")
    for suffix in ("", "__regrade1"):                                              # a regrade is graded by the same rule
        with pytest.raises(ValueError, match=r"judge prompt prompts/judge.md changed since dev_x ran"):
            grade_experiment("dev_x", suffix=suffix)
    with pytest.raises(ValueError, match="changed since"):                           # also when a grade_fn is passed
        grade_experiment("dev_x", grade_fn=fake_grade)
    assert not list((project / "out" / "grades").glob("*.jsonl"))                    # nothing was graded or created
    write(judge_md, "Grade 0 to 2.\n")                                              # restored: fine again
    assert grade_experiment("dev_x")["graded_now"] == 2
    rows = [json.loads(line) for line in (project / "out" / "grades" / "dev_x.jsonl").read_text(encoding="utf-8").splitlines()]
    from labkit.freeze import file_hash
    assert {r["judge_prompt_hash"] for r in rows} == {file_hash(judge_md)}          # which prompt graded it is on record
    judge_md.unlink()
    with pytest.raises(ValueError, match=r"judge prompt prompts/judge.md was deleted since dev_x ran"):
        grade_experiment("dev_x", suffix="__regrade1")


def test_only_the_judge_prompt_blocks_grading_not_other_inputs(project):
    """Editing the answer prompt after a run is flagged at evaluation and in the report; it does not change how the
    answers are graded, so it must not stop grading."""
    write(project / "prompts" / "answer.md", "Answer from the context.\n")
    write(project / "configs" / "baseline.yaml", BASELINE + "answer: {prompt: prompts/answer.md}\n")
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    write(project / "prompts" / "answer.md", "A different instruction.\n")
    assert grade_experiment("dev_x")["graded_now"] == 2
    assert evaluate_experiment("dev_x", prices=PRICES)["file_check"]["changed_since_run"] == ["prompts/answer.md"]


def test_a_run_logged_without_its_judge_prompt_is_graded_only_once_the_prompt_exists(project):
    """Only files present when the run started are hashed. A run can no longer start without its judge prompt (round
    7), but a log written before that has no hash of it: grading it needs the prompt now, and the grade rows record
    which text graded them, instead of judge_prompt_hash None."""
    import sqlite3
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    with sqlite3.connect(project / "out" / "runs.db") as db:                      # what an older log looks like
        for rowid, text in db.execute("SELECT rowid, config FROM runs").fetchall():
            config = json.loads(text)
            config["file_hashes"].pop("prompts/judge.md")
            db.execute("UPDATE runs SET config = ? WHERE rowid = ?", (json.dumps(config), rowid))
    (project / "prompts" / "judge.md").unlink()
    with pytest.raises(ValueError, match="judge.prompt names prompts/judge.md, which does not exist"):
        grade_experiment("dev_x")
    write(project / "prompts" / "judge.md", "Written afterwards.\n")
    assert grade_experiment("dev_x")["graded_now"] == 2
    rows = [json.loads(line) for line in (project / "out" / "grades" / "dev_x.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(r["judge_prompt_hash"] for r in rows)


def test_a_grade_row_without_a_run_id_matches_nothing(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    write(project / "out" / "grades" / "dev_x.jsonl", "".join(
        json.dumps({"qid": q, "correctness": 2}) + "\n" for q in ("POS001", "NEG001")))
    s = evaluate_experiment("dev_x", prices=PRICES)
    assert s["n_graded"] == 0 and s["complete"] is False
    assert grade_experiment("dev_x", grade_fn=fake_grade)["graded_now"] == 2


@pytest.mark.parametrize("bad", [None, "text", {"answer": "x"}, {"status": ""}, {"status": 5}, {"status": None}])
def test_a_system_result_without_a_status_is_a_failed_question_not_a_finished_one(project, bad):
    """It was saved with status NULL and counted as done: a resume skipped it, grade and evaluate scored it, and the
    batch had stopped with the one-word error `status`."""
    class NoStatus(FakeSystem):
        def run(self, question, scope):
            steps = {}
            record_call(steps, node="generate", model="m", input_tokens=1000, output_tokens=200)   # the call was paid
            if isinstance(bad, dict):
                return {**bad, "steps": steps}
            return bad
    cfg = load_config(project / "configs" / "baseline.yaml")
    result = run_experiment(cfg, "dev", experiment="dev_x", system=NoStatus())
    assert result["statuses"] == {"error": 2}                                    # the batch finished; both are failures
    rows = RunLog(project / "out" / "runs.db").load("dev_x")
    assert {r["status"] for r in rows} == {"error"} and all("no `status`" in r["error"] for r in rows)
    if isinstance(bad, dict):
        assert all(len(r["steps"]["calls"]) == 1 for r in rows)                   # the cost of the call is kept
    assert run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())["ran"] == 2   # failed: retried, not done


def test_grading_an_experiment_that_does_not_exist_is_an_error_and_creates_nothing(project, capsys):
    """`labkit grade --experiment typo` printed graded_now 0 and left an empty out/grades/typo.jsonl."""
    from labkit.cli import main
    with pytest.raises(ValueError, match=r"no runs logged for dev_typo"):
        grade_experiment("dev_typo", grade_fn=fake_grade)
    assert not (project / "out" / "grades").exists() or not list((project / "out" / "grades").glob("dev_typo*"))
    assert main(["grade", "--experiment", "dev_typo"]) == 1
    assert "no runs logged for dev_typo" in capsys.readouterr().err


def test_a_report_cell_with_a_pipe_or_a_line_break_stays_one_table_cell(project):
    """A multi-line value in a changed setting split a SUMMARY.md row, and a `|` added columns."""
    write(project / "results" / "dev_odd" / "summary.json", json.dumps({
        "experiment": "dev_odd", "split": "dev", "n_questions": 2, "complete": True, "card": "experiments/a|b.md",
        "changed": {"prompt.text": {"from": "x|y", "to": "line one\nline two | three"}}}))
    build_report()
    lines = (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines()
    header = next(l for l in lines if l.startswith("| Experiment"))
    row = next(l for l in lines if l.startswith("| dev_odd"))
    assert row.replace("\\|", "").count("|") == header.count("|")                    # same number of cells as the header
    assert "line one line two" in row and "x\\|y" in row


def test_the_local_files_labkit_writes_have_unix_line_endings_too(project):
    summary_of(project)
    build_report()
    assert b"\r\n" not in (project / "out" / "report.html").read_bytes()
    assert b"\r\n" not in (project / "out" / "grades" / "dev_000_baseline.jsonl").read_bytes()


def test_the_summary_records_which_sets_it_covers(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_all", system=FakeSystem())
    assert graded_and_evaluated("dev_all")["sets"] is None                      # the whole split
    run_experiment(cfg, "dev", experiment="dev_some", sets=["base_positive"], system=FakeSystem())
    assert graded_and_evaluated("dev_some")["sets"] == ["base_positive"]


def test_a_cut_off_summary_is_named_in_one_clear_error_not_a_bare_json_message(project):
    """A 0-byte summary.json made `labkit run` and `labkit report` fail for EVERY experiment with
    `Expecting value: line 1 column 1`."""
    from labkit.cli import main
    cfg = load_config(project / "configs" / "baseline.yaml")
    write(project / "results" / "dev_cut" / "summary.json", "")
    with pytest.raises(ValueError, match=r"dev_cut.summary.json is not a readable summary.*remove the folder dev_cut"):
        run_experiment(cfg, "dev", experiment="dev_a", system=FakeSystem())
    with pytest.raises(ValueError, match="not a readable summary"):
        build_report()
    write(project / "results" / "dev_ok" / "summary.json", "{}")
    with pytest.raises(ValueError, match="not a readable summary"):
        compare_experiments("dev_cut", "dev_ok")
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev", "--name", "dev_b"]) == 1


def test_evaluate_writes_atomically_so_a_crash_never_leaves_a_half_summary(project, monkeypatch):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    first = graded_and_evaluated("dev_x")
    summary = project / "results" / "dev_x" / "summary.json"
    before = summary.read_text(encoding="utf-8")

    def broken(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr("labkit.harness.os.replace", broken)
    with pytest.raises(OSError, match="disk full"):
        evaluate_experiment("dev_x", prices=PRICES, overwrite=True)
    assert summary.read_text(encoding="utf-8") == before                          # the old, whole file is still there
    assert not list((project / "results" / "dev_x").glob("*.tmp"))               # and no temporary file is left
    assert json.loads(before)["n_questions"] == first["n_questions"]


def test_a_nan_anywhere_in_a_grade_is_refused_not_only_at_the_top_level(project):
    with pytest.raises(ValueError, match=r"extra\.x=nan.*finite"):
        check_grade({"correctness": 1, "extra": {"x": float("nan")}}, "q", 2)
    with pytest.raises(ValueError, match=r"faith\[0\]=inf"):
        check_grade({"correctness": 1, "faith": [float("inf")]}, "q", 2)
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())

    def bad_calls(item, run, c):                                                  # a hand-built call record with NaN
        return {"correctness": 1, "calls": [{"model": "j", "input_tokens": float("nan"), "output_tokens": 1}]}
    with pytest.raises(ValueError, match=r"calls\[0\]\.input_tokens=nan"):
        grade_experiment("dev_x", grade_fn=bad_calls)
    assert not (project / "out" / "grades" / "dev_x.jsonl").read_text(encoding="utf-8").strip()


def test_results_files_are_valid_json_even_when_a_system_logged_a_nan(project):
    class Noisy(FakeSystem):
        def run(self, question, scope):
            result = super().run(question, scope)
            result["steps"]["score"] = float("nan")                               # a system's own diagnostic
            return result
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=Noisy())
    grade_experiment("dev_x")
    evaluate_experiment("dev_x", prices=PRICES, with_runs=True)
    for name in ("summary.json", "metrics.jsonl", "grades.jsonl", "runs.jsonl"):
        text = (project / "results" / "dev_x" / name).read_text(encoding="utf-8")
        assert "NaN" not in text and "Infinity" not in text, name
        json.loads(text if name == "summary.json" else text.splitlines()[0])       # valid JSON, strictly


def test_results_files_have_unix_line_endings(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x")
    evaluate_experiment("dev_x", prices=PRICES, with_runs=True)
    for name in ("summary.json", "metrics.jsonl", "grades.jsonl", "runs.jsonl"):
        assert b"\r\n" not in (project / "results" / "dev_x" / name).read_bytes(), name


def test_evaluating_again_leaves_no_stale_export_beside_the_new_summary(project):
    """A 4-row runs.jsonl stayed next to a 6-question summary, and a grades.jsonl stayed after the grades were gone."""
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", limit=4, system=FakeSystem())
    grade_experiment("dev_x")
    evaluate_experiment("dev_x", prices=PRICES, with_runs=True)
    out = project / "results" / "dev_x"
    assert len((out / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    evaluate_experiment("dev_x", prices=PRICES, overwrite=True)                  # no --with-runs: refreshed all the same
    assert len((out / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 5
    (project / "out" / "grades" / "dev_x.jsonl").unlink()                        # grades gone
    s = evaluate_experiment("dev_x", prices=PRICES, overwrite=True)
    assert s["n_graded"] == 0 and not (out / "grades.jsonl").exists()


def test_a_suffix_cannot_make_the_grades_file_of_another_experiment(project):
    """`--suffix _r2` on dev_000_baseline wrote the grades file of the experiment dev_000_baseline_r2."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    for name in ("dev_000_baseline__regradex", "dev_000_baseline"):                # the longer name exists first
        run_experiment(cfg, "dev", experiment=name, system=FakeSystem())
    with pytest.raises(ValueError, match=r"dev_000_baseline__regradex is an experiment of its own"):
        grade_experiment("dev_000_baseline", grade_fn=fake_grade, suffix="__regradex")
    assert not (project / "out" / "grades" / "dev_000_baseline__regradex.jsonl").exists()


def test_a_new_experiment_cannot_be_named_like_another_ones_regrade_file(project):
    """The same collision from the other side: after `grade --suffix __regrade1`, an experiment named
    dev_000_baseline__regrade1 shared that grades file, and each one's judge cost included the other's."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    grade_experiment("dev_000_baseline", grade_fn=fake_grade, suffix="__regrade1")
    with pytest.raises(ValueError, match=r"dev_000_baseline__regrade1.*grades file of dev_000_baseline"):
        run_experiment(cfg, "dev", experiment="dev_000_baseline__regrade1", system=FakeSystem())
    assert run_experiment(cfg, "dev", experiment="dev_000_baseline_r2", system=FakeSystem())["ran"] == 2   # `_r2` is fine


def test_a_baseline_run_set_by_set_is_the_whole_split(project):
    """Run --sets a, then --sets b, then the rest: the summary kept `sets: [a, b]` and the baseline check said the
    baseline covered only some sets, although together they were all of dev."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", sets=["base_positive"], system=FakeSystem())
    assert graded_and_evaluated("dev_x")["sets"] == ["base_positive"]                 # only one so far: a subset
    run_experiment(cfg, "dev", experiment="dev_x", sets=["base_negative"], system=FakeSystem())
    s = graded_and_evaluated("dev_x", overwrite=True)
    assert s["sets"] is None and s["complete"] is True and s["n_questions"] == 2      # together: the whole split


def test_the_report_shows_no_difference_between_a_subset_and_the_whole_split(project):
    """`--sets s1` against the whole-split baseline showed `1.000 (+0.250)` for an identical config and system."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    graded_and_evaluated("dev_000_baseline")
    run_experiment(cfg, "dev", experiment="dev_001_sub", sets=["base_positive"], system=FakeSystem())
    graded_and_evaluated("dev_001_sub")
    run_experiment(cfg, "dev", experiment="dev_002_all", system=FakeSystem())
    graded_and_evaluated("dev_002_all")
    build_report()
    rows = {line.split("|")[1].strip(): line for line in
            (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines() if line.startswith("| dev_")}
    assert "different sets (base_positive vs the whole split)" in rows["dev_001_sub"]
    assert "(+" not in rows["dev_001_sub"] and "(-" not in rows["dev_001_sub"]
    assert "different sets" not in rows["dev_002_all"]                                  # same questions: a real comparison
    assert "different sets" in (project / "out" / "report.html").read_text(encoding="utf-8")


def test_two_experiment_names_that_differ_only_by_case_never_coexist(project):
    """On Windows and macOS results/Test_Final and results/test_final are one folder: a dev run named Test_Final
    replaced the complete test result of test_final."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    with pytest.raises(ValueError, match=r"'DEV_X' differs only by case from the existing experiment 'dev_x'"):
        run_experiment(cfg, "dev", experiment="DEV_X", system=FakeSystem())
    write(project / "results" / "dev_folder_only" / "summary.json", "{}")                # known from results/ too
    with pytest.raises(ValueError, match="differs only by case"):
        run_experiment(cfg, "dev", experiment="Dev_Folder_Only", system=FakeSystem())
    assert run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())["ran"] == 0   # the same name is fine


def test_a_test_summary_in_the_folder_is_protected_whatever_split_the_new_run_has(project):
    """The guard looked at the split of the NEW run only: a dev experiment whose folder held a complete test
    summary could overwrite it."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_y", system=FakeSystem())
    grade_experiment("dev_y", grade_fn=fake_grade)
    summary = project / "results" / "dev_y" / "summary.json"
    write(summary, json.dumps({"experiment": "dev_y", "split": "test", "complete": True}))
    with pytest.raises(SplitLocked, match="test result"):
        evaluate_experiment("dev_y", prices=PRICES, overwrite=True)
    write(summary, json.dumps({"experiment": "dev_y", "split": "test", "complete": False}))
    with pytest.raises(SplitLocked, match="holds a test result and this run is on the dev split"):   # unfinished, but a
        evaluate_experiment("dev_y", prices=PRICES, overwrite=True)                                   # dev run is not it
    write(summary, json.dumps({"experiment": "dev_y", "split": "dev", "complete": False}))    # an unfinished dev one may be redone
    assert evaluate_experiment("dev_y", prices=PRICES, overwrite=True)["split"] == "dev"


@pytest.mark.parametrize("name", ["dev_000_baseline", "dev_004-hybrid_retrieval", "dev_000_baseline_r2", "dev_v1.2",
                                  "dev_기준선", "pilot_a"])
def test_ordinary_experiment_names_are_fine(project, name):
    cfg = load_config(project / "configs" / "baseline.yaml")
    assert run_experiment(cfg, "dev", experiment=name, system=FakeSystem())["ran"] == 2


def test_index_build_cost_is_reported_through_labkit_yaml_not_a_python_argument(project):
    """index_dir_fn existed but the CLI never passed one, so index_build_usd was null for everyone."""
    from labkit.index import index_key
    write(project / "labkit.yaml", LABKIT_YAML + "index_keys: [corpus]\nindex_dir: out/index/{index_key}/{scope}\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x")
    assert evaluate_experiment("dev_x", prices=PRICES)["cost"]["index_build_usd"] is None      # no build.json yet
    key = index_key(cfg, load_project())
    calls = []
    record_call({"calls": calls}, node="embed", model="m", input_tokens=2_000_000, output_tokens=0, kind="embedding")
    write(project / "out" / "index" / key / "A" / "build.json", json.dumps({"calls": calls}))
    s = evaluate_experiment("dev_x", prices=PRICES, overwrite=True)
    assert s["cost"]["index_build_usd"] == pytest.approx(2 * 0.75)                            # 2M tokens at $0.75 per 1M


def test_index_build_cost_is_null_while_any_scope_has_no_build_record(project):
    """Two scopes with a build.json for only one printed that one's cost as the total, with no sign one was missing."""
    from labkit.index import index_key
    write(project / "labkit.yaml", LABKIT_YAML + "index_keys: [corpus]\nindex_dir: out/index/{index_key}/{scope}\n")
    write(project / "configs" / "baseline.yaml", BASELINE.replace("{base_positive: A, base_negative: A}",
                                                                   "{base_positive: A, base_negative: B}"))
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x")
    key = index_key(cfg, load_project())
    calls = []
    record_call({"calls": calls}, node="embed", model="m", input_tokens=1_000_000, output_tokens=0, kind="embedding")
    write(project / "out" / "index" / key / "A" / "build.json", json.dumps({"calls": calls}))
    assert evaluate_experiment("dev_x", prices=PRICES)["cost"]["index_build_usd"] is None      # B is unknown
    write(project / "out" / "index" / key / "B" / "build.json", json.dumps({"calls": calls}))
    assert evaluate_experiment("dev_x", prices=PRICES, overwrite=True)["cost"]["index_build_usd"] == pytest.approx(1.5)


def test_index_build_cost_is_null_without_index_dir(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    assert graded_and_evaluated("dev_x")["cost"]["index_build_usd"] is None


@pytest.mark.parametrize("value", ["3", "[a]", "out/{oops}/x", "out/{}/x", "out/{index_key!z}"])
def test_index_dir_must_be_a_path_using_only_known_placeholders(project, value):
    write(project / "labkit.yaml", f"index_dir: {value}\n")
    with pytest.raises(ValueError, match="index_dir"):
        load_project()


def test_regrade_costs_are_reported_not_lost(project):
    """`labkit grade --suffix __regrade1` calls the judge for every answer; nothing recorded what that cost."""
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    grade_experiment("dev_000_baseline")
    grade_experiment("dev_000_baseline", suffix="__regrade1")
    s = evaluate_experiment("dev_000_baseline", prices=PRICES)
    one_pass = 2 * (500 * 0.75 + 50 * 3.75) / 1e6                                             # two answers, fake_grade's calls
    assert s["cost"]["judge_usd"] == pytest.approx(one_pass)
    assert s["cost"]["judge_regrade_usd"] == pytest.approx(one_pass)
    assert evaluate_experiment("dev_000_baseline", prices=PRICES, overwrite=True)["cost"]["judge_regrade_usd"] == pytest.approx(one_pass)


def test_a_nan_in_an_extra_judge_field_is_refused_like_a_bad_correctness(project):
    """Only `correctness` was checked: a NaN faithfulness was averaged into the summary as a bare NaN token, which
    is not valid JSON."""
    def nan_judge(item, run, cfg):
        return {"correctness": 1, "faithfulness": float("nan")}
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    with pytest.raises(ValueError, match=r"faithfulness=nan.*finite"):
        grade_experiment("dev_x", grade_fn=nan_judge)
    assert not (project / "out" / "grades" / "dev_x.jsonl").read_text(encoding="utf-8").strip()   # nothing was saved
    write(project / "out" / "grades" / "dev_x.jsonl",                                          # a hand-edited file too
          "".join(json.dumps({"qid": r["qid"], "run_id": r["run_id"], "correctness": 1, "faithfulness": float("nan")}) + "\n"
                  for r in RunLog(project / "out" / "runs.db").latest("dev_x")))
    with pytest.raises(ValueError, match=r"faithfulness=nan"):
        evaluate_experiment("dev_x", prices=PRICES)


def test_a_measure_that_returns_nan_is_left_out_of_the_means(project):
    @register_measure("flaky")
    def flaky(item, run, project):
        return {"flaky_score": float("nan") if item["qid"] == "POS001" else 0.5}
    try:
        write(project / "labkit.yaml", LABKIT_YAML + "measures: [retrieval, flaky]\n")
        cfg = load_config(project / "configs" / "baseline.yaml")
        run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
        s = graded_and_evaluated("dev_x")
    finally:
        MEASURES.pop("flaky", None)
    assert s["metrics"]["flaky_score"] == 0.5
    for name in ("summary.json", "metrics.jsonl"):                       # both must stay valid JSON
        assert "NaN" not in (project / "results" / "dev_x" / name).read_text(encoding="utf-8")


def two_experiments_under_different_max_scores(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    graded_and_evaluated("dev_000_baseline")
    write(project / "labkit.yaml", LABKIT_YAML + "max_score: 4\n")           # a later experiment, on a 0..4 scale
    run_experiment(cfg, "dev", experiment="dev_001_x", system=FakeSystem())
    graded_and_evaluated("dev_001_x")


def test_compare_does_not_divide_both_experiments_by_whatever_labkit_yaml_says_now(project):
    """Each summary knows its own scale. Dividing a 0..2 result by today's 4 printed 0.500 for a baseline of 1.0."""
    two_experiments_under_different_max_scores(project)
    with pytest.raises(ValueError, match=r"dev_000_baseline was scored 0..2 and dev_001_x 0..4.*--max-score"):
        compare_experiments("dev_000_baseline", "dev_001_x")
    text = compare_experiments("dev_000_baseline", "dev_001_x", max_score=2)            # the user chose: allowed, flagged
    assert "WARNING: dev_000_baseline and dev_001_x were scored under different labkit.yaml settings (max_score)" in text
    assert "WARNING" not in without_small_n(compare_experiments("dev_000_baseline", "dev_000_baseline"))


def test_the_report_shows_no_difference_between_results_scored_under_different_settings(project):
    two_experiments_under_different_max_scores(project)
    build_report()
    rows = {line.split("|")[1].strip(): line for line in
            (project / "results" / "SUMMARY.md").read_text(encoding="utf-8").splitlines() if line.startswith("| dev_")}
    assert "different labkit.yaml settings (max_score)" in rows["dev_001_x"]
    assert "(+" not in rows["dev_001_x"] and "(-" not in rows["dev_001_x"]            # no delta against the 0..2 baseline
    assert "different labkit.yaml settings" not in rows["dev_000_baseline"]


def summary_of(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_000_baseline", system=FakeSystem())
    grade_experiment("dev_000_baseline")
    return evaluate_experiment("dev_000_baseline", prices=PRICES)


def test_tokens_per_question_counts_input_and_output(project):
    assert summary_of(project)["cost"]["tokens_per_question"] == 1200.0               # 1000 in + 200 out per question


def test_fully_correct_means_the_top_score_not_any_credit(project):
    assert summary_of(project)["scores"]["fully_correct"] == 0.5                      # grades 2 and 1 on a 0..2 scale


def test_cost_columns_are_not_averaged_into_metrics(project):
    leaked = {"usd", "input_tokens", "output_tokens", "latency_s", "n_llm_calls"} & set(summary_of(project)["metrics"])
    assert not leaked


def test_a_run_outside_git_records_no_commit_and_no_mixed_commits(project, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(project.parent))
    assert summary_of(project)["environment_check"] == {"git_commits": [], "code_versions": [], "mixed_commits": False}


def test_grading_never_calls_the_judge_for_a_retrieval_only_row(project):
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_r", retrieval_only=True,
                   system=FakeSystem())

    def boom(*args):
        raise AssertionError("the judge was called on a retrieval-only row")
    assert grade_experiment("dev_r", grade_fn=boom)["graded_now"] == 0


def test_a_complete_test_result_is_never_overwritten_but_an_unfinished_one_can_be_completed(project, capsys):
    """"Test split once": evaluate --final --overwrite replaced a finished test result with a fresh one. An
    evaluation made before grading was finished is not a result yet, so completing it stays possible."""
    import shutil
    from labkit.cli import main
    cfg = load_config(project / "configs" / "baseline.yaml")
    for name in ("test_000_baseline", "final_try"):                       # the second is a test run under a neutral name
        run_experiment(cfg, "test", experiment=name, final=True, system=FakeSystem())
        first = evaluate_experiment(name, prices=PRICES, final=True)       # evaluated before grading: incomplete
        assert first["complete"] is False
        with pytest.raises(FileExistsError):                              # without --overwrite nothing changes
            evaluate_experiment(name, prices=PRICES, final=True)
        grade_experiment(name, grade_fn=fake_grade, final=True)
        assert evaluate_experiment(name, prices=PRICES, final=True, overwrite=True)["complete"] is True   # finishing it
        before = (project / "results" / name / "summary.json").read_text(encoding="utf-8")
        with pytest.raises(SplitLocked, match=r"complete \(or unreadable\) test result.*never overwritten"):
            evaluate_experiment(name, prices=PRICES, final=True, overwrite=True)
        assert (project / "results" / name / "summary.json").read_text(encoding="utf-8") == before

    assert main(["evaluate", "--experiment", "test_000_baseline", "--final", "--overwrite"]) == 1
    err = capsys.readouterr().err
    assert "a test result is never overwritten" in err and "results/test_000_baseline" in err.replace("\\", "/")

    shutil.rmtree(project / "results" / "test_000_baseline")              # declaring it invalid is a visible, reviewed act
    assert evaluate_experiment("test_000_baseline", prices=PRICES, final=True)["complete"] is True


def test_the_test_overwrite_rule_does_not_touch_dev_and_is_cautious_about_odd_summaries(project):
    import shutil
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    graded_and_evaluated("dev_x")
    assert evaluate_experiment("dev_x", prices=PRICES, overwrite=True)["complete"] is True    # dev: as before
    run_experiment(cfg, "test", experiment="test_x", final=True, system=FakeSystem())
    grade_experiment("test_x", grade_fn=fake_grade, final=True)
    summary = project / "results" / "test_x" / "summary.json"
    evaluate_experiment("test_x", prices=PRICES, final=True)
    summary.write_text(json.dumps({"split": "test"}), encoding="utf-8")        # no `complete` key: not known to be unfinished
    with pytest.raises(SplitLocked):
        evaluate_experiment("test_x", prices=PRICES, final=True, overwrite=True)
    summary.write_text("{ not json", encoding="utf-8")                         # damaged: not known to be unfinished
    with pytest.raises(SplitLocked, match="remove the folder"):
        evaluate_experiment("test_x", prices=PRICES, final=True, overwrite=True)
    shutil.rmtree(summary.parent)                                              # declaring it invalid is the way out
    assert evaluate_experiment("test_x", prices=PRICES, final=True)["complete"] is True


def test_a_run_refuses_a_judge_that_is_the_answer_model(project):
    """Step 1 checks this once; after its approval a variant with answer.model == judge.model ran unchallenged."""
    write(project / "configs" / "same.yaml", BASELINE + "answer: {model: j}\n")          # the baseline's judge model is `j`
    cfg = load_config(project / "configs" / "same.yaml")
    with pytest.raises(ValueError, match=r"judge and answer both use j.*grading its own answers"):
        run_experiment(cfg, "dev", experiment="dev_same", system=FakeSystem(), limit=3)   # even a smoke test: it is paid for
    assert run_experiment(cfg, "dev", experiment="dev_same_retrieval", retrieval_only=True, system=FakeSystem(),
                          limit=3)["ran"] == 2                                            # no judge, nothing to grade
    write(project / "configs" / "shout.yaml", BASELINE + "answer: {model: J}\n")        # the judge's `j`, in capitals
    with pytest.raises(ValueError, match="judge and answer both use"):
        run_experiment(load_config(project / "configs" / "shout.yaml"), "dev", experiment="dev_shout",
                       system=FakeSystem(), limit=3)
    ok = load_config(project / "configs" / "baseline.yaml")                              # no answer.model recorded: not judged here
    assert run_experiment(ok, "dev", experiment="dev_ok", system=FakeSystem())["ran"] == 2
    write(project / "configs" / "other.yaml", BASELINE + "answer: {model: m}\n")
    assert run_experiment(load_config(project / "configs" / "other.yaml"), "dev", experiment="dev_other",
                          system=FakeSystem(), limit=3)["ran"] == 2                       # a different judge is fine


def test_compare_prints_no_test_number_without_final(project, capsys):
    """compare read results/<name>/ directly and was the one command that never asked for --final."""
    from labkit.cli import main
    cfg = load_config(project / "configs" / "baseline.yaml")
    for name in ("test_000_baseline", "final_try"):                       # the second is a test run under a dev-like name
        run_experiment(cfg, "test", experiment=name, final=True, system=FakeSystem())
        grade_experiment(name, grade_fn=fake_grade, final=True)
        evaluate_experiment(name, prices=PRICES, final=True)
    with pytest.raises(SplitLocked):
        compare_experiments("test_000_baseline", "final_try")
    with pytest.raises(SplitLocked):
        compare_experiments("final_try", "test_000_baseline", metric="page_hit@5")
    assert main(["compare", "test_000_baseline", "final_try"]) == 1
    captured = capsys.readouterr()
    assert "locked" in captured.err and "n=" not in captured.out        # no score reached the screen
    assert "n=1" in compare_experiments("test_000_baseline", "final_try", final=True)
    assert main(["compare", "test_000_baseline", "final_try", "--final"]) == 0
    cfg = load_config(project / "configs" / "baseline.yaml")              # a dev comparison never needed it
    run_experiment(cfg, "dev", experiment="dev_a", system=FakeSystem())
    graded_and_evaluated("dev_a")
    assert "n=2" in compare_experiments("dev_a", "dev_a")


def test_compare_warns_when_the_inputs_of_a_result_changed_after_it_ran(project):
    """The report and the baseline check re-read the input files live; compare put two results side by side without
    a word although the repository no longer held what produced one of them."""
    write(project / "prompts" / "judge.md", "Grade 0 to 2.\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    for name in ("dev_a", "dev_b"):
        run_experiment(cfg, "dev", experiment=name, system=FakeSystem())
        graded_and_evaluated(name)
    assert "WARNING" not in without_small_n(compare_experiments("dev_a", "dev_b"))                      # nothing changed: silent
    write(project / "prompts" / "judge.md", "Grade 0 to 5, generously.\n")
    text = compare_experiments("dev_a", "dev_b")
    for name in ("dev_a", "dev_b"):
        assert f"WARNING: {name}: inputs changed since the run (prompts/judge.md)" in text
    (project / "prompts" / "judge.md").unlink()                                        # a deleted input is a change too
    assert "prompts/judge.md" in compare_experiments("dev_a", "dev_b")


def test_compare_warns_about_a_card_edited_after_the_result(project):
    from labkit.cli import main
    write(project / "experiments" / "004-hybrid.md", FILLED_CARD)
    write(project / "configs" / "004-hybrid.yaml",
          "base: baseline.yaml\nname: 004_hybrid\ncard: experiments/004-hybrid.md\nretrieval: {k: 9}\n")
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_000_baseline",
                   system=FakeSystem())
    graded_and_evaluated("dev_000_baseline")
    run_experiment(load_config(project / "configs" / "004-hybrid.yaml"), "dev", experiment="dev_004_hybrid",
                   system=FakeSystem())
    graded_and_evaluated("dev_004_hybrid")
    assert "WARNING" not in without_small_n(compare_experiments("dev_000_baseline", "dev_004_hybrid"))
    write(project / "experiments" / "004-hybrid.md", FILLED_CARD.replace("net gain >= 3 questions", "net gain >= 1 question"))
    assert "WARNING: dev_004_hybrid: card sections 1–4 edited since it was first used" in compare_experiments(
        "dev_000_baseline", "dev_004_hybrid")
    assert main(["compare", "dev_000_baseline", "dev_004_hybrid"]) == 0                  # a warning, not a refusal


def test_costs_computed_from_different_price_tables_are_flagged_in_compare(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    prices = project / "configs" / "pricing.yaml"
    write(prices, "models:\n  m: {input: 0.75, output: 3.75}\n  j: {input: 0.75, output: 3.75}\n")
    run_experiment(cfg, "dev", experiment="dev_a", system=FakeSystem())
    grade_experiment("dev_a")
    first = evaluate_experiment("dev_a")                                                # prices read from pricing.yaml
    write(prices, "models:\n  m: {input: 1.5, output: 7.5}\n  j: {input: 1.5, output: 7.5}\n")
    run_experiment(cfg, "dev", experiment="dev_b", system=FakeSystem())
    grade_experiment("dev_b")
    second = evaluate_experiment("dev_b")
    assert first["pricing_hash"] and second["pricing_hash"] and first["pricing_hash"] != second["pricing_hash"]
    text = compare_experiments("dev_a", "dev_b")
    assert "WARNING: dev_a and dev_b were costed from different configs/pricing.yaml versions" in text
    assert "WARNING" not in without_small_n(compare_experiments("dev_a", "dev_a"))


def test_failed_questions_are_flagged_and_what_they_cost_is_kept(project):
    class FailsAfterCalling(FakeSystem):
        def run(self, question, scope):
            if "휴면" in question:
                steps = {}
                record_call(steps, node="generate", model="m", input_tokens=1000, output_tokens=200)
                return {"status": "error", "error": "answer could not be parsed", "steps": steps}
            return super().run(question, scope)

    class AlwaysFails(FakeSystem):
        def run(self, question, scope):
            return {"status": "error", "error": "no network"}

    cfg = load_config(project / "configs" / "baseline.yaml")
    exp = "dev_000_baseline"
    spent = (1000 * 0.75 + 200 * 3.75) / 1e6
    run_experiment(cfg, "dev", experiment=exp, system=FailsAfterCalling())
    s = graded_and_evaluated(exp)
    assert s["n_errors"] == 1 and s["n_questions"] == 1 and s["complete"] is False
    assert s["incomplete"] == ["1 questions failed on every attempt: POS001"]
    assert s["scores"]["correctness_mean"] == 0.5                                # NEG001 only: the failure is not a 0
    assert s["cost"]["wasted_usd"] == pytest.approx(spent) and s["cost"]["usd"] == pytest.approx(spent)
    assert s["cost"]["total_usd"] == pytest.approx(2 * spent)

    run_experiment(cfg, "dev", experiment=exp, system=FakeSystem())              # the retry succeeds
    s = graded_and_evaluated(exp, overwrite=True)
    assert s["complete"] is True and s["n_errors"] == 0
    assert s["cost"]["wasted_usd"] == pytest.approx(spent)                       # the failed attempt was still paid for
    assert s["cost"]["total_usd"] == pytest.approx(s["cost"]["usd"] + spent)

    run_experiment(cfg, "dev", experiment="dev_dead", system=AlwaysFails())
    with pytest.raises(ValueError, match="every question of dev_dead failed"):
        evaluate_experiment("dev_dead", prices=PRICES)


def test_the_split_is_what_was_run_not_what_the_name_says(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "test", experiment="final_try", final=True, system=FakeSystem())
    with pytest.raises(SplitLocked):                                             # the name does not hide the test split
        grade_experiment("final_try", grade_fn=fake_grade)
    with pytest.raises(SplitLocked):
        evaluate_experiment("final_try", prices=PRICES)
    grade_experiment("final_try", grade_fn=fake_grade, final=True)
    s = evaluate_experiment("final_try", prices=PRICES, final=True)
    assert s["split"] == "test" and s["compare_to"] == "test_000_baseline"
    build_report()
    assert "final_try" not in (project / "results" / "SUMMARY.md").read_text(encoding="utf-8")   # kept out of the report

    with pytest.raises(ValueError, match=r"already holds runs on the \['test'\] split"):
        run_experiment(cfg, "dev", experiment="final_try", system=FakeSystem())  # one name, one split

    run_experiment(cfg, "dev", experiment="test_scratch", system=FakeSystem())    # a dev run is not locked by its name
    assert grade_experiment("test_scratch", grade_fn=fake_grade)["graded_now"] == 2


def test_a_judge_that_breaks_its_contract_is_stopped_before_anything_is_saved(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    for bad in ("2", None, 7, -1, True, float("nan"), {}):                       # max_score is 2
        with pytest.raises(ValueError, match="correctness="):
            grade_experiment("dev_x", grade_fn=lambda item, run, c, bad=bad: {"correctness": bad})
    path = project / "out" / "grades" / "dev_x.jsonl"
    assert not path.read_text(encoding="utf-8").strip()                          # nothing was written

    grade_experiment("dev_x", grade_fn=lambda item, run, c: {"correctness": 1.5})   # in range: fine
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["correctness"] = 7                                                   # a grades file edited by hand
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="correctness=7"):
        evaluate_experiment("dev_x", prices=PRICES)


def test_integer_qids_are_one_question_and_resume_like_strings(project):
    items = [{**GOLDEN[0], "qid": 0}, {**GOLDEN[1], "qid": 1}]
    write(project / "golden" / "provided.jsonl", "".join(json.dumps(g, ensure_ascii=False) + "\n" for g in items))
    assert [i["qid"] for i in load_golden()] == ["0", "1"]                       # 0 is a valid qid, read as text
    cfg = load_config(project / "configs" / "baseline.yaml")
    assert run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())["ran"] == 2
    again = run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem())
    assert again["ran"] == 0 and again["skipped_done"] == 2                      # the log keeps text; the golden must match
    assert graded_and_evaluated("dev_x")["complete"] is True

    write(project / "golden" / "provided.jsonl", json.dumps({**GOLDEN[0], "qid": 1}) + "\n"
          + json.dumps({**GOLDEN[1], "qid": "1"}) + "\n")
    with pytest.raises(ValueError, match="duplicate qid '1'"):
        load_golden()
    for bad in ("true", "1.5", "[1]", '""'):
        write(project / "golden" / "provided.jsonl", f'{{"qid": {bad}, "split": "dev"}}\n')
        with pytest.raises(ValueError, match="needs a `qid`"):
            load_golden()


def test_a_padded_qid_is_the_same_question_and_blank_or_case_only_differences_are_refused(project):
    """`"d1 "`, `"D1"` and `"d1"` once ran as three questions (n 8), and `show --qid d1` found only one of them."""
    def golden(*qids):
        write(project / "golden" / "provided.jsonl", "".join(
            json.dumps({**GOLDEN[0], "qid": q}, ensure_ascii=False) + "\n" for q in qids))
    golden(" POS1 ", "POS2\t")
    assert [i["qid"] for i in load_golden()] == ["POS1", "POS2"]                 # read trimmed everywhere
    golden("d1", "d1 ")
    with pytest.raises(ValueError, match=r"duplicate qid 'd1' in provided.jsonl and provided.jsonl"):
        load_golden()                                                            # equal once trimmed
    golden("d1", "D1")
    with pytest.raises(ValueError, match=r"qid 'D1' differs from 'd1' only by case"):
        load_golden()
    golden("   ")
    with pytest.raises(ValueError, match="needs a `qid`"):
        load_golden()
    golden(" 7 ")
    assert [i["qid"] for i in load_golden()] == ["7"]
    write(project / "golden" / "provided.jsonl", json.dumps({**GOLDEN[0], "qid": 7}) + "\n"
          + json.dumps({**GOLDEN[1], "qid": " 7"}) + "\n")
    with pytest.raises(ValueError, match="duplicate qid '7'"):                   # the integer 7 and the string " 7"
        load_golden()


def test_qids_that_only_look_alike_are_one_question_or_refused(project):
    """NFC and NFD forms of the same Korean text, or a zero-width space, were accepted as different questions."""
    import unicodedata
    nfc = "가나다"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc

    def golden(*qids):
        write(project / "golden" / "provided.jsonl", "".join(
            json.dumps({**GOLDEN[0], "qid": q}, ensure_ascii=False) + "\n" for q in qids))
    golden(nfd)
    assert [i["qid"] for i in load_golden()] == [nfc]                           # read in one canonical form
    golden(nfd, nfc)
    with pytest.raises(ValueError, match="duplicate qid"):
        load_golden()
    for odd in ("a​b", "a‍b", "a⁠b", "a\x07b", "a‮b",        # invisible or control characters
                "a b", "a b", "a b"):                              # and a space that is not a plain space
        golden(odd)
        with pytest.raises(ValueError, match=r"qid .* has an invisible character \(U\+[0-9A-F]{4}\)"):
            load_golden()


def test_golden_check_still_reports_a_case_clash_instead_of_loading_it(project):
    from labkit.golden_check import run_check
    write(project / "golden" / "provided.jsonl", "".join(
        json.dumps({**GOLDEN[0], "qid": q}) + "\n" for q in ("d1", "D1")))
    write(project / "corpus" / "pages.jsonl", '{"file": "a.pdf", "page": 3, "text": "x"}\n')
    with pytest.raises(ValueError, match="differs from 'd1' only by case"):
        run_check("corpus/pages.jsonl", root=project)


def test_show_finds_a_question_whatever_spaces_the_user_typed(project):
    from labkit.show import show
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem(), root=project)
    assert "== POS001 " in show("dev_x", qids=[" POS001"], root=project)


def test_limit_must_be_a_positive_number_even_for_a_variant_config(project):
    write(project / "configs" / "010-variant.yaml", "base: baseline.yaml\nname: 010\nretrieval: {k: 9}\n")
    variant = load_config(project / "configs" / "010-variant.yaml")
    assert variant["changed"]                                                    # no card: only a smoke test may run
    for limit in (0, -1):
        with pytest.raises(ValueError, match="--limit must be at least 1"):      # -1 used to count as "a smoke test"
            run_experiment(variant, "dev", experiment="dev_010", limit=limit, system=FakeSystem())
    assert run_experiment(variant, "dev", experiment="dev_010", limit=1, system=FakeSystem())["ran"] == 1


def test_quantiles_interpolate():
    assert _quantile([1, 2, 3, 4], 0.5) == 2.5                                   # the median, not the upper middle value
    assert _quantile(list(range(1, 11)), 0.9) == pytest.approx(9.1)
    assert _quantile([5], 0.9) == 5 and _quantile([], 0.5) is None and _quantile([None, 2, 4], 0.5) == 3


def test_compare_warns_about_an_incomplete_experiment(project):
    five_dev_questions(project)
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_full", system=FakeSystem())
    graded_and_evaluated("dev_full")
    run_experiment(cfg, "dev", experiment="dev_part", limit=4, system=FakeSystem())
    graded_and_evaluated("dev_part")
    text = compare_experiments("dev_full", "dev_part")
    assert "WARNING: dev_part is incomplete (1 of 5 expected questions never ran)" in text
    assert "WARNING: dev_full" not in text


def test_every_key_labkit_adds_to_a_stored_run_config_is_reserved(project):
    """A key labkit writes that a user config may also use is how `smoke: true` hijacked a run. Anything added to
    run_config must be listed in config.RESERVED_KEYS, which load_config refuses in a user's files."""
    from labkit.config import RESERVED_KEYS
    cfg = load_config(project / "configs" / "baseline.yaml")
    run_experiment(cfg, "dev", experiment="dev_x", system=FakeSystem(), limit=1)      # a smoke run stores every key
    stored = RunLog(project / "out" / "runs.db").load("dev_x")[0]["config"]
    assert set(stored) - set(cfg) <= RESERVED_KEYS
