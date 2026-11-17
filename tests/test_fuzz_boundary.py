"""A quick type-fuzz of what a system returns: every field labkit reads gets values of the wrong type, and run, grade,
evaluate and show must turn them into failed questions or plain values, never a TypeError, AttributeError, KeyError
or OSError later (labkit.contract). The full fuzz of the seventh review round ran 406 such combinations of system results through the CLI and found 73
tracebacks at v0.1.5; this is the part that runs in the suite, through the API.
"""

import copy

from labkit.config import load_config
from labkit.harness import evaluate_experiment, grade_experiment, run_experiment
from labkit.show import show
from test_harness import PRICES, FakeSystem, fake_judge, project  # noqa: F401

GENERIC = [None, "x", 7, [], {}]
SHORT = [None, "x", 7]       # nested fields and counts: the suite has 20 s for this file; [] and {} are covered above
TOP = ["context", "citations", "steps", "searches"]
NESTED = ([("context", 0, k) for k in ("chunk_id", "source", "page", "content", "pages")]
          + [("citations", 0, k) for k in ("chunk_id", "source", "page")]
          + [("searches", 0, "chunk_ids")])
NUMERIC = [("latency_s",), ("input_tokens",), ("output_tokens",), ("n_llm_calls",), ("n_searches",),
           ("steps", "calls", 0, "input_tokens")]
LEAKS = (TypeError, AttributeError, KeyError, OSError)


def put(out, path, value):
    out.setdefault("searches", [{"query": "q", "chunk_ids": ["c1"]}])
    cur = out
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value
    return out


class Fuzzed(FakeSystem):
    def __init__(self, path, value):
        super().__init__()
        self.path, self.value = path, value

    def run(self, question, scope):
        out = copy.deepcopy(super().run(question, scope))  # the context units are test_harness's shared PAGE and OTHER
        return put(out, self.path, self.value) if "휴면" in question else out


def combinations():
    numbers = list(SHORT)
    try:
        import numpy
        numbers.append(numpy.int64(3))
    except ImportError:
        pass
    for field in TOP:
        for value in GENERIC:
            yield (field,), value
    for path in NESTED:
        for value in SHORT:
            yield path, value
    for path in NUMERIC:
        for value in numbers:
            yield path, value


def test_no_result_shape_leaks_a_traceback_from_run_grade_evaluate_or_show(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    leaked = []
    for i, (path, value) in enumerate(combinations()):
        experiment = f"dev_f{i}"
        stage = "run"
        try:
            run_experiment(cfg, "dev", experiment=experiment, system=Fuzzed(path, value))
            stage = "grade"
            grade_experiment(experiment)
            stage = "evaluate"
            evaluate_experiment(experiment, prices=PRICES)
            stage = "show"
            show(experiment, all_rows=True)
        except LEAKS as e:
            leaked.append(f"{'.'.join(map(str, path))}={value!r}: {stage} raised {type(e).__name__}: {e}")
    assert i >= 60                                                       # the table above was walked
    assert not leaked, "\n".join(leaked)
