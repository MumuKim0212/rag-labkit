"""The guards that make the workflow's rules more than documentation: the card gate, the index key,
`ready()`, the pool measure, and main-only step records."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from labkit import cards, steps
from labkit.config import load_config
from labkit.cost import record_call
from labkit.harness import evaluate_experiment, run_experiment
from labkit.index import index_key
from labkit.measures import retrieval
from labkit.project import load_project

TEMPLATE = Path(__file__).resolve().parents[1] / "labkit" / "template"
GOLD = {"chunk_id": "c1", "source": "a.pdf", "page": 3, "content": "휴면성 증권계좌는 6개월 이상 매매가 없는 계좌"}
OTHER = {"chunk_id": "c2", "source": "b.pdf", "page": 1, "content": "관련 없는 내용"}
ITEM = {"qid": "Q1", "set": "s", "split": "dev", "question": "휴면 계좌는?", "answerable": True,
        "targets": [{"file": "a.pdf", "page": 3}], "evidence": []}
BASELINE = """
name: 000_baseline
system: fake
run: {concurrency: 1}
chunking: {size: 800}
retrieval: {k: 5}
embedding: {model: e1}
"""
FILLED_CARD = """# 004: hybrid

Status: planned

## 1. Hypothesis

- Change (one variable): hybrid search instead of vector
- Failure it targets: retrieval misses, e.g. Q1
- Expected effect: gold@5 up by about 2 questions

## 2. Measurement

- Compared against (reused, not rerun): dev_000_baseline
- Proxy metric (step 4): gold@5, tracked answers in step 4
- Split: dev
- Noise (step 3): (a) 3 of 90, (b) 2 of 90

## 3. Go/no-go criteria (written before results)

- LLM-free gate: net gain >= 3 questions on dev, larger than noise
- Adopt if: paired net wins >= 5 and the 95% CI excludes 0

## 4. LLM budget

- Planned calls if the gates pass: 90 answers + 90 judge
- Calls so far in the project: 410

## 5. LLM-free results

_to be filled_

