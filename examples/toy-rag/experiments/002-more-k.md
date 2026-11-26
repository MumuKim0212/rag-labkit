# 002: More chunks

Status: planned
Date: 2026-11-26
Branch: `demo/002-more-k` · Config: `configs/002-more-k.yaml`

## 1. Hypothesis

- Change (one variable): chunks passed to the answer step, 2 → 4 (`retrieval.k`)
- Failure it targets: the answer sentence is retrieved but ranked below the first chunk (Q004, Q008, Q010, Q013)
- Expected effect: evidence coverage in the context rises; the answer step may find the sentence it needs

## 2. Measurement

- Compared against (reused, not rerun): dev_000_baseline
- Proxy metric (step 4): evid@1, the share of evidence quotes found in the first chunk, because the simulated answer model quotes the first chunk
- Split: dev
- Noise (step 3): (a) 1 of 24 changed, (b) 0 of 24 changed

## 3. Go/no-go criteria (written before results)

- LLM-free gate: evid@1 net gain ≥ 4 questions on dev, larger than the noise of 1
- Headroom gate: upside − risk ≥ 4 questions (`python -m labkit.eval.headroom`)
- Cost cap: tokens per question ≤ 2× baseline
- Adopt if (answer run): paired net wins larger than the noise of 1 and the 95% CI excludes 0, or the same direction in a second independent run

## 4. LLM budget

- Planned calls if the gates pass: answers 24 + judge 24
- Calls so far in the project: see the ledger in report/PROCESS.md

## 5. LLM-free results

| Config | Proxy metric (dev) | Net vs baseline (+/−) | Context chars / est. tokens |
|---|---|---|---|
| baseline | | – | |
| _variant_ | | | |

- Headroom: upside _N_ (_qids_), already correct among improved _N_, risk _N_ (_qids_)
- Gates passed? _yes / no — if no, skip section 6 and decide in section 7_

## 6. Answer run (only if the gates passed)

- Experiment name: _..._
- Paired vs baseline: _B better N / A better N / same N, diff ±0.000, 95% CI [_, _]_
- Tokens / latency vs baseline: _..._
- LLM calls used: _answers __ + judge ___

## 7. Decision and notes

- Decision: _adopt / reject / inconclusive / below criteria, adopted by the owner_ · Approved by: _name, date_
- Why, with question IDs: _..._
- What the criteria missed, if anything: _..._
