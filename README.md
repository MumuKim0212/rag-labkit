# RAG Lab Kit

English | [한국어](docs/README.ko.md)

A workflow, templates, and small tools for running RAG experiments you can trust. It does not build a RAG
system for you; it keeps your experiments honest: one change per card, noise measured before anything is
called an improvement, the test split looked at only once, every model call costed.

This is a 0.1.x release: the commands and file formats may still change between releases.

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
tests/              python -m pytest
docs/               this repository's own documents (the Korean README)
```

## License

[MIT License](LICENSE)
