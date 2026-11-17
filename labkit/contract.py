"""The one boundary between what a system returns and what labkit stores, grades and evaluates.

`harness.run_experiment` passes every result through `plain` and then `check_result` before it is saved. A result
that fails the check becomes a failed question (the batch goes on, a rerun retries it) and keeps its `steps` when
their calls can be costed, so the money already spent still shows as `wasted_usd`.

What is checked is what labkit itself reads later, and only its shape (labkit.systems has the full contract):

- `context`: a list of units, each a mapping with `chunk_id` (text or a whole number), `source` (text), `page` (a
  whole number) and `content` (text); `pages`, when present, is a list of whole numbers. Other keys (`score`, ...)
  are the system's own.
- `citations`: a list of mappings with `chunk_id`, `source` and `page` as above.
- `searches`: a list of mappings whose `chunk_ids` is a list of chunk ids.
- `steps`: a mapping (its `calls` are checked by labkit.cost.calls_problem).

null means "not given" everywhere, as `targets: null` does in a golden item: `context: null` is no context,
`chunk_ids: null` an empty list. Anything else is left to the system (`answer`, `error`, extra keys).

Before the check, `plain` turns numbers of other types (numpy's int64 and float64, Decimal, Fraction) into int and
float and tuples into lists: the log stored a numpy count from one question and a TypeError followed at evaluate.
"""

import numbers
from collections.abc import Mapping


def plain(value, _active=None):
    """`value` with every number that is not a bool turned into int or float, mappings into dicts and tuples into
    lists, recursively. Text, None, bools and unknown objects are returned as they are (the log decides about those).
    A container that contains itself is returned unchanged at the point of the loop."""
    if value is None or type(value) in (str, bool, int, float):  # numpy's float64 is a float subclass: not here
        return value
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, (Mapping, list, tuple)):
        _active = _active if _active is not None else set()
        if id(value) in _active:
            return value
        _active.add(id(value))
        try:
            if isinstance(value, Mapping):
                return {plain(k, _active): plain(v, _active) for k, v in value.items()}
            return [plain(v, _active) for v in value]
        finally:
            _active.discard(id(value))
    return value


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _chunk_id(value) -> bool:
    return isinstance(value, str) or _whole(value)


def _type(value) -> str:
    return "null" if value is None else type(value).__name__


def _unit_problem(unit, where: str, needs_content: bool) -> str | None:
    if not isinstance(unit, Mapping):
        return f"{where} is {_type(unit)}, not a mapping"
    if not _chunk_id(unit.get("chunk_id")):
        return f"{where}.chunk_id is {unit.get('chunk_id')!r}, not text or a whole number"
    if not isinstance(unit.get("source"), str):
        return f"{where}.source is {unit.get('source')!r}, not text"
    if not _whole(unit.get("page")):
        return f"{where}.page is {unit.get('page')!r}, not a whole number"
    if needs_content and not isinstance(unit.get("content"), str):
        return f"{where}.content is {_type(unit.get('content'))}, not text"
    pages = unit.get("pages")
    if pages is not None and not (isinstance(pages, list) and all(_whole(p) for p in pages)):
        return f"{where}.pages is {pages!r}, not a list of whole numbers"
    return None


def _list_problem(result: Mapping, key: str) -> str | None:
    value = result.get(key)
    if value is not None and not isinstance(value, list):
        return f"`{key}` is {_type(value)}, not a list"
    return None


def check_result(result: Mapping) -> str | None:
    """The first reason labkit cannot use this (already `plain`) result, or None."""
    for key in ("context", "citations", "searches"):
        problem = _list_problem(result, key)
        if problem:
            return problem
    for i, unit in enumerate(result.get("context") or []):
        problem = _unit_problem(unit, f"context[{i}]", needs_content=True)
        if problem:
            return problem
    for i, citation in enumerate(result.get("citations") or []):
        problem = _unit_problem(citation, f"citations[{i}]", needs_content=False)
        if problem:
            return problem
    for i, search in enumerate(result.get("searches") or []):
        if not isinstance(search, Mapping):
            return f"searches[{i}] is {_type(search)}, not a mapping"
        ids = search.get("chunk_ids")
        if ids is not None and not (isinstance(ids, list) and all(_chunk_id(c) for c in ids)):
            return f"searches[{i}].chunk_ids is {ids!r}, not a list of chunk ids"
    steps = result.get("steps")
    if steps is not None and not isinstance(steps, Mapping):
        return f"`steps` is {_type(steps)}, not a mapping"
    return None


def search_count(result: Mapping):
    """`n_searches` when it is a whole number of at least 0 (or not given); else the number of `searches` recorded."""
    n = result.get("n_searches")
    if n is None or (_whole(n) and n >= 0):
        return n
    searches = result.get("searches")
    return len(searches) if isinstance(searches, list) else None
