"""The toy-rag example (examples/) runs end to end, and what its documents quote is what it prints.

examples/run_demo.py starts a project with `labkit init`, copies the example's files over it and walks the first
steps of the workflow with labkit's own commands, over a simulated system (no model, no network). Its output is
committed as examples/transcript.txt. Two things are checked here:

- the committed transcript is what the demo prints now (the seeds that make the simulated model's sampling repeat
  are in the script), so a change in labkit's output or in the example shows up as a failing test, not as a README
  that quietly stopped being true;
- every block the example's README marks as ```transcript is a piece of that transcript, line for line.
"""

import difflib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
TRANSCRIPT = EXAMPLES / "transcript.txt"
README = EXAMPLES / "README.md"

pytestmark = [pytest.mark.needs_git]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture(scope="module")
def fresh(tmp_path_factory):
    """The demo's output now. The demo is slow (about half a minute: it runs labkit some forty times), so it runs once."""
    out = tmp_path_factory.mktemp("demo") / "transcript.txt"
    done = subprocess.run([sys.executable, str(EXAMPLES / "run_demo.py"), "--transcript", str(out)],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-1500:]
    return read(out)


def test_the_committed_transcript_is_what_the_demo_prints_now(fresh):
    committed = read(TRANSCRIPT)
    if fresh != committed:
        lines = difflib.unified_diff(committed.splitlines(), fresh.splitlines(), "committed", "now", lineterm="", n=1)
        pytest.fail("examples/transcript.txt is stale: run `python examples/run_demo.py --transcript examples/transcript.txt`, "
                    "read the diff, and update the README excerpts that changed.\n" + "\n".join(list(lines)[:40]))


def test_the_demo_shows_what_it_is_for(fresh):
    """The walkthrough reaches each thing it exists to show; a demo that stopped early would still exit 0."""
    for needle in (
        "the test split is locked until the final workflow step",           # a locked split
        "write sections 1–4 before running",                                # a card first
        "the `Gates passed?` line of section 5 is not filled in",           # the LLM-free gate before an answer run
        "section 5 says the gates did not pass",                            # a rejected card stays rejected
        "larger than noise",                                                # a difference measured against noise
        "within noise: do not read it as a difference",
        "skip the answer run: it cannot show a gain",                       # the headroom check
        "labkit.eval.paired",                                               # the noise rerun
    ):
        assert needle in fresh, needle


@pytest.mark.parametrize("readme, at_least", [(README, 4), (ROOT / "README.md", 3), (ROOT / "docs" / "README.ko.md", 3)])
def test_the_readmes_quote_the_transcript_only(fresh, readme, at_least):
    """The example's README explains the walkthrough; the repository's two READMEs quote a few lines of it to say what
    labkit is for. Output is not translated, so the Korean one quotes the same lines."""
    blocks = re.findall(r"^```transcript\n(.*?)^```", read(readme), re.MULTILINE | re.DOTALL)
    assert len(blocks) >= at_least, f"{readme.name} should quote the transcript where it explains it"
    for block in blocks:
        assert block.rstrip("\n") in fresh, f"a block of {readme.name} is not a piece of the transcript:\n" + block


@pytest.mark.parametrize("readme, link", [("README.md", "(examples/README.md)"), ("docs/README.ko.md", "(../examples/README.md)")])
def test_the_readmes_send_a_visitor_to_the_example(readme, link):
    """A visitor who wants to see labkit work, without an API key, is one click from the example."""
    assert link in (ROOT / readme).read_text(encoding="utf-8")


def test_the_example_is_not_a_second_copy_of_the_template():
    """The example holds what a project writes itself; the workflow files come from `labkit init`."""
    template = {p.relative_to(ROOT / "labkit" / "template").as_posix()
                for p in (ROOT / "labkit" / "template").rglob("*") if p.is_file()}
    mine = {p.relative_to(EXAMPLES / "toy-rag").as_posix() for p in (EXAMPLES / "toy-rag").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}
    copied = sorted(m for m in mine & template if read(EXAMPLES / "toy-rag" / m) == read(ROOT / "labkit" / "template" / m))
    assert not copied, f"unchanged copies of template files: {copied}"
