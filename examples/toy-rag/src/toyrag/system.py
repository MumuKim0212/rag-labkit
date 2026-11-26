"""The toy RAG system: keyword retrieval over a few pages, and a simulated "answer model".

Nothing here calls a real model, so the example runs without a network or an API key. What it keeps from a real
system is what labkit needs to see:

- retrieval over chunks, whose size is a setting (`chunking.sentences`) and whose count is another (`retrieval.k`);
- an answer step that sees only the retrieved context, cites where it read, and abstains when nothing matches;
- sampling noise: with probability `answer.temperature` the simulated model quotes the second-best chunk instead of
  the best one, so two runs of one config do not give identical answers (the noise step 3 of the workflow measures);
- every model call recorded with labkit.cost.record_call, so the cost columns of the report are filled.

The noise is seeded so the example reproduces: the seed is the environment variable TOYRAG_SEED (unset: a new
random seed per process, as a real model would give), mixed with the question.
"""

import json
import math
import os
import random
from collections import Counter

from labkit.cost import record_call
from labkit.index import index_key
from labkit.paths import project_root
from labkit.project import load_project
from labkit.systems import register_system

from .text import sentences, tokens, words

NO_ANSWER = "I could not find this in the documents."


class ToyRAG:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.k = cfg["retrieval"]["k"]
        self.size = cfg["chunking"]["sentences"]
        self.temperature = cfg["answer"]["temperature"]
        self.abstain_below = cfg["answer"]["abstain_below"]
        self.model = cfg["answer"]["model"]
        self.pages_path = project_root() / cfg["corpus"]["pages"]
        self.index_key = index_key(cfg, load_project())
        self.chunks: list[dict] = []
        self.terms: list[Counter] = []
        self.df: Counter = Counter()
        if self.pages_path.exists():
            self._build()

    def _build(self) -> None:
        with self.pages_path.open(encoding="utf-8") as f:
            pages = [json.loads(line) for line in f if line.strip()]
        for page in pages:
            parts = sentences(page["text"])
            for i in range(0, len(parts), self.size):
                self.chunks.append({"chunk_id": f"{page['file']}#{page['page']}.{i // self.size}",
                                    "source": page["file"], "page": page["page"], "content": " ".join(parts[i:i + self.size])})
        self.terms = [Counter(words(c["content"])) for c in self.chunks]
        self.df = Counter(t for terms in self.terms for t in terms)

    def _idf(self, term: str) -> float:
        return math.log(1 + len(self.chunks) / (self.df.get(term, 0) + 0.5))

    def ready(self) -> tuple[bool, str]:
        if not self.chunks:
            return False, f"no pages at {self.pages_path}"
        return True, ""

    def _search(self, question: str) -> list[tuple[float, dict]]:
        """Chunks that share a word with the question, best first, each with the share of the question's weight it covers."""
        query = set(words(question))
        total = sum(self._idf(t) for t in query) or 1.0
        scored = [(sum(self._idf(t) for t in query if t in terms) / total, chunk) for terms, chunk in zip(self.terms, self.chunks)]
        scored = [s for s in scored if s[0] > 0]
        return sorted(scored, key=lambda s: -s[0])  # sorted is stable: a tie keeps the order of the corpus

    def retrieve(self, question: str, scope: str, steps: dict | None = None) -> list[dict]:
        return [dict(chunk) for _, chunk in self._search(question)[: self.k]]

    def run(self, question: str, scope: str) -> dict:
        found = self._search(question)[: self.k]
        context = [dict(chunk) for _, chunk in found]
        searches = [{"query": question, "chunk_ids": [c["chunk_id"] for c in context], "seconds": 0.0}]
        steps = {"index_key": self.index_key}
        coverage = found[0][0] if found else 0.0
        prompt_chars = len(question) + sum(len(c["content"]) for c in context)
        if coverage < self.abstain_below:
            answer, status, cited = NO_ANSWER, "abstained", []
        else:
            seed = f"{os.environ.get('TOYRAG_SEED', random.random())}:{question}"
            pick = 1 if len(context) > 1 and random.Random(seed).random() < self.temperature else 0
            chosen = context[pick]
            answer = f"{chosen['content']} [{chosen['source']}, p. {chosen['page']}]"
            status, cited = "answered", [chosen]
        record_call(steps, node="answer", model=self.model, input_tokens=prompt_chars // 4 + 30,  # 30: the prompt's own words
                    output_tokens=tokens(answer))
        return {"status": status, "answer": answer, "searches": searches, "context": context, "steps": steps,
                "citations": [{"chunk_id": c["chunk_id"], "source": c["source"], "page": c["page"]} for c in cited],
                "n_searches": 1}


@register_system("toy-rag")
def make_toy_rag(cfg: dict) -> ToyRAG:
    return ToyRAG(cfg)
