"""The three items round 7 left for the owner, done after v0.1.6.

- An answer run of a variant needs section 5 of its card to say the LLM-free gates passed (CLAUDE.md: "No batch LLM
  run without a card whose gates passed" was a sentence; labkit could not see the gates).
- A paired comparison of fewer than 10 questions starts with a warning (N2, tests/test_eval.py).
- The documents' `labkit` commands and flags, and the summary table, are checked against the code
  (tests/test_doc_commands.py).
"""

import pytest

from labkit.cli import main
from test_cli import CARD, project, variant  # noqa: F401

GATES = "- Gates passed? yes\n"
RUN = ["run", "--config", "configs/004-x.yaml", "--split", "dev"]


def card_with(line: str) -> str:
    assert GATES in CARD
    return CARD.replace(GATES, line)


@pytest.mark.parametrize("line, said", [
    ("- Gates passed? _yes / no — if no, skip section 6 and decide in section 7_\n", "is not filled in"),
    ("- Gates passed?\n", "is not filled in"),
    ("- Gates passed? no, net +1 is within the noise\n", "says the gates did not pass (no, net +1"),
    ("- Gates passed? pending\n", "says the gates did not pass (pending)"),
    ("", "section 5 has no `- Gates passed?` line"),
])
def test_an_answer_run_needs_section_5_to_say_the_gates_passed(project, capsys, line, said):
    variant(project, card_with(line))
    assert main(RUN) == 1
    err = capsys.readouterr().err
    assert said in err and "decide in section 7 without an answer run" in err
    assert not (project / "out" / "runs.db").exists()                       # refused before any question ran


def test_the_llm_free_run_and_a_smoke_test_come_before_the_gates(project, capsys):
    variant(project, card_with("- Gates passed? _yes / no_\n"))
    assert main(RUN + ["--retrieval-only"]) == 0                               # it is what fills section 5
    assert main(RUN + ["--limit", "1"]) == 0                                  # a smoke test is exempt, as for 1–4


@pytest.mark.parametrize("line", ["- Gates passed? yes\n", "- Gates passed? Yes, proxy +4 and headroom +3\n",
                                  "- Gates passed? n/a: answer-step change, always gets an answer run\n"])
def test_yes_or_the_answer_step_exemption_lets_the_answer_run_start(project, line):
    variant(project, card_with(line))
    assert main(RUN) == 0


def test_a_card_without_section_5_is_refused_for_an_answer_run(project, capsys):
    variant(project, CARD.split("## 5.")[0])
    assert main(RUN) == 1
    assert "section 5 (LLM-free results) is missing" in capsys.readouterr().err


def test_the_baseline_needs_no_card_and_no_gates(project):
    assert main(["run", "--config", "configs/baseline.yaml", "--split", "dev"]) == 0

