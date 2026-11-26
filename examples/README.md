# Example: from an empty folder to two decided experiment cards

`toy-rag/` is a small RAG project that runs without a model, a network or an API key, and `run_demo.py` walks it
through the first steps of labkit's workflow with labkit's own commands. It exists to show what labkit does to an
experiment: it makes the noise visible, makes you write the criteria first, and refuses the steps that would let a
result fool you.

The models are simulated, and the numbers below come from a made-up corpus of 11 pages and 24 dev questions, so they
say nothing about any real system. The question wording and thresholds were tuned while building the example so
that it shows each thing once; it is a demonstration, not a benchmark.

## Run it

```bash
pip install -e .                                 # from the repository root; the demo also needs git
python examples/run_demo.py                      # about 30 seconds, in a temporary project
python examples/run_demo.py --keep my-demo       # the same, and leave the project in my-demo/ to look around
```

The output is [transcript.txt](transcript.txt). `tests/test_examples.py` runs the demo and fails when that file is out
of date, and when a block below stops being a piece of it.

## What is in it

| Path | What it is |
|---|---|
| `toy-rag/corpus/pages.jsonl` | 11 pages of three made-up documents: an employee handbook, an e-bike manual, a warranty |
| `toy-rag/golden/golden.jsonl` | 32 questions: 24 dev (20 answerable, 4 not) and 8 test (6 answerable, 2 not) |
| `toy-rag/src/toyrag/` | the system (keyword retrieval, a simulated answer model) and a judge that applies a rubric in code |
| `toy-rag/configs/` | the baseline and two variants, each changing one setting; prices (invented) |
| `toy-rag/experiments/` | the two cards, with sections 1-4 written before any result |
| `run_demo.py` | starts a project with `labkit init`, copies the files above over it, then runs the commands |

Only what a project writes itself is here. The workflow documents, the card template and the step files come from
`labkit init`, so they are not copied into this folder.

The baseline cuts each page into one-sentence chunks, passes the best two to the answer step, and the simulated
answer model quotes the best chunk. With probability 0.1 it quotes the second one instead; that is the sampling noise
of a real model, and the demo fixes it with `TOYRAG_SEED` so that its output repeats. It abstains when the best chunk
covers too little of the question. The judge scores 0 to 2 from the words of the reference answer.

## 1. Noise comes first

The baseline runs once, then runs again under another seed, and its answers are graded a second time:

```transcript
$ python -m labkit.eval.paired out/grades/dev_000_baseline.jsonl out/grades/dev_000_baseline_r2.jsonl --max-score 2
n=24  out/grades/dev_000_baseline.jsonl 0.562  out/grades/dev_000_baseline_r2.jsonl 0.521  diff -0.042  95% CI [-0.125, +0.000]
out/grades/dev_000_baseline_r2.jsonl better 0 / out/grades/dev_000_baseline.jsonl better 1 / same 23
```

One of 24 questions changed with nothing changed, so a later difference of one question means nothing. Grading the
same answers twice changed none, so here the noise comes from generation and not from the judge. Every later
comparison passes the figure as `--noise 1`.

## 2. What labkit refuses

The test split stays locked until the last step, and a variant cannot run until its card says what it expects:

```transcript
$ labkit run --config configs/baseline.yaml --split test
test_000_baseline: changed vs baseline = nothing
labkit: error: test: the test split is locked until the final workflow step; pass --final then
(exit 1)
```

An answer run, which is what costs model calls, is refused until the LLM-free gates have been run and written down:

```transcript
$ labkit run --config configs/001-bigger-chunks.yaml --split dev
dev_001_bigger_chunks: changed vs baseline = {'chunking.sentences': {'from': 1, 'to': 2}}
labkit: error: experiments/001-bigger-chunks.md: the `Gates passed?` line of section 5 is not filled in. An answer run comes after the LLM-free gates: run `--retrieval-only` and the headroom check, fill section 5 and write `- Gates passed? yes` (a card for an answer-step change writes `n/a: answer-step change, always gets an answer run`); if they did not pass, decide in section 7 without an answer run
(exit 1)
```

## 3. Card 001: bigger chunks, adopted on two runs

The card's failure is a first chunk that is the neighbor of the sentence holding the answer ("It takes about 4 hours
to charge fully" does not repeat "battery"). Two sentences per chunk should put them together. The LLM-free check
searches only, with no model call, and compares the share of evidence found in the first chunk (`evid@1`):

```transcript
$ labkit compare dev_000_baseline_retrieval dev_001_bigger_chunks_retrieval --metric evid@1 --noise 1
n=20  dev_000_baseline_retrieval 0.650  dev_001_bigger_chunks_retrieval 0.950  diff +0.300  95% CI [+0.100, +0.500]
dev_001_bigger_chunks_retrieval better 6 / dev_000_baseline_retrieval better 0 / same 14
```

The headroom check asks how many of those six could change an answer score at all:

