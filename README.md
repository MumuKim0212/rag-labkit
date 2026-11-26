# RAG Lab Kit

[![tests](https://github.com/MumuKim0212/rag-labkit/actions/workflows/tests.yml/badge.svg)](https://github.com/MumuKim0212/rag-labkit/actions/workflows/tests.yml)

English | [한국어](docs/README.ko.md)

A workflow, templates, and small tools for running RAG experiments you can trust. It does not build a RAG
system for you; it keeps your experiments honest: one change per card, noise measured before anything is
called an improvement, the test split looked at only once, every model call costed.

This is a 0.1.x release: the commands and file formats may still change between releases.

## What it keeps you from

"B beat A" is how a RAG experiment usually ends, and three things make that sentence cheap to write: a rerun of A
scores differently, the test split gets looked at while tuning, and a change that raised a retrieval metric did
nothing for the answers. labkit puts a measurement or a refusal in front of each. The lines below are quoted from the
[example](examples/README.md), a small simulated project that runs without a model or an API key.

**A rerun is not the same run.** The baseline ran twice with nothing changed, and a question changed:

```transcript
$ python -m labkit.eval.paired out/grades/dev_000_baseline.jsonl out/grades/dev_000_baseline_r2.jsonl --max-score 2
n=24  out/grades/dev_000_baseline.jsonl 0.562  out/grades/dev_000_baseline_r2.jsonl 0.521  diff -0.042  95% CI [-0.125, +0.000]
out/grades/dev_000_baseline_r2.jsonl better 0 / out/grades/dev_000_baseline.jsonl better 1 / same 23
```

Later comparisons pass that figure as `--noise 1`, and a difference of one question is reported as noise.

**The test split is locked** until the last step of the workflow:

```transcript
$ labkit run --config configs/baseline.yaml --split test
test_000_baseline: changed vs baseline = nothing
labkit: error: test: the test split is locked until the final workflow step; pass --final then
(exit 1)
```

**A better proxy is not a better answer.** Passing four chunks to the answer step instead of two raised the share of
evidence in the context from 0.65 to 0.90:

```transcript
$ labkit evaluate --experiment dev_002_more_k_retrieval
dev_002_more_k_retrieval: complete=True, graded 0 of 24 questions
  evid@1 0.6500, evid 0.9000
```

The simulated model quotes the first chunk, and the first chunk did not change. The check that comes before an answer
run says so, the card records that the gates did not pass, and `labkit run` then refuses the run that would have cost
model calls:

```transcript
upside - risk = 0 vs noise 1: skip the answer run: it cannot show a gain
```

The walkthrough, including a card that was adopted on two independent answer runs, is in
[examples/](examples/README.md); `python examples/run_demo.py` repeats it in about half a minute.

## Install

```bash
git clone https://github.com/MumuKim0212/rag-labkit.git
cd rag-labkit
pip install -e .            # Python 3.10+, depends on PyYAML
pip install -e ".[env]"     # also python-dotenv, to read a project's .env
```

Or straight from GitHub without cloning, which is where a project's `requirements.txt` points (a release is a
git tag `vX.Y.Z` equal to `labkit.__version__`; it is not on PyPI):

```bash
pip install "rag-labkit[env] @ git+https://github.com/MumuKim0212/rag-labkit.git@v0.1.9"
```

## Start a project

```bash
labkit init my-rag --name "My RAG" --git     # copies the template into an empty folder
cd my-rag
labkit status                                # which of the eight steps is current
```

Then follow `docs/WORKFLOW.md` of the new project and the file for the current step in its `docs/steps/`; the
eight steps are described in the template's [WORKFLOW.md](labkit/template/docs/WORKFLOW.md). Your own code (the
system, the judge, any extra measures) is registered by name and listed under `modules:` in `labkit.yaml`.

## Commands

| | |
|---|---|
| `labkit run / grade / evaluate / compare / report` | the experiment loop; the test split needs `--final` |
| `labkit show EXPERIMENT` | read what a run logged: answers, context, citations, grades (smoke rows marked) |
| `labkit golden-check` | do the golden set's evidence quotes exist on their target pages? |
| `labkit new-card SLUG` | next experiment card and variant config |
| `labkit status / check / tick / approve / revoke` | the eight steps: what holds, who approved (on `main` only) |
| `labkit init PATH` | start a project from the template |
| `/labkit-next` (Claude Code) | the agent does the current step, claims what it verified, and stops for your approval |

`labkit --help` and `labkit <command> --help` list the options.

## Layout

```
labkit/             the package: eval tools, harness, steps, cards, CLI
labkit/template/    what `labkit init` copies: the workflow and one file per step, the card, CLAUDE.md, configs, report/
examples/           a runnable walkthrough on a simulated system, with its output
tests/              python -m pytest
docs/               this repository's own documents (the Korean README)
```

## License

[MIT License](LICENSE)