- Gates passed? yes
"""


class Fake:
    def __init__(self, steps_extra=None, ready=None):
        self.steps_extra = steps_extra or {}
        if ready is not None:                      # most systems do not define ready() at all
            self.ready = lambda: ready

    def retrieve(self, question, scope, steps=None):
        return [GOLD]

    def run(self, question, scope):
        steps = dict(self.steps_extra)
        record_call(steps, node="generate", model="m", input_tokens=10, output_tokens=5)
        return {"status": "answered", "answer": "a", "context": [GOLD], "steps": steps, "n_searches": 1,
                "citations": []}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    write(tmp_path / "labkit.yaml", "golden: {files: [g.jsonl]}\nindex_keys: [chunking, embedding]\n")
    write(tmp_path / "configs" / "baseline.yaml", BASELINE)
    write(tmp_path / "configs" / "pricing.yaml", "models:\n  m: {input: 1.0, output: 1.0}\n")
    write(tmp_path / "golden" / "g.jsonl", json.dumps(ITEM, ensure_ascii=False) + "\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def variant(proj, name="004-hybrid", body="retrieval: {k: 8}\n", card="experiments/004-hybrid.md"):
    return load_config(write(proj / "configs" / f"{name}.yaml",
                             f"base: baseline.yaml\nname: {name.replace('-', '_')}\n"
                             + (f"card: {card}\n" if card else "") + body))


# ---------------------------------------------------------------- card gate

def test_template_card_has_placeholders_in_every_section_and_a_filled_one_has_none():
    left = cards.problems(TEMPLATE / "experiments" / "_TEMPLATE.md")
    assert [p.split(" (")[0] for p in left] == ["section 1", "section 2", "section 3", "section 4"]
    assert all("placeholders" in p for p in left)


def test_placeholder_detection_ignores_identifiers(tmp_path):
    def probs(bullet):
        return cards.problems(write(tmp_path / "c.md", "".join(
            f"## {n}. x\n\n- {bullet}\n\n" for n in range(1, 5))))
    assert probs("compared with dev_004_hybrid_retrieval and `__init__.py` and results/dev_000_baseline") == []
    assert probs("gain of _N_ questions")                 # an italic placeholder
    assert probs("at least __ questions")                 # a blank
    assert probs("_the whole line is a placeholder_")
    empty = cards.problems(write(tmp_path / "e.md", "## 1. a\n\n## 2. b\n\n- x\n\n## 3. c\n\n- y\n\n## 4. d\n\n- z\n"))
    assert empty == ["section 1 (Hypothesis) has no content"]


def test_gate_exempts_the_baseline_and_smoke_tests_but_not_variants(proj):
    base = load_config(proj / "configs" / "baseline.yaml")
    assert cards.gate(base, proj) == {}                                    # baseline and noise reruns: changed is empty
    nocard = variant(proj, card=None)
    with pytest.raises(ValueError, match="names no `card:`"):
        cards.gate(nocard, proj)
    assert cards.gate(nocard, proj, limit=cards.SMOKE_LIMIT) == {}         # a smoke test needs no card
    with pytest.raises(ValueError):
        cards.gate(nocard, proj, limit=cards.SMOKE_LIMIT + 1)
    with_card = variant(proj)
    with pytest.raises(FileNotFoundError, match="004-hybrid.md does not exist"):
        cards.gate(with_card, proj)
    write(proj / "experiments" / "004-hybrid.md", (TEMPLATE / "experiments" / "_TEMPLATE.md").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="write sections 1–4 before running"):
        cards.gate(with_card, proj)
    write(proj / "experiments" / "004-hybrid.md", FILLED_CARD)
    record = cards.gate(with_card, proj)
    assert record["file"] == "experiments/004-hybrid.md" and len(record["sections_hash"]) == 10


def test_run_is_refused_without_a_card_and_records_the_card_hash_with_one(proj):
    cfg = variant(proj)
    with pytest.raises(FileNotFoundError):
        run_experiment(cfg, "dev", experiment="dev_004_hybrid", system=Fake())
    write(proj / "experiments" / "004-hybrid.md", FILLED_CARD)
    assert run_experiment(cfg, "dev", experiment="dev_004_hybrid", system=Fake())["ran"] == 1
    summary = evaluate_experiment("dev_004_hybrid", prices={"m": {"input": 1, "output": 1}})
    check = summary["card_check"]
    assert check["file"] == "experiments/004-hybrid.md" and check["edited_after_run"] is False


def test_editing_sections_1_to_4_after_the_run_is_detected_but_later_sections_are_free(proj):
    cfg, card = variant(proj), proj / "experiments" / "004-hybrid.md"
    write(card, FILLED_CARD)
    run_experiment(cfg, "dev", experiment="dev_004_hybrid", system=Fake())
    write(card, FILLED_CARD.replace("_to be filled_", "gold@5 improved from 0.80 to 0.90"))      # section 5
    prices = {"m": {"input": 1, "output": 1}}
    assert evaluate_experiment("dev_004_hybrid", prices=prices)["card_check"]["edited_after_run"] is False
    write(card, FILLED_CARD.replace("net gain >= 3 questions", "net gain >= 1 question"))          # section 3
    s = evaluate_experiment("dev_004_hybrid", prices=prices, overwrite=True)
    assert s["card_check"]["edited_after_run"] is True and s["card_check"]["hash_now"] != s["card_check"]["hash_at_first_run"]


def test_a_smoke_test_logged_first_does_not_switch_the_card_check_off(proj):
    """The documented flow: smoke test (exempt from the gate) first, then the full run, same experiment name.
    The smoke row has no card record and belongs to no result, so the card check comes from the real run."""
    write(proj / "golden" / "g.jsonl", "".join(json.dumps({**ITEM, "qid": f"Q{i}"}, ensure_ascii=False) + "\n"
                                               for i in (1, 2, 3)))
    cfg, card = variant(proj), proj / "experiments" / "004-hybrid.md"
    write(card, FILLED_CARD)
    assert run_experiment(cfg, "dev", experiment="dev_004_hybrid", system=Fake(), limit=1)["ran"] == 1
    assert run_experiment(cfg, "dev", experiment="dev_004_hybrid", system=Fake())["ran"] == 3   # the smoke question again
    write(card, FILLED_CARD.replace("net gain >= 3 questions", "net gain >= 1 question"))
    check = evaluate_experiment("dev_004_hybrid", prices={"m": {"input": 1, "output": 1}})["card_check"]
    assert check is not None and check["edited_after_run"] is True


def test_baseline_run_needs_no_card(proj):
    base = load_config(proj / "configs" / "baseline.yaml")
    run_experiment(base, "dev", experiment="dev_000_baseline", system=Fake())
    s = evaluate_experiment("dev_000_baseline", prices={"m": {"input": 1, "output": 1}})
    assert s["card_check"] is None


# ---------------------------------------------------------------- index key, ready()

def test_index_key_follows_only_the_declared_settings(proj):
    project = load_project(proj)
    base = load_config(proj / "configs" / "baseline.yaml")
    assert index_key(variant(proj, "a", "retrieval: {k: 8}\n"), project) == index_key(base, project)   # same index
    assert index_key(variant(proj, "b", "chunking: {size: 400}\n"), project) != index_key(base, project)
    assert index_key(variant(proj, "c", "embedding: {model: e2}\n"), project) != index_key(base, project)
    assert index_key(base, {"index_keys": []}) is None


def test_a_variant_that_reused_the_wrong_index_fails_loudly(proj):
    base = load_config(proj / "configs" / "baseline.yaml")
    key = index_key(base, load_project(proj))
    run_experiment(base, "dev", experiment="dev_ok", system=Fake({"index_key": key}))
    run_experiment(base, "dev", experiment="dev_stale", system=Fake({"index_key": "deadbeef00"}))
    prices = {"m": {"input": 1, "output": 1}}
    assert evaluate_experiment("dev_ok", prices=prices)["index_key"] == key
    from labkit.eval.runlog import RunLog
    (row,) = RunLog(proj / "out" / "runs.db").load("dev_stale")
    assert row["status"] == "error" and "index mismatch" in row["error"] and key in row["error"]
    assert row["config"]["index_key"] == key
    assert [c["model"] for c in row["steps"]["calls"]] == ["m"]        # the call was made and paid for: its cost is kept


def test_ready_stops_the_batch_before_any_question_runs(proj):
    base = load_config(proj / "configs" / "baseline.yaml")
    with pytest.raises(RuntimeError, match="not ready: index for chunking.size=800 is missing"):
        run_experiment(base, "dev", experiment="dev_x", system=Fake(ready=(False, "index for chunking.size=800 is missing")))
    from labkit.eval.runlog import RunLog
    assert RunLog(proj / "out" / "runs.db").experiments() == []             # nothing ran, nothing was logged
    assert run_experiment(base, "dev", experiment="dev_x", system=Fake(ready=(True, "")))["ran"] == 1
    with pytest.raises(ValueError, match=r"ready\(\) must return \(ok: bool, detail: str\)"):
        run_experiment(base, "dev", experiment="dev_y", system=Fake(ready=True))


def test_systems_without_ready_still_run(proj):
    class Plain:
        def retrieve(self, question, scope, steps=None):
            return [GOLD]

        def run(self, question, scope):
            return {"status": "answered", "context": [GOLD], "steps": {}, "n_searches": 1}
    base = load_config(proj / "configs" / "baseline.yaml")
    assert run_experiment(base, "dev", experiment="dev_plain", system=Plain())["ran"] == 1


# ---------------------------------------------------------------- pool measure

def test_pool_metrics_show_what_a_reranker_could_still_fix():
    project = load_project(Path("."))
    pool_has_gold = {"context": [OTHER], "steps": {"pool": [OTHER, GOLD]}}
    row = retrieval(ITEM, pool_has_gold, project)
    assert row["page_hit"] == 0 and row["pool_page_hit"] == 1 and row["pool_units"] == 2
    not_in_pool = {"context": [OTHER], "steps": {"pool": [OTHER]}}
    assert retrieval(ITEM, not_in_pool, project)["pool_page_hit"] == 0
    no_pool = retrieval(ITEM, {"context": [GOLD], "steps": {}}, project)
    assert "pool_page_hit" not in no_pool and "first_page_hit" not in no_pool
    both = retrieval(ITEM, {"context": [GOLD], "steps": {"pool": [GOLD], "first_context": [OTHER]}}, project)
    assert both["first_page_hit"] == 0 and both["pool_page_hit"] == 1


# ---------------------------------------------------------------- main-only step records

@pytest.mark.needs_git
def test_step_records_are_written_only_on_main(proj):
    root = proj / "proj"
    shutil.copytree(TEMPLATE, root)

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                       capture_output=True)
    git("init", "-b", "main")
    git("add", ".")
    git("commit", "-m", "init")
    first = [i.index for i in steps.criteria(root, 1) if not i.auto][0]
    steps.tick(root, 1, first, "evidence", "ana")                          # on main: fine
    git("checkout", "-b", "ana/004-hybrid")
    with pytest.raises(ValueError, match=r"run this on the 'main' branch \(you are on 'ana/004-hybrid'\)"):
        steps.tick(root, 1, first, "evidence 2", "ana")
    with pytest.raises(ValueError, match="'main' branch"):
        steps.approve(root, 1, "ana")
    with pytest.raises(ValueError, match="'main' branch"):
        steps.revoke(root, 1, "x", "ana")
    (root / "labkit.yaml").write_text("main_branch: ana/004-hybrid\n", encoding="utf-8")   # the name is configurable
    steps.tick(root, 1, first, "evidence 3", "ana")
    git("checkout", "--detach")
    with pytest.raises(ValueError, match="detached HEAD"):
        steps.tick(root, 1, first, "evidence 4", "ana")


@pytest.mark.needs_git
def test_a_tag_named_like_the_main_branch_does_not_change_which_branch_is_main(proj):
    """With a tag `main`, `symbolic-ref --short HEAD` prints `heads/main`, and the real main branch was refused."""
    root = proj / "proj"
    shutil.copytree(TEMPLATE, root)

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                       capture_output=True)
    git("init", "-b", "main")
    git("add", ".")
    git("commit", "-m", "init")
    git("tag", "main")
    first = [i.index for i in steps.criteria(root, 1) if not i.auto][0]
    steps.tick(root, 1, first, "evidence", "ana")                          # the real main branch: allowed
    git("checkout", "-b", "feature")
    git("tag", "feature")                                                  # the same on a branch that must be refused
    with pytest.raises(ValueError, match=r"run this on the 'main' branch \(you are on 'feature'\)"):
        steps.tick(root, 1, first, "evidence 2", "ana")
    git("checkout", "-b", "heads/main")                                    # a branch whose name looks like the long form
    with pytest.raises(ValueError, match=r"you are on 'heads/main'"):
        steps.tick(root, 1, first, "evidence 3", "ana")
