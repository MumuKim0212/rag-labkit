"""The interface every system implements, and the registry the harness uses (config key `system`).

A system answers one question within one index scope and returns a result dict whose keys are
labkit.eval.runlog COLUMNS. The harness adds system, config, question, latency_s, input_tokens, output_tokens and
n_llm_calls: labkit computes the last four itself (the clock and the sum of `steps["calls"]`), and a value the
system gives for them is ignored, so a row's tokens and the summary's totals come from one source.

    {
      "status": "answered | abstained | ...",   # your own vocabulary; "error" and "retrieval_only" are reserved
      "answer": "...",                           # final text including the source list
      "searches": [{"query": "...", "chunk_ids": [...], "seconds": 0.4}],
      "context": [unit, ...],          # exactly what the answer step received (labkit.eval unit)
      "citations": [{"chunk_id", "source", "page"}],
      "steps": {"calls": [...], ...},  # labkit.cost.record_call; agents add decisions, "first_context", etc.
      "n_searches": 1,                 # a whole number of at least 0; anything else is replaced by len(searches)
    }

`status` is required: a result that is not a dict, or has no non-empty text `status`, is a failed question (its `steps`
are kept like those of any error), never a finished one.

A question that cannot be answered: raise, or return {"status": "error", "error": "...", "steps": steps}. The batch
goes on and a rerun retries it. Raising loses the model calls already made; returning keeps them in `steps`, so
their cost shows up as `wasted_usd`. A failed question is not scored as 0: the summary says `complete: false`.
`steps["calls"]` must be a list of mappings that each name a `model`, with token counts (if given) that are finite
numbers of at least 0; a result with calls that cannot be costed is a failed question too, and its `steps` are dropped.

The shapes labkit reads later are checked before the result is saved (labkit.contract): `context` is a list of units,
each a mapping with `chunk_id` (text or a whole number), `source` (text), `page` (a whole number), `content` (text)
and optionally `pages` (whole numbers); each citation has `chunk_id`, `source` and `page`; `searches` is a list of
mappings whose `chunk_ids` is a list of ids; `steps` is a mapping. null means not given (`context: null` is no
context). A result of another shape is a failed question that says which field is wrong, and its `steps` are kept
when their calls can be costed. Numbers of other types (numpy's int64 and float64) are stored as int and float, and
tuples as lists.

retrieve() is the search alone, used for LLM-free comparisons (`--retrieval-only`); it records its
query-embedding call into `steps` when given. A system whose retrieval calls a model (a reranker, a query
rewriter) is not LLM-free in that mode: record those calls too, and the run's cost shows it.

Optional records in `steps` that unlock measures (labkit/measures.py):
    "first_context": units of the first search, for systems that search more than once
    "pool": candidates before reranking or cutting to k, so a reranker's headroom can be measured
    "index_key": the index the system actually used (labkit/index.py); a mismatch with the config fails the question

Optional method `ready() -> (bool, str)`: called before a batch; return False with a reason (e.g. the index
is missing) to stop the batch before any question runs.

Register a system in a module listed under `modules:` in labkit.yaml:

    @register_system("rag")
    def make_rag(cfg): return BaselineRAG(cfg)
"""

from typing import Callable, Protocol

from .registry import Registry


class System(Protocol):
    def retrieve(self, question: str, scope: str, steps: dict | None = None) -> list[dict]: ...

    def run(self, question: str, scope: str) -> dict: ...


SYSTEMS: Registry = Registry("system")
register_system = SYSTEMS.register


def build_system(cfg: dict) -> System:
    factory: Callable[[dict], System] = SYSTEMS.lookup(cfg["system"])
    return factory(cfg)
