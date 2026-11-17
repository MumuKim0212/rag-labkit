"""Every `labkit …` command the documents show exists, with the flags they give it.

The documents are the interface a team (and its agent) follows; a renamed flag or a command that was never built is
found here instead of by a team in the middle of a step. Only code is read: inline code spans and code blocks of
README.md, its Korean copy docs/README.ko.md and every Markdown file of the template (the step files, the READMEs,
CLAUDE.md, the skill). What a command does is not checked, only that the parser accepts its name and its options.
"""

import re
import shlex
from pathlib import Path

import pytest

from labkit.cli import build_parser
from test_harness import fake_judge, project  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "docs" / "README.ko.md", *sorted((ROOT / "labkit" / "template").rglob("*.md"))]
SPAN = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
STOP = {"·", ";", "|", "&&", "||", ">", "then", "or", "and"}  # several commands in one span: `run … · grade …`


def parser_table():
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices
    global_flags = set(parser._option_string_actions)
    return global_flags, {name: set(sub._option_string_actions) for name, sub in commands.items()}


def code_pieces(text: str):
    """Code from a document: each line of a code block (comments cut off), and each inline code span."""
    for block in FENCE.findall(text):
        for line in block.splitlines():
            yield line.split(" #", 1)[0]
    outside = FENCE.sub("", text)
    yield from SPAN.findall(outside)


def tokens(piece: str) -> list[str]:
    try:
        return shlex.split(piece.replace("…", " "), posix=True)
    except ValueError:  # an unmatched quote in a shortened example: split on spaces instead
        return piece.split()


def uses(piece: str):
    """(command, flags) for each `labkit <command> …` in a piece of code."""
    words = tokens(piece)
    for i, word in enumerate(words):
        if word != "labkit":
            continue
        rest, globals_, command = words[i + 1:], [], None
        while rest and rest[0].startswith("-"):  # `labkit --root PATH status`
            globals_.append(rest.pop(0))
            if globals_[-1] == "--root" and rest:
                rest.pop(0)
        if rest and re.fullmatch(r"[a-z][a-z-]*", rest[0]):
            command, rest = rest[0], rest[1:]
        flags = []
        for word_ in rest:
            if word_ in STOP or word_ == "labkit":
                break
            if word_.startswith("--"):  # `--split/--sets` names two options
                flags.extend(part.split("=", 1)[0] for part in word_.split("/") if part.startswith("--"))
        yield command, globals_, flags


def found_uses():
    for path in DOCS:
        for piece in code_pieces(path.read_text(encoding="utf-8")):
            for command, globals_, flags in uses(piece):
                yield path.relative_to(ROOT).as_posix(), piece.strip(), command, globals_, flags


def test_the_documents_show_labkit_commands_at_all():
    """Guards the scan itself: if it found nothing, the test below would pass on anything."""
    seen = {command for _, _, command, _, _ in found_uses()}
    assert {"run", "grade", "evaluate", "report", "compare", "check", "tick", "new-card", "golden-check"} <= seen


def test_every_documented_labkit_command_and_flag_exists():
    global_flags, commands = parser_table()
    wrong = []
    for where, piece, command, globals_, flags in found_uses():
        for flag in globals_:
            if flag not in global_flags:
                wrong.append(f"{where}: `{piece}`: labkit has no global option {flag}")
        if command is None:
            continue
        if command not in commands:
            wrong.append(f"{where}: `{piece}`: labkit has no command {command!r}")
            continue
        for flag in flags:
            if flag not in commands[command] and flag not in global_flags:
                wrong.append(f"{where}: `{piece}`: `labkit {command}` has no option {flag}")
    assert not wrong, "\n".join(wrong)


def documented_summary_keys() -> set[str]:
    """The keys in the first column of the `## summary.json` table of the template's results/README.md."""
    text = (ROOT / "labkit" / "template" / "results" / "README.md").read_text(encoding="utf-8")
    table = text.split("## summary.json", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| `")]
    return {key for row in rows for key in re.findall(r"`([a-z_]+)`", row.split("|")[1])}


def test_the_summary_table_lists_exactly_the_keys_a_summary_has(project):
    """A key labkit writes but the table does not explain, or one the table explains but labkit no longer writes."""
    from labkit.config import load_config
    from labkit.harness import evaluate_experiment, grade_experiment, run_experiment
    from test_harness import PRICES, FakeSystem
    run_experiment(load_config(project / "configs" / "baseline.yaml"), "dev", experiment="dev_x", system=FakeSystem())
    grade_experiment("dev_x")
    written = set(evaluate_experiment("dev_x", prices=PRICES))
    documented = documented_summary_keys()
    assert written - documented == set(), "written but not in results/README.md"
    assert documented - written == set(), "in results/README.md but not written"


@pytest.mark.parametrize("piece, expected", [
    ("labkit run --config configs/baseline.yaml --split dev --limit 1", [("run", [], ["--config", "--split", "--limit"])]),
    ("labkit --root proj check 3", [("check", ["--root"], [])]),
    ("labkit run … --split dev · labkit grade --experiment x", [("run", [], ["--split"]), ("grade", [], ["--experiment"])]),
    ('labkit tick 6 1 --evidence "card 004: sections 5-7 filled"', [("tick", [], ["--evidence"])]),
])
def test_the_scan_reads_commands_and_flags_as_written(piece, expected):
    assert list(uses(piece)) == expected
