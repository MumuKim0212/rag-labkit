<!--
The judge rubric of the toy example. src/toyrag/judge.py applies it in code instead of asking a model.
-->

Score the answer against the reference answer: 2 if it holds every content word of the reference, 1 if at least half, otherwise 0. A question the documents cannot answer scores 2 for an abstention and 0 for an answer.
