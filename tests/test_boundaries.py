"""Boundary and detail tests for code the rest of the suite only exercised on one happy path.

A mutation run (181 one-line changes to labkit, each checked against the suite) found that 59 of them survived. These
tests pin the ones that touched something labkit promises: each fails on its own mutant and passes on the real code.
The metrics are tested with one happy-path example each elsewhere; here are their edges.
"""

import subprocess
import sys
import unicodedata

import pytest

from conftest import without_small_n

from labkit import cards, scaffold
from labkit.compare import compare_experiments
from labkit.config import changed_keys, config_hash
from labkit.env import setup
from labkit.eval import load_jsonl, metrics as M, paired
from labkit.eval.headroom import headroom
from labkit.eval.runlog import RunLog
from labkit.golden import scope_of
from labkit.harness import SplitLocked, _expected_qids, _quantile, experiment_split, guard_test
from labkit.index import index_key
from labkit.measures import citation, retrieval
from labkit.project import load_project
from labkit.repro import git_info
from labkit.show import _cut, show

ITEM = {"targets": [{"file": "a.pdf", "page": 1}], "evidence": []}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def unit(i, gold=True, content="x"):
    return {"chunk_id": f"c{i}", "source": "a.pdf", "page": 1 if gold else 9, "content": content}


# ---------------------------------------------------------------- eval metrics

def test_evidence_lines_are_kept_from_eight_characters():
    assert M.evidence_segments(["12345678\n1234567"]) == ["12345678"]          # 8 characters count, 7 do not


def test_page_hit_at_k_counts_the_kth_unit():
    r = M.ranked_metrics(ITEM, [unit(i, gold=(i == 5)) for i in range(1, 6)])   # gold only at rank 5
    assert (r["page_hit@3"], r["page_hit@5"]) == (0, 1)
    assert M.ranked_metrics(ITEM, [unit(1)])["page_hit@1"] == 1


def test_gold_at_k_counts_exactly_k_units():
    r = M.ranked_metrics(ITEM, [unit(i) for i in range(6)])
    assert (r["gold@1"], r["gold@3"], r["gold@5"]) == (1, 3, 5)


def test_a_citation_is_in_context_only_if_every_citation_is():
    cites = [{"chunk_id": "c1", "source": "a.pdf", "page": 1}, {"chunk_id": "zz", "source": "a.pdf", "page": 1}]
    assert M.citation_check(ITEM, {"context": [unit(1)], "citations": cites})["in_context"] == 0


def test_evid_full_needs_every_evidence_segment():
    m = M.context_metrics({**ITEM, "evidence": ["alpha-bravo-1", "charlie-delta-2"]}, [unit(1, content="alpha-bravo-1")])
    assert m["evid"] == 0.5 and m["evid_full"] == 0


def test_squash_folds_full_width_characters():
    assert M.squash("ＡＢＣ １２３") == "ABC123"                                  # NFKC, not NFC


def test_direction_agreement_does_not_call_a_tie_the_same_direction():
    assert M.direction_agreement({"q": 1}, {"q": 1}, {"q": 0}, {"q": 1}) == {
        "score_changed": 1, "same_direction": 0, "opposite_direction": 0, "metric_unchanged": 1}


def test_target_files_match_across_unicode_forms():
    nfd, nfc = (unicodedata.normalize(f, "한글.pdf") for f in ("NFD", "NFC"))
    assert M.is_gold({"chunk_id": "c", "source": nfc, "page": 1, "content": "x"},
                     M.target_pages({"targets": [{"file": nfd, "page": 1}]}))


def test_headroom_does_not_call_a_worsened_question_a_risk():
    r = headroom({"q": 0}, {"q": 1}, {"q": 0}, max_score=2)
    assert r["worsened"] == 1 and r["risk"] == []


def test_a_difference_equal_to_the_noise_is_within_noise():
    r = paired.compare({"a": 0, "b": 0, "c": 1}, {"a": 1, "b": 1, "c": 1})      # net +2 questions
    assert "within noise" in paired.report(r, noise=2)


def test_questions_only_in_the_first_run_are_counted_as_only_in_a():
    r = paired.compare({"q1": 1, "q2": 1}, {"q1": 1})
    assert (r["only_in_a"], r["only_in_b"]) == (1, 0)


def test_load_jsonl_where_compares_as_strings(tmp_path):
    p = write(tmp_path / "x.jsonl", '{"qid": "a", "k": 1}\n{"qid": "b", "k": 2}\n')
    assert [r["qid"] for r in load_jsonl(p, {"k": "1"})] == ["a"]


# ---------------------------------------------------------------- config

def test_changed_keys_sees_a_key_the_variant_dropped():
    """A config with no `base:` that omits a baseline key must still count as a change, or it skips the card gate."""
    assert changed_keys({"a": 1}, {"a": 1, "b": 2}) == {"b": {"from": 2, "to": None}}
    assert changed_keys({"f": None}, {"f": {}}) == {"f": {"from": {}, "to": None}}


def test_config_hash_does_not_depend_on_key_order():
    assert config_hash({"a": 1, "b": {"x": 1, "y": 2}}) == config_hash({"b": {"y": 2, "x": 1}, "a": 1})


def test_scope_comes_from_set_scope_before_the_items_own_scope():
    assert scope_of({"set": "s", "scope": "own"}, {"corpus": {"set_scope": {"s": "mapped"}}}) == "mapped"


def test_main_branch_defaults_to_main(tmp_path):
    assert load_project(tmp_path)["main_branch"] == "main"