```transcript
$ python -m labkit.eval.headroom --scores results/dev_000_baseline/grades.jsonl --before results/dev_000_baseline_retrieval/metrics.jsonl --after results/dev_001_bigger_chunks_retrieval/metrics.jsonl --metric evid@1 --noise 1
n=20  metric improved 6, worsened 0
upside 6 (improved, baseline not fully correct): Q004 Q008 Q010 Q013 Q014 Q018
spent  0 (improved, baseline already fully correct)
```

Only then are the model calls spent. The first answer run gains three questions net, which is more than the noise of
one but not enough for the 95% interval to leave zero:

```transcript
$ labkit compare dev_000_baseline dev_001_bigger_chunks --noise 1
n=24  dev_000_baseline 0.562  dev_001_bigger_chunks 0.688  diff +0.125  95% CI [-0.042, +0.292]
dev_001_bigger_chunks better 4 / dev_000_baseline better 1 / same 19
  dev_001_bigger_chunks better: Q004 Q013 Q014 Q018
  dev_000_baseline better: Q007
net +3 questions vs noise 1: larger than noise
        USD/question: 7.4e-05 -> 0.000118  (1.59x)
     tokens/question: 100.2 -> 153.9  (1.54x)
```

Q008 and Q010 had the right chunk first, and the simulated model quoted the second one instead
(`labkit show dev_001_bigger_chunks --qid Q008` prints what it was given and what it said). The card allowed for noise
before any result existed: it also accepts the same direction in a second independent run, and the second run gains six, with the
interval above zero:

```transcript
$ labkit compare dev_000_baseline dev_001_bigger_chunks_r2 --noise 1
n=24  dev_000_baseline 0.562  dev_001_bigger_chunks_r2 0.812  diff +0.250  95% CI [+0.083, +0.417]
dev_001_bigger_chunks_r2 better 6 / dev_000_baseline better 0 / same 18
```

The change also costs about 1.5 times the tokens per question, which is inside the card's cap of 2. Adopting it is the
owner's decision; the demo stops where the card's section 7 would be written.

## 4. Card 002: more chunks, stopped before it cost anything

Passing four chunks to the answer step instead of two puts the evidence into the context more often, and the report
shows it:

```transcript
$ labkit evaluate --experiment dev_002_more_k_retrieval
dev_002_more_k_retrieval: complete=True, graded 0 of 24 questions
  evid@1 0.6500, evid 0.9000
```

But the simulated model quotes the first chunk, and the first chunk did not change. The metric that tracks the answer
here is `evid@1`, and it does not move:

```transcript
$ python -m labkit.eval.headroom --scores results/dev_000_baseline/grades.jsonl --before results/dev_000_baseline_retrieval/metrics.jsonl --after results/dev_002_more_k_retrieval/metrics.jsonl --metric evid@1 --noise 1
n=20  metric improved 0, worsened 0
upside 0 (improved, baseline not fully correct): 
spent  0 (improved, baseline already fully correct)
risk   0 (worsened, baseline scored above 0): 
upside - risk = 0 vs noise 1: skip the answer run: it cannot show a gain
```

The card records `Gates passed? no`, and `labkit run` then refuses the answer run, so no model call was spent on it.
The summary table is where a reader sees both cards together:

```transcript
| Experiment | Changed | Score | evid@5 | Tokens/question |
| --- | --- | --- | --- | --- |
| dev_000_baseline | baseline config | 0.562 | 0.650 | 100 |
| dev_000_baseline_r2 | baseline config | 0.521 (-0.042) | 0.650 | 100 (-0) |
| dev_000_baseline_retrieval | baseline config | – | 0.650 | 0 |
| dev_001_bigger_chunks | chunking.sentences: 1 → 2 | 0.688 (+0.125) | 0.950 (+0.300) | 154 (+54) |
| dev_001_bigger_chunks_r2 | chunking.sentences: 1 → 2 | 0.812 (+0.250) | 0.950 (+0.300) | 153 (+53) |
| dev_001_bigger_chunks_retrieval | chunking.sentences: 1 → 2 | – | 0.950 (+0.300) | 0 |
| dev_002_more_k_retrieval | retrieval.k: 2 → 4 | – | 0.900 (+0.250) | 0 |
```

## What the demo leaves out

It does not run `labkit approve`, so the steps are never approved, and it does not fill in `report/PROCESS.md`; both
are the owner's part of the workflow. It skips the proxy choice (step 4), the triage of every failure (step 5), the
structure step (7) and the one run of the test split (8). `labkit check 3` in the transcript shows what is left of
step 3 when the code-checked part is done: the figure still has to be written down and claimed with `labkit tick`.

## Use a real model

The system's contract is in `labkit/systems.py`. Replace `toyrag/system.py` and `toyrag/judge.py` with code that calls
your models, record every call with `labkit.cost.record_call`, put your model names and prices in
`configs/pricing.yaml`, and keep the judge a different model from the answer model: `labkit run` refuses a config
where they are the same.
