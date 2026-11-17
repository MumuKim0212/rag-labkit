"""Recorded calls and their cost, using the project's configs/pricing.yaml.

Every LLM, embedding, or rerank call a system makes is appended to `steps["calls"]` with `record_call`,
whatever the provider. A call is {"node", "kind", "model", "input_tokens", "output_tokens",
"thinking_tokens", "seconds"}; `output_tokens` includes thinking tokens, which are billed as output.
`totals` turns a list of calls into USD and token counts.

pricing.yaml: `models: {<model id>: {input: <USD per 1M tokens>, output: <USD per 1M tokens>}}`.
"""

import math
import numbers
from pathlib import Path

import yaml

from .paths import pricing_path

TOKEN_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens")


def record_call(steps: dict, *, node: str, model: str, kind: str = "llm", input_tokens: int = 0,
                output_tokens: int = 0, thinking_tokens: int = 0, seconds: float = 0.0, **extra) -> dict:
    """Append one call to steps["calls"]. `output_tokens` must include thinking tokens (billed as output)."""
    call = {"node": node, "kind": kind, "model": model, "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0), "thinking_tokens": int(thinking_tokens or 0),
            "seconds": round(float(seconds), 3), **extra}
    steps.setdefault("calls", []).append(call)
    return call


def load_prices(path: Path | str | None = None) -> dict:
    with Path(path or pricing_path()).open(encoding="utf-8") as f:
        return yaml.safe_load(f)["models"]


def call_usd(call: dict, prices: dict) -> float:
    model = call["model"]
    if model not in prices:
        raise KeyError(f"no price for {model!r}: add it to configs/pricing.yaml")
    p = prices[model]
    if call.get("output_tokens", 0) and "output" not in p:  # `outpt: 3.75` priced every answer's output at zero
        raise KeyError(f"no `output` price for {model!r} in configs/pricing.yaml (a misspelled key?) but the call "
                       f"produced {call['output_tokens']} output tokens")
    return (call.get("input_tokens", 0) * p["input"] + call.get("output_tokens", 0) * p.get("output", 0)) / 1e6


def calls_of(steps) -> list[dict]:
    """Recorded calls in a run's `steps` ({"calls": [...], ...}); empty when nothing was recorded."""
    return list((steps or {}).get("calls", [])) if isinstance(steps, dict) else []


def calls_problem(calls) -> str | None:
    """Why `calls` cannot be costed, or None. A list of mappings, each with a `model` and, when it has token counts,
    finite non-negative numbers: anything else raised a TypeError or KeyError after the call was paid for, or at
    evaluate time, far from the system that wrote it."""
    if not isinstance(calls, (list, tuple)):
        return f"`calls` is {type(calls).__name__}, not a list"
    for i, call in enumerate(calls):
        if not isinstance(call, dict):
            return f"calls[{i}] is {type(call).__name__}, not a mapping"
        if not isinstance(call.get("model"), str) or not call["model"].strip():
            return f"calls[{i}] has no `model` (the price is looked up by it)"
        for field in TOKEN_FIELDS:
            if field in call:
                value = call[field]
                if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value) or value < 0:
                    return f"calls[{i}].{field} is {value!r}, not a finite number of at least 0"
    return None


def steps_problem(steps) -> str | None:
    """`calls_problem` of a run's `steps`; None when no calls were recorded."""
    return calls_problem(steps["calls"]) if isinstance(steps, dict) and "calls" in steps else None


def totals(calls: list[dict], prices: dict) -> dict:
    out = {"usd": 0.0, "usd_by_model": {}, "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
           "llm_calls": 0, "embedding_calls": 0}
    for call in calls:
        usd = call_usd(call, prices)
        out["usd"] += usd
        out["usd_by_model"][call["model"]] = out["usd_by_model"].get(call["model"], 0.0) + usd
        for field in ("input_tokens", "output_tokens", "thinking_tokens"):
            out[field] += call.get(field, 0) or 0
        out["embedding_calls" if call.get("kind") == "embedding" else "llm_calls"] += 1
    return out