def test_index_key_matches_whole_dotted_names_only():
    p = {"index_keys": ["chunk"]}
    assert index_key({"chunk": 1, "chunking_extra": 1}, p) == index_key({"chunk": 1, "chunking_extra": 2}, p)


# ---------------------------------------------------------------- harness helpers

def test_a_library_caller_cannot_slip_a_test_split_past_the_guard():
    with pytest.raises(SplitLocked):
        guard_test("test_extra", final=False)


def test_two_recorded_splits_in_one_experiment_are_refused(tmp_path):
    log = RunLog(tmp_path / "r.db")
    for s in ("dev", "test"):
        log.save({"status": "answered", "config": {"selection": {"split": s}}}, experiment="x", qid=s)
    with pytest.raises(ValueError, match="more than one split"):
        experiment_split("x", log.load("x"))


def test_quantile_sorts_its_input():
    assert _quantile([3, 1, 2], 0.5) == 2


def test_a_run_without_sets_widens_the_expected_questions_to_the_whole_split():
    rows = [{"config": {"selection": {"split": "dev", "sets": ["a"]}}},
            {"config": {"selection": {"split": "dev", "sets": None}}}]
    items = {"1": {"split": "dev", "set": "a"}, "2": {"split": "dev", "set": "b"}}
    assert _expected_qids(rows, "dev", items) == {"1", "2"}


# ---------------------------------------------------------------- measures, show, compare

def test_pool_columns_exist_for_an_empty_pool():
    row = retrieval({**ITEM, "qid": "Q"}, {"status": "answered", "context": [unit(1)], "steps": {"pool": []}}, {})
    assert row["pool_page_hit"] == 0


def test_citation_measure_skips_retrieval_only_runs():
    assert citation(ITEM, {"status": "retrieval_only"}, {}) == {}


def two_results(root):
    for name, v in (("a", 1.0), ("b", 0.0)):
        write(root / f"results/{name}/metrics.jsonl",
              "".join(f'{{"qid": "{q}", "mrr": {v}}}\n' for q in ("q1", "q2")))
        write(root / f"results/{name}/summary.json", "{}")


def test_compare_metric_default_scale_is_one(tmp_path):
    two_results(tmp_path)
    assert "a 1.000  b 0.000" in compare_experiments("a", "b", metric="mrr", root=tmp_path)


def test_compare_warns_only_about_an_experiment_that_says_it_is_incomplete(tmp_path):
    two_results(tmp_path)
    assert "WARNING" not in without_small_n(compare_experiments("a", "b", metric="mrr", root=tmp_path))    # no `complete` key: no warning
    write(tmp_path / "results/b/summary.json", '{"complete": false, "incomplete": ["x"]}')
    assert "WARNING: b is incomplete" in compare_experiments("a", "b", metric="mrr", root=tmp_path)


def test_show_finds_a_question_by_an_integer_qid(tmp_path):
    RunLog(tmp_path / "out/runs.db").save({"status": "answered", "question": "q", "config": {"selection": {"split": "dev"}}},
                                          experiment="dev_x", qid="12")
    assert "== 12 " in show("dev_x", qids=[12], root=tmp_path)


def test_cut_keeps_a_text_of_exactly_the_limit():
    assert _cut("a" * 300, 300) == "a" * 300


# ---------------------------------------------------------------- environment

def test_a_missing_dotenv_is_an_error_not_a_silent_skip(tmp_path, monkeypatch):
    write(tmp_path / ".env", "A=1\n")
    monkeypatch.setitem(sys.modules, "dotenv", None)
    with pytest.raises(RuntimeError, match="python-dotenv"):
        setup(tmp_path)


def test_outside_git_nothing_is_dirty_and_nothing_is_fingerprinted(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert git_info(tmp_path) == {"commit": None, "branch": None, "dirty": None, "changes": None}


# ---------------------------------------------------------------- cards and scaffold

CARD = "".join(f"## {n}. {t}\n\n- text {n}\n\n" for n, t in ((1, "Hypothesis"), (2, "Measurement"),
                                                             (3, "Go/no-go criteria"), (4, "LLM budget"))) + "## 5. Results\n\nnone\n"


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_every_card_section_from_1_to_4_is_hashed_and_5_is_not(tmp_path, n):
    c = tmp_path / "c.md"
    c.write_text(CARD, encoding="utf-8")
    h = cards.sections_hash(c)
    c.write_text(CARD.replace("none", "later"), encoding="utf-8")
    assert cards.sections_hash(c) == h                                          # results may be filled in afterwards
    c.write_text(CARD.replace(f"- text {n}", f"- other {n}"), encoding="utf-8")
    assert cards.sections_hash(c) != h


def test_a_numbered_b_card_is_not_reused(tmp_path):
    scaffold.init_project(tmp_path / "p")
    write(tmp_path / "p/experiments/007b-retry.md", "x")
    assert scaffold.next_number(tmp_path / "p") == 8


@pytest.mark.needs_git
def test_the_card_branch_user_is_lower_case(tmp_path):
    scaffold.init_project(tmp_path / "p", git=True)
    subprocess.run(["git", "config", "user.name", "Ana Kim"], cwd=tmp_path / "p", check=True)
    assert scaffold.new_card(tmp_path / "p", "x")["branch"] == "ana-kim/001-x"


@pytest.mark.needs_git
def test_init_lists_no_git_internals(tmp_path):
    assert not [f for f in scaffold.init_project(tmp_path / "p", git=True) if f.startswith(".git/")]
