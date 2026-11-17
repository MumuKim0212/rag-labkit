"""Which settings change the index, so a variant cannot silently reuse another variant's index.

labkit does not build indexes: that depends on your vector store, embedding model, and chunker. It only needs
to know which config keys the index depends on, declared in labkit.yaml:

    index_keys: [corpus, chunking, embedding]      # dotted prefixes into the config

`index_key(cfg, project)` is a short hash of those settings. It is recorded with every run and shown in the
report, so a reviewer sees which experiments shared an index. Two ways to make the guarantee hold:

- Name your index folder by the key, so a changed chunk size builds a new index.
- Have the system report the key it actually used (`steps["index_key"]`): labkit fails the question if it
  differs from the config's, which catches a variant that quietly reused the baseline's index.

To report what building the index cost, have your builder write `build.json` ({"calls": [...]}, the same call
records as `labkit.cost.record_call`) into the index folder and say where in labkit.yaml:

    index_dir: out/index/{index_key}/{scope}       # {index_key} and {scope} are the only placeholders

`labkit evaluate` then sums the builds of the scopes the experiment used into `cost.index_build_usd` (null when
`index_dir` is not set or no build.json is there).

A system may also define `ready() -> (bool, str)`; labkit calls it before a batch and stops with the reason when
the index is missing (a test run that finds no index half-way is expensive).
"""

import hashlib
import json

from .config import flatten, settings


def index_key(cfg: dict, project: dict) -> str | None:
    prefixes = project.get("index_keys") or []
    if not prefixes:
        return None
    picked = {k: v for k, v in flatten(settings(cfg)).items() if any(k == p or k.startswith(p + ".") for p in prefixes)}
    blob = json.dumps(picked, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]
