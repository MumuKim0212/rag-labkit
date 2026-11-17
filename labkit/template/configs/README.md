<!--
GUIDE: How experiment configs work. Keep in sync with labkit.config.
Keep guide comment in place after editing.
-->

# configs

- `baseline.yaml`: every setting of the baseline. Treat it as frozen from the first real run; labkit enforces it once an experiment that used it has been evaluated.
- `pricing.yaml`: model prices; the only place prices live.
- `<card>-<variant>.yaml`: one file per experiment variant, listing only what it changes.

## Writing a variant

```yaml
# configs/004-hybrid.yaml
base: baseline.yaml
name: 004_hybrid
card: experiments/004-hybrid.md
description: hybrid search, alpha 0.5
retrieval:
  method: hybrid
```

- Meta keys (`name`, `card`, `description`, `compare_to`) are not settings, and each file states its own: they are **not inherited** from `base`. A variant without `name:` is named after its file; without `card:` it has no card (and `labkit run` refuses it). `compare_to` is the experiment the report diffs against; default `<split>_000_baseline`. Set it when the comparison baseline moved (e.g. `compare_to: dev_001_chunk400`), and again in every config built on that one.
- `base` may point to another variant (e.g. after a change was adopted, `base: 001-chunk400.yaml`). Settings are merged top-down along the chain; a loop is an error.
- `labkit` records three things in every run and `summary.json`: `changed`, the keys that differ from `baseline.yaml` (a non-empty `changed` makes the config a variant, which needs a card); `changed_from_base`, the keys that differ from the file named by `base:`, which is what this card itself changes; and a hash of the merged settings (`config_hash`).
- One variant changes one thing. If `changed_from_base` lists more than the card says, fix the file before running. For a config built directly on `baseline.yaml` the two lists are the same; on top of an adopted variant, `changed` also lists everything that variant changed and is expected to be longer.
- `run.concurrency` (worker threads) is how many workers, not what is computed: it is not hashed and not in `changed`, so changing it never makes a baseline run a variant or blocks resuming. The other `run:` keys (the template has `timeout_s` and `max_retries`) are settings like any other: changing them makes a variant that needs a card. A frozen config file (the baseline, once evaluated) cannot be edited even for this key: set `run: {concurrency: N}` in a child config with `base: baseline.yaml`.
- Experiment name = `<split>_<name>`, e.g. `dev_004_hybrid`.
  - Retrieval-only runs get `_retrieval` and never share a name with an answer run (`labkit run` refuses to resume one as the other, which matters with `--name`).
  - A name is unique: a rerun gets a new one, e.g. `004_hybrid_r2` (the noise step needs that pattern).
  - It is a folder under `results/` and a file under `out/grades/`, so it uses letters (any script), digits, `_`, `-` and `.`, and differs from every other experiment by more than case. `labkit` refuses any other name, a `--name` or config `name:` included, and its message lists the full rules (length, Windows device names, ...).
  - A grades suffix is `__regrade` plus letters, digits, `_`, `-` or `.` (e.g. `__regrade1`). An experiment name may not be another experiment's name plus `__…`, since that is the file a regrade of the first one writes.

## Keys labkit reads

| Key | Used for |
|---|---|
| `system` | which registered system runs (`@register_system`) |
| `judge.name`, `judge.model`, `judge.prompt` | which registered judge grades; model and prompt are recorded in each grade row. The `judge.prompt` file must exist before an answer run starts (`labkit run` refuses otherwise), and it is frozen from then on |
| `answer.model` | the answer model; `labkit check` (step 1) requires it to differ from `judge.model` in every config (case and surrounding blanks do not make two names different models), and `labkit run` refuses an answer run (not `--retrieval-only`) whose two are equal, at any step |
| `run.concurrency` | worker threads for a batch (default 1); the only key under `run:` that is not hashed |
| `corpus.set_scope` | optional: golden `set` → the scope label passed to the system; it must name every golden set the run covers, and a missing one is refused before the first question |
| `card`, `compare_to`, `description` | report columns and what the report diffs against (text). A variant (a config with `changed`) must name its `card:`; `labkit run` checks that sections 1–4 are written |

Shapes are checked when a config is loaded, with the file and key in the message: `answer`, `judge`, `run` and `corpus` are mappings, `run.concurrency` is a whole number of at least 1, `base`, `card`, `compare_to`, `description`, `judge.name` and `judge.prompt` are text, `corpus.set_scope` maps each set to a text label, and `system` is present when a config is run. An answer run (not `--retrieval-only`) is refused before the first question when `judge.name` is not a registered judge, so a misspelled judge is found before the answers are paid for, not at `labkit grade`.

Everything else (chunking, retrieval, models, prompts) is yours: it is part of `config_hash` and shows up in `changed` and `changed_from_base`. Two limits, both refused when the config loads, naming the key:

- The keys labkit writes into every run's stored config are reserved (a `smoke: true` of your own would turn every full run into a smoke test): `smoke`, `selection`, `environment`, `retrieval_only`, `file_hashes`, `project_settings`, `card_check`, `index_key`, and the computed `changed`, `changed_from_base`, `config_hash`, `config_file`.
- Keys and values must be something the run log can store: text, numbers, true/false, lists and mappings. YAML reads an unquoted `2026-01-01` as a date, `.inf` as infinity, and `yes:`, `on:` and `1:` as keys that are not text, so write those in quotes (`'2026-01-01'`, `'yes':`).

## pricing.yaml

`models: {<model id>: {input: <USD per 1M tokens>, output: <USD per 1M tokens>}}`; the header of the file says how prices are kept (old entries are never edited). A call to a model with no entry fails loudly, and so does a call that produced output tokens for a model whose entry has no `output` (a misspelled key would otherwise price the output at zero); an embedding model may list `input` only. labkit reads only `input` and `output`; extra keys such as `valid_until` are for people.
